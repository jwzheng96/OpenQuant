"""A-share data toolkit — provider-agnostic interface used by LLM agents.

Implementations:
  AkShareToolkit   — free, no token, slower; uses stock_news_em + akshare 财务接口
  TushareToolkit   — uses Tushare news (需 5000+ 积分) + income/balancesheet/cashflow
                     (these are openable on current 积分 level so partial work)
  HybridToolkit    — tries Tushare first per-method, falls back to AkShare on
                     missing-permission errors. Auto-degrades gracefully.

Each method returns structured dataclasses (NewsItem, FundamentalSnapshot, etc.)
that prompts.py formats into LLM context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol


# ---------------------------------------------------------------------------- #
# Data containers                                                              #
# ---------------------------------------------------------------------------- #


@dataclass
class NewsItem:
    timestamp: str            # ISO format, may be date-only if source granularity is coarse
    title: str
    summary: str
    source: str               # "akshare_em" | "tushare_news" | "caixin" | ...
    url: str | None = None


@dataclass
class FundamentalSnapshot:
    ts_code: str
    as_of: date
    name: str | None = None
    industry: str | None = None
    # Valuation
    pe_ttm: float | None = None
    pb: float | None = None
    ps_ttm: float | None = None
    total_mv: float | None = None
    # Profitability (from income statement)
    revenue: float | None = None       # 万元
    net_profit: float | None = None    # 万元
    revenue_growth_yoy: float | None = None     # %
    profit_growth_yoy: float | None = None      # %
    roe_ttm: float | None = None       # %
    gross_margin: float | None = None  # %
    # Balance sheet
    total_assets: float | None = None
    total_debt: float | None = None
    debt_ratio: float | None = None    # %
    # Raw data for LLM context, in case agent wants to drill deeper
    raw: dict = field(default_factory=dict)


@dataclass
class SentimentSnapshot:
    ts_code: str
    as_of: date
    bullish_pct: float | None = None   # 0-1
    bearish_pct: float | None = None
    n_posts: int = 0
    top_topics: list[str] = field(default_factory=list)
    raw_summary: str = ""


@dataclass
class TechnicalSnapshot:
    ts_code: str
    as_of: date
    close: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    above_ma20: bool | None = None
    above_ma60: bool | None = None
    factor_values: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------- #
# Protocol — every toolkit conforms to this                                    #
# ---------------------------------------------------------------------------- #


class Toolkit(Protocol):
    name: str

    def get_news(self, ts_code: str, *, days: int = 7, limit: int = 8) -> list[NewsItem]: ...

    def get_fundamentals(self, ts_code: str, *, as_of: date | None = None) -> FundamentalSnapshot: ...

    def get_sentiment(self, ts_code: str, *, days: int = 7) -> SentimentSnapshot | None: ...

    def get_technical(self, ts_code: str, *, as_of: date) -> TechnicalSnapshot: ...


# ---------------------------------------------------------------------------- #
# AkShare implementation (free, default fallback)                              #
# ---------------------------------------------------------------------------- #


def _code_part(ts_code: str) -> str:
    """600519.SH → 600519"""
    return ts_code.split(".")[0]


class AkShareToolkit:
    """Free no-token toolkit. Slower than Tushare, sometimes rate-limited."""

    name = "akshare"

    # -- news ----------------------------------------------------------------

    def get_news(self, ts_code: str, *, days: int = 7, limit: int = 8) -> list[NewsItem]:
        """Multi-source news fetch — combines 3 AkShare endpoints:
          1. 财联社全球资讯  (stock_info_global_cls)  → 实时市场快讯，含完整内容
          2. 财新主新闻      (stock_news_main_cx)     → 主流财经
          3. 巨潮个股公告    (stock_zh_a_disclosure_report_cninfo) → 官方披露

        Market-wide sources (1, 2) are cached for 1 hour and filtered by symbol
        code AND company name. Per-stock source (3) is fetched on demand.
        """
        code = _code_part(ts_code)
        name = _get_stock_name(ts_code)
        cutoff = datetime.now() - timedelta(days=days)
        items: list[NewsItem] = []

        # Source 1: 财联社 market-wide
        items += _fetch_cls_filtered(code, name, cutoff)
        # Source 2: 财新主新闻
        items += _fetch_caixin_filtered(code, name, cutoff)
        # Source 3: 公告
        items += _fetch_cninfo_notices(ts_code, code, cutoff, days)

        # Dedupe by title (first 60 chars) + sort by timestamp desc
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for it in items:
            key = it.title[:60]
            if key in seen or not it.title:
                continue
            seen.add(key)
            unique.append(it)
        unique.sort(key=lambda x: x.timestamp, reverse=True)
        return unique[:limit]

    # -- fundamentals --------------------------------------------------------

    def get_fundamentals(self, ts_code: str, *, as_of: date | None = None) -> FundamentalSnapshot:
        """Stitches local store + live AkShare:
        - LOCAL stock_basic_info cache → name, industry  (1-day TTL)
        - LOCAL daily_basic parquet → PE/PB/MV (no live call needed)
        - LIVE stock_individual_info_em → fallback for name when cache miss
        - LIVE stock_financial_abstract → 历史财务（按季度）
        Designed to degrade gracefully when EastMoney is rate-limiting.
        """
        import akshare as ak
        code = _code_part(ts_code)
        snap = FundamentalSnapshot(ts_code=ts_code, as_of=as_of or date.today())

        # 1) Try local info cache first (name + industry, much more stable)
        cached_info = _info_cache_get(code)
        if cached_info:
            snap.name = cached_info.get("name") or ""
            snap.industry = cached_info.get("industry") or ""
            mv = _to_float(cached_info.get("total_mv"))
            if mv is not None:
                snap.total_mv = mv
            snap.raw["info_source"] = "cache"

        # 2) PE/PB/MV from local daily_basic parquet — fast + no throttle
        try:
            from open_quant.data.api import get_data_api
            api = get_data_api()
            res = api.query.con.execute(
                "SELECT pe_ttm, pb, total_mv FROM daily_basic "
                "WHERE symbol = ? AND trade_date <= ? "
                "ORDER BY trade_date DESC LIMIT 1",
                [ts_code, snap.as_of],
            ).pl()
            if not res.is_empty():
                if res["pe_ttm"][0] is not None:
                    snap.pe_ttm = float(res["pe_ttm"][0])
                if res["pb"][0] is not None:
                    snap.pb = float(res["pb"][0])
                if res["total_mv"][0] is not None and snap.total_mv is None:
                    snap.total_mv = float(res["total_mv"][0])
                snap.raw["valuation_source"] = "local_daily_basic"
        except Exception as e:
            snap.raw["local_basic_error"] = str(e)[:80]

        # 3) Live individual_info_em — only if cache missed AND we still need name
        if not snap.name:
            try:
                info = ak.stock_individual_info_em(symbol=code)
                if info is not None and len(info) > 0:
                    kv = dict(zip(info["item"], info["value"]))
                    snap.name = str(kv.get("股票简称", "") or "")
                    snap.industry = str(kv.get("行业", "") or "")
                    if snap.total_mv is None:
                        snap.total_mv = _to_float(kv.get("总市值"))
                    snap.raw["list_date"] = str(kv.get("上市时间", ""))
                    snap.raw["info_source"] = "live_em"
                    # cache it
                    _info_cache_put(code, {
                        "name": snap.name, "industry": snap.industry,
                        "total_mv": snap.total_mv,
                    })
            except Exception as e:
                snap.raw["info_error"] = str(e)[:80]

        # 3) Financial abstract — 历史季度财务，正确处理列结构
        # df.columns = ['选项', '指标', '20260331', '20251231', '20250930', ...]
        try:
            df = ak.stock_financial_abstract(symbol=code)
            if df is not None and len(df) > 0:
                # Find date columns (8-digit YYYYMMDD)
                date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
                if date_cols:
                    latest_col = date_cols[0]   # 最近季度（columns 已按时间倒序）
                    latest = dict(zip(df["指标"], df[latest_col]))
                    snap.revenue = _to_float(latest.get("营业总收入"))
                    snap.net_profit = _to_float(latest.get("归母净利润"))
                    snap.roe_ttm = _to_float(latest.get("净资产收益率(ROE)"))
                    snap.gross_margin = _to_float(latest.get("毛利率"))
                    snap.raw["latest_period"] = str(latest_col)

                    # YoY growth: 比较 4 季度前的同期 (e.g. 20260331 vs 20250331)
                    if len(date_cols) >= 5:
                        yoy_col = date_cols[4]  # 4 季度前
                        yoy = dict(zip(df["指标"], df[yoy_col]))
                        rev_yoy = _to_float(yoy.get("营业总收入"))
                        np_yoy = _to_float(yoy.get("归母净利润"))
                        if rev_yoy and snap.revenue:
                            snap.revenue_growth_yoy = (snap.revenue / rev_yoy - 1) * 100
                        if np_yoy and snap.net_profit:
                            snap.profit_growth_yoy = (snap.net_profit / np_yoy - 1) * 100
                        snap.raw["yoy_period"] = str(yoy_col)

                    # Debt ratio from balance sheet if available
                    # 总资产 / 股东权益 → infer 资产负债率
                    equity = _to_float(latest.get("股东权益合计(净资产)"))
                    if equity and snap.total_mv:
                        # crude proxy — not exact but useful signal
                        snap.raw["equity"] = equity
        except Exception as e:
            snap.raw["financial_error"] = str(e)[:80]

        return snap

    # -- sentiment (stub for stage 1 — scraping is brittle) ------------------

    def get_sentiment(self, ts_code: str, *, days: int = 7) -> SentimentSnapshot | None:
        return None  # disabled for now; news already covers sentiment signal somewhat

    # -- technical (uses our own factor engine + store) ----------------------

    def get_technical(self, ts_code: str, *, as_of: date) -> TechnicalSnapshot:
        from open_quant.data.api import get_data_api
        api = get_data_api()
        panel = api.get_daily([ts_code], as_of - timedelta(days=120), as_of, adjust="fwd")
        if panel.is_empty():
            return TechnicalSnapshot(ts_code=ts_code, as_of=as_of)
        latest = panel.tail(1)
        close = float(latest["close"][0])
        snap = TechnicalSnapshot(ts_code=ts_code, as_of=as_of, close=close)
        closes = panel["close"].to_list()
        if len(closes) >= 6:
            snap.return_5d = close / closes[-6] - 1
        if len(closes) >= 21:
            snap.return_20d = close / closes[-21] - 1
            snap.above_ma20 = close > sum(closes[-21:-1]) / 20
        if len(closes) >= 61:
            snap.return_60d = close / closes[-61] - 1
            snap.above_ma60 = close > sum(closes[-61:-1]) / 60

        # Snapshot key factors (without recomputing — query stored ml_lgb if present)
        snap.factor_values = _query_factor_snapshot(ts_code, as_of)
        return snap


# ---------------------------------------------------------------------------- #
# Tushare implementation                                                       #
# ---------------------------------------------------------------------------- #


class TushareToolkit:
    """Tushare-backed toolkit. Falls back to AkShare on missing-permission errors."""

    name = "tushare"

    def __init__(self, token: str | None = None):
        from open_quant.utils import load_settings
        if not token:
            token = load_settings().data_sources.tushare.token
        if not token or token == "REPLACE_WITH_YOUR_TOKEN":
            raise RuntimeError("Tushare token missing; configure in data_sources.yaml")
        import tushare as ts
        ts.set_token(token)
        self._pro = ts.pro_api()
        self._fallback = AkShareToolkit()

    def _call(self, api: str, **params):
        try:
            return getattr(self._pro, api)(**params)
        except Exception as e:
            msg = str(e)
            # Treat all "soft" failures as PermissionError so callers can fallback
            soft = ("权限" in msg or "permission" in msg.lower()
                    or "token不对" in msg or "积分" in msg
                    or "频率" in msg or "rate" in msg.lower())
            if soft:
                raise PermissionError(f"tushare {api}: {msg[:80]}") from e
            raise

    def get_news(self, ts_code: str, *, days: int = 7, limit: int = 8) -> list[NewsItem]:
        """Tushare news API requires 5000+ 积分 — may raise PermissionError."""
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            df = self._call("news", src="sina",
                            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                            end_date=end.strftime("%Y-%m-%d %H:%M:%S"))
            if df is None or len(df) == 0:
                return self._fallback.get_news(ts_code, days=days, limit=limit)
            # Filter by ts_code in content (Tushare news isn't per-stock; need keyword match)
            code = _code_part(ts_code)
            df = df[df["content"].str.contains(code, na=False) |
                    df["title"].str.contains(code, na=False)]
            items = []
            for _, row in df.head(limit).iterrows():
                items.append(NewsItem(
                    timestamp=str(row.get("datetime", "")),
                    title=str(row.get("title", "")),
                    summary=str(row.get("content", ""))[:300],
                    source="tushare_news",
                ))
            return items or self._fallback.get_news(ts_code, days=days, limit=limit)
        except PermissionError:
            return self._fallback.get_news(ts_code, days=days, limit=limit)

    def get_fundamentals(self, ts_code: str, *, as_of: date | None = None) -> FundamentalSnapshot:
        snap = FundamentalSnapshot(ts_code=ts_code, as_of=as_of or date.today())
        # 1) income statement — current 积分 should allow this (user verified earlier)
        try:
            income = self._call("income", ts_code=ts_code,
                                start_date="20230101",
                                end_date=(as_of or date.today()).strftime("%Y%m%d"))
            if income is not None and len(income) >= 2:
                latest = income.iloc[0]
                snap.revenue = float(latest.get("total_revenue") or 0) / 1e4
                snap.net_profit = float(latest.get("n_income_attr_p") or 0) / 1e4
                # YoY: compare to 4 quarters earlier (similar period)
                if len(income) >= 5:
                    yoy = income.iloc[4]
                    rev_yoy = float(yoy.get("total_revenue") or 1)
                    np_yoy = float(yoy.get("n_income_attr_p") or 1)
                    snap.revenue_growth_yoy = (snap.revenue * 1e4 / rev_yoy - 1) * 100 if rev_yoy else None
                    snap.profit_growth_yoy = (snap.net_profit * 1e4 / np_yoy - 1) * 100 if np_yoy else None
                snap.raw["income_period"] = str(latest.get("end_date", ""))
        except PermissionError:
            snap.raw["income_error"] = "no_permission"
        except Exception as e:
            snap.raw["income_error"] = str(e)[:80]

        # 2) daily_basic for PE/PB/MV
        try:
            db = self._call("daily_basic", ts_code=ts_code,
                            trade_date=(as_of or date.today()).strftime("%Y%m%d"))
            if db is not None and len(db) > 0:
                r = db.iloc[0]
                snap.pe_ttm = float(r.get("pe_ttm") or float("nan"))
                snap.pb = float(r.get("pb") or float("nan"))
                snap.ps_ttm = float(r.get("ps_ttm") or float("nan"))
                snap.total_mv = float(r.get("total_mv") or float("nan"))
        except PermissionError:
            # Fall back to AkShare spot
            ak_snap = self._fallback.get_fundamentals(ts_code, as_of=as_of)
            snap.pe_ttm = snap.pe_ttm or ak_snap.pe_ttm
            snap.pb = snap.pb or ak_snap.pb
            snap.total_mv = snap.total_mv or ak_snap.total_mv
            snap.name = snap.name or ak_snap.name
        except Exception as e:
            snap.raw["daily_basic_error"] = str(e)[:80]

        return snap

    def get_sentiment(self, ts_code: str, *, days: int = 7) -> SentimentSnapshot | None:
        return None

    def get_technical(self, ts_code: str, *, as_of: date) -> TechnicalSnapshot:
        return self._fallback.get_technical(ts_code, as_of=as_of)


# ---------------------------------------------------------------------------- #
# Hybrid toolkit (smart fallback)                                              #
# ---------------------------------------------------------------------------- #


class HybridToolkit:
    """Try Tushare first per-method, fall back to AkShare gracefully.

    Each method independently tries primary → fallback. Ideal for users with
    partial Tushare permissions (e.g. income API yes, news API no).
    """

    name = "hybrid"

    def __init__(self):
        self._ak = AkShareToolkit()
        try:
            self._ts: TushareToolkit | None = TushareToolkit()
        except Exception:
            self._ts = None

    def get_news(self, ts_code: str, *, days: int = 7, limit: int = 8) -> list[NewsItem]:
        if self._ts:
            try:
                return self._ts.get_news(ts_code, days=days, limit=limit)
            except Exception:
                pass
        return self._ak.get_news(ts_code, days=days, limit=limit)

    def get_fundamentals(self, ts_code: str, *, as_of: date | None = None) -> FundamentalSnapshot:
        # Merge: prefer Tushare income (more accurate) + AkShare spot (always available)
        ak_snap = self._ak.get_fundamentals(ts_code, as_of=as_of)
        if self._ts is None:
            return ak_snap
        try:
            ts_snap = self._ts.get_fundamentals(ts_code, as_of=as_of)
            # Field-by-field merge: prefer Tushare where present, AkShare elsewhere
            for fld in ("revenue", "net_profit", "revenue_growth_yoy", "profit_growth_yoy", "roe_ttm"):
                if getattr(ts_snap, fld) is not None:
                    setattr(ak_snap, fld, getattr(ts_snap, fld))
            for fld in ("pe_ttm", "pb", "ps_ttm", "total_mv"):
                v = getattr(ts_snap, fld)
                if v is not None and not (isinstance(v, float) and v != v):  # not NaN
                    setattr(ak_snap, fld, v)
            ak_snap.raw.update(ts_snap.raw)
            return ak_snap
        except Exception:
            return ak_snap

    def get_sentiment(self, ts_code: str, *, days: int = 7) -> SentimentSnapshot | None:
        return self._ak.get_sentiment(ts_code, days=days)

    def get_technical(self, ts_code: str, *, as_of: date) -> TechnicalSnapshot:
        return self._ak.get_technical(ts_code, as_of=as_of)


# ---------------------------------------------------------------------------- #
# Helpers                                                                      #
# ---------------------------------------------------------------------------- #


# ---------------------------------------------------------------------------- #
# Market-wide news caches (CLS / 财新) — fetched once, filtered per stock.      #
# ---------------------------------------------------------------------------- #

_NEWS_CACHE_DIR = "data/agents_cache/_market_news"
_NEWS_CACHE_TTL_MIN = 60   # refresh every hour during live runs


def _market_cache_get(source: str) -> list[dict] | None:
    """Return cached list of dicts for a market-wide news source, if fresh."""
    import json as _json
    from pathlib import Path as _P
    from datetime import datetime as _dt, timedelta as _td
    p = _P(_NEWS_CACHE_DIR) / f"{source}.json"
    if not p.exists():
        return None
    try:
        data = _json.loads(p.read_text())
        cached_at = _dt.fromisoformat(data["_cached_at"])
        if _dt.now() - cached_at > _td(minutes=_NEWS_CACHE_TTL_MIN):
            return None
        return data.get("rows") or []
    except Exception:
        return None


def _market_cache_put(source: str, rows: list[dict]) -> None:
    import json as _json
    from pathlib import Path as _P
    from datetime import datetime as _dt
    p = _P(_NEWS_CACHE_DIR) / f"{source}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps({
        "_cached_at": _dt.now().isoformat(timespec="seconds"),
        "rows": rows,
    }, ensure_ascii=False))


def _get_stock_name(ts_code: str) -> str:
    """Look up company name from the stock_info cache."""
    info = _info_cache_get(_code_part(ts_code))
    if info:
        return info.get("name") or ""
    return ""


def _matches_stock(text: str, code: str, name: str) -> bool:
    """Heuristic: does this news item mention the target stock?"""
    if not text:
        return False
    if code and code in text:
        return True
    if name and len(name) >= 2 and name in text:
        return True
    return False


def _fetch_cls_filtered(code: str, name: str, cutoff: datetime) -> list[NewsItem]:
    """Fetch 财联社 market-wide news + filter by stock code/name."""
    cached = _market_cache_get("cls")
    if cached is None:
        try:
            import akshare as ak
            df = ak.stock_info_global_cls()
            if df is None or len(df) == 0:
                _market_cache_put("cls", [])
                return []
            cached = []
            for _, row in df.iterrows():
                pub_date = str(row.get("发布日期", ""))
                pub_time = str(row.get("发布时间", ""))
                ts = f"{pub_date}T{pub_time}" if pub_date and pub_time else datetime.now().isoformat()
                cached.append({
                    "ts": ts,
                    "title": str(row.get("标题", "")).strip(),
                    "content": str(row.get("内容", "")).strip(),
                })
            _market_cache_put("cls", cached)
        except Exception:
            return []

    out: list[NewsItem] = []
    for r in cached:
        try:
            ts_dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        if ts_dt < cutoff:
            continue
        text = r["title"] + " " + r["content"][:200]
        if not _matches_stock(text, code, name):
            continue
        summary = r["content"][:300] + ("…" if len(r["content"]) > 300 else "")
        out.append(NewsItem(
            timestamp=ts_dt.isoformat(timespec="seconds"),
            title=r["title"], summary=summary, source="cls",
        ))
    return out


def _fetch_caixin_filtered(code: str, name: str, cutoff: datetime) -> list[NewsItem]:
    """Fetch 财新 main news + filter by stock code/name."""
    cached = _market_cache_get("caixin")
    if cached is None:
        try:
            import akshare as ak
            df = ak.stock_news_main_cx()
            if df is None or len(df) == 0:
                _market_cache_put("caixin", [])
                return []
            cached = []
            for _, row in df.iterrows():
                cached.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),  # 财新没明确时间，用 fetch 时间
                    "tag": str(row.get("tag", "")),
                    "summary": str(row.get("summary", "")).strip(),
                    "url": str(row.get("url", "")),
                })
            _market_cache_put("caixin", cached)
        except Exception:
            return []

    out: list[NewsItem] = []
    for r in cached:
        text = r["tag"] + " " + r["summary"]
        if not _matches_stock(text, code, name):
            continue
        # 财新 returns 'tag' as summary-style title + summary as body
        out.append(NewsItem(
            timestamp=r["ts"], title=r["tag"][:80],
            summary=r["summary"][:300], source="caixin",
            url=r.get("url") or None,
        ))
    return out


def _fetch_cninfo_notices(ts_code: str, code: str, cutoff: datetime, days: int) -> list[NewsItem]:
    """Fetch per-stock official 公告 from 巨潮 (cninfo)."""
    try:
        import akshare as ak
        start = (cutoff).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京",
            start_date=start, end_date=end,
        )
        if df is None or len(df) == 0:
            return []
        out: list[NewsItem] = []
        for _, row in df.head(20).iterrows():
            ann_time = str(row.get("公告时间", ""))
            try:
                # cninfo time format: YYYY-MM-DD HH:MM
                ts_dt = datetime.fromisoformat(ann_time.replace(" ", "T"))
            except Exception:
                ts_dt = datetime.now()
            if ts_dt < cutoff:
                continue
            title = str(row.get("公告标题", "")).strip()
            url = str(row.get("公告链接", "")) or None
            out.append(NewsItem(
                timestamp=ts_dt.isoformat(timespec="seconds"),
                title=title, summary="（官方公告）",
                source="cninfo_notice", url=url,
            ))
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------- #
# Local cache for stock metadata (name/industry/MV) — survives between runs    #
# ---------------------------------------------------------------------------- #

_INFO_CACHE_PATH = "data/agents_cache/_stock_info.json"
_INFO_CACHE_TTL_DAYS = 30   # name/industry rarely change — 1 month is fine


def _info_cache_get(code: str) -> dict | None:
    import json as _json
    from pathlib import Path as _P
    from datetime import datetime as _dt, timedelta as _td
    p = _P(_INFO_CACHE_PATH)
    if not p.exists():
        return None
    try:
        data = _json.loads(p.read_text())
        entry = data.get(code)
        if not entry:
            return None
        cached_at = _dt.fromisoformat(entry.get("_cached_at", "2000-01-01"))
        if _dt.now() - cached_at > _td(days=_INFO_CACHE_TTL_DAYS):
            return None
        return entry
    except Exception:
        return None


def _info_cache_put(code: str, info: dict) -> None:
    import json as _json
    from pathlib import Path as _P
    from datetime import datetime as _dt
    p = _P(_INFO_CACHE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = _json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        data = {}
    info["_cached_at"] = _dt.now().isoformat(timespec="seconds")
    data[code] = info
    p.write_text(_json.dumps(data, ensure_ascii=False, indent=2))


def _to_float(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except (ValueError, TypeError):
        return None


def _query_factor_snapshot(ts_code: str, as_of: date) -> dict[str, float]:
    """Query last available value of key factors from the store for a symbol."""
    try:
        from open_quant.data.api import get_data_api
        api = get_data_api()
        rows = api.query.con.execute(
            "SELECT value FROM read_parquet('data/parquet/factors/name=ml_lgb_strict/data.parquet') "
            "WHERE symbol = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
            [ts_code, as_of],
        ).pl()
        result: dict[str, float] = {}
        if not rows.is_empty():
            result["ml_lgb_strict"] = float(rows["value"][0])
        return result
    except Exception:
        return {}
