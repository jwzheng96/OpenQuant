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
        import akshare as ak
        code = _code_part(ts_code)
        try:
            df = ak.stock_news_em(symbol=code)
        except Exception as e:
            return [NewsItem(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                title=f"[FETCH FAILED] {e}", summary="",
                source="akshare_em_failed",
            )]
        if df is None or len(df) == 0:
            return []

        # AkShare columns: 关键词, 新闻标题, 新闻内容, 发布时间, 文章来源, 新闻链接
        cutoff = datetime.now() - timedelta(days=days)
        items: list[NewsItem] = []
        for _, row in df.head(limit * 3).iterrows():    # over-fetch then filter
            ts_raw = row.get("发布时间", "")
            try:
                ts_dt = datetime.fromisoformat(str(ts_raw).replace(" ", "T"))
            except Exception:
                ts_dt = datetime.now()
            if ts_dt < cutoff:
                continue
            title = str(row.get("新闻标题", "")).strip()
            content = str(row.get("新闻内容", "")).strip()
            # Truncate long content
            summary = content[:300] + ("…" if len(content) > 300 else "")
            items.append(NewsItem(
                timestamp=ts_dt.isoformat(timespec="seconds"),
                title=title, summary=summary,
                source="akshare_em",
                url=str(row.get("新闻链接", "")) or None,
            ))
            if len(items) >= limit:
                break
        return items

    # -- fundamentals --------------------------------------------------------

    def get_fundamentals(self, ts_code: str, *, as_of: date | None = None) -> FundamentalSnapshot:
        """Single quarter snapshot via stock_zh_a_spot_em + 财务摘要 endpoints."""
        import akshare as ak
        code = _code_part(ts_code)
        snap = FundamentalSnapshot(ts_code=ts_code, as_of=as_of or date.today())

        # Spot quote — has PE/PB/MV
        try:
            spot = ak.stock_zh_a_spot_em()
            row = spot[spot["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                snap.name = str(r.get("名称", "") or "")
                snap.pe_ttm = float(r.get("市盈率-动态", float("nan")))
                snap.pb = float(r.get("市净率", float("nan")))
                snap.total_mv = float(r.get("总市值", float("nan")))
                snap.raw["spot_change_pct"] = float(r.get("涨跌幅", 0))
        except Exception as e:
            snap.raw["spot_error"] = str(e)[:80]

        # Financial summary — 财务报表 (income / balance sheet)
        try:
            df = ak.stock_financial_abstract(symbol=code)
            if df is not None and len(df) > 0:
                # Latest period (most recent column)
                latest_col = df.columns[1]   # 第一个是指标名
                indicators = dict(zip(df["指标"], df[latest_col]))
                snap.revenue = _to_float(indicators.get("营业总收入"))
                snap.net_profit = _to_float(indicators.get("归母净利润"))
                snap.roe_ttm = _to_float(indicators.get("净资产收益率"))
                snap.gross_margin = _to_float(indicators.get("销售毛利率"))
                # Year-over-year growth if available
                snap.revenue_growth_yoy = _to_float(indicators.get("营业总收入滚动环比增长"))
                snap.profit_growth_yoy = _to_float(indicators.get("归属净利润滚动环比增长"))
                snap.raw["latest_period"] = str(latest_col)
        except Exception as e:
            snap.raw["financial_error"] = str(e)[:80]

        return snap

    # -- sentiment (stub for stage 1 — scraping is brittle) ------------------

    def get_sentiment(self, ts_code: str, *, days: int = 7) -> SentimentSnapshot | None:
        return None  # disabled for now; news already covers sentiment signal somewhat

    # -- technical (uses our own factor engine + store) ----------------------

    def get_technical(self, ts_code: str, *, as_of: date) -> TechnicalSnapshot:
        from uni_quant.data.api import get_data_api
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
        from uni_quant.utils import load_settings
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
            if "权限" in str(e) or "permission" in str(e).lower():
                # Caller decides whether to fallback
                raise PermissionError(f"tushare {api} no permission") from e
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
        from uni_quant.data.api import get_data_api
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
