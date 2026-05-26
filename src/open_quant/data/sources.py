"""Data source adapters: Tushare Pro + AkShare + MockSource.

Each adapter exposes the same protocol returning polars DataFrames:
    .daily(symbols, start, end) -> OHLCV+amount+pre_close
    .adj_factor(symbols, start, end) -> symbol, trade_date, adj_factor
    .daily_basic(symbols, start, end) -> PE/PB/PS/turnover/etc.
    .trade_calendar(start, end) -> cal_date
    .stock_basic() -> symbol/name/list_date/industry/delist_date

Tushare-Pro column names are the canonical schema:
  ts_code  trade_date(YYYYMMDD)  open high low close pre_close vol amount
We normalize AkShare's Chinese column names to match.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

import polars as pl
from tenacity import retry, stop_after_attempt, wait_exponential

from open_quant.utils import get_logger

log = get_logger(__name__)


class DataSource(Protocol):
    name: str

    def daily(self, symbols: Iterable[str] | None, start: date, end: date) -> pl.DataFrame: ...
    def adj_factor(self, symbols: Iterable[str] | None, start: date, end: date) -> pl.DataFrame: ...
    def daily_basic(self, symbols: Iterable[str] | None, start: date, end: date) -> pl.DataFrame: ...
    def trade_calendar(self, start: date, end: date) -> pl.DataFrame: ...
    def stock_basic(self) -> pl.DataFrame: ...


# ---------------------------------------------------------------------------- #
# Tushare                                                                      #
# ---------------------------------------------------------------------------- #


@dataclass
class TushareSource:
    """Tushare Pro adapter. Requires sufficient 积分 (≥2000 for daily/basic)."""

    token: str
    rate_limit_per_minute: int = 500
    name: str = "tushare"

    def __post_init__(self):
        try:
            import tushare as ts  # noqa: F401
        except ImportError as e:
            raise RuntimeError("tushare not installed; run `pip install tushare`") from e
        import tushare as ts
        ts.set_token(self.token)
        self._pro = ts.pro_api()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60))
    def _call(self, api: str, **params) -> pl.DataFrame:
        log.debug(f"tushare.{api}({params})")
        fn = getattr(self._pro, api)
        df = fn(**params)
        return pl.from_pandas(df) if df is not None and len(df) else pl.DataFrame()

    @staticmethod
    def _fmt(d: date) -> str:
        return d.strftime("%Y%m%d")

    def daily(self, symbols, start, end) -> pl.DataFrame:
        if symbols is None:
            cal = self.trade_calendar(start, end)
            frames = [self._call("daily", trade_date=d) for d in cal["cal_date"]]
            return pl.concat(frames) if frames else pl.DataFrame()
        frames = [self._call("daily", ts_code=s, start_date=self._fmt(start),
                             end_date=self._fmt(end)) for s in symbols]
        return pl.concat([f for f in frames if not f.is_empty()]) if frames else pl.DataFrame()

    def adj_factor(self, symbols, start, end) -> pl.DataFrame:
        if symbols is None:
            cal = self.trade_calendar(start, end)
            frames = [self._call("adj_factor", trade_date=d) for d in cal["cal_date"]]
            return pl.concat(frames) if frames else pl.DataFrame()
        frames = [self._call("adj_factor", ts_code=s, start_date=self._fmt(start),
                             end_date=self._fmt(end)) for s in symbols]
        return pl.concat([f for f in frames if not f.is_empty()]) if frames else pl.DataFrame()

    def daily_basic(self, symbols, start, end) -> pl.DataFrame:
        if symbols is None:
            cal = self.trade_calendar(start, end)
            frames = [self._call("daily_basic", trade_date=d) for d in cal["cal_date"]]
            return pl.concat(frames) if frames else pl.DataFrame()
        frames = [self._call("daily_basic", ts_code=s, start_date=self._fmt(start),
                             end_date=self._fmt(end)) for s in symbols]
        return pl.concat([f for f in frames if not f.is_empty()]) if frames else pl.DataFrame()

    def trade_calendar(self, start, end) -> pl.DataFrame:
        return self._call("trade_cal", exchange="SSE", start_date=self._fmt(start),
                          end_date=self._fmt(end), is_open="1")

    def stock_basic(self) -> pl.DataFrame:
        return self._call("stock_basic", exchange="", list_status="L",
                          fields="ts_code,symbol,name,area,industry,list_date,delist_date,market")


# ---------------------------------------------------------------------------- #
# AkShare                                                                      #
# ---------------------------------------------------------------------------- #


@dataclass
class AkShareSource:
    """AkShare adapter — free, no token. Slower than Tushare but covers most data.

    AkShare returns forward-adjusted (qfq) prices directly. We store those as the
    canonical close and set adj_factor = 1.0. Price-limit detection that needs
    un-adjusted prices must be done from a separately-pulled raw daily dataset,
    or by upgrading to Tushare daily.
    """

    name: str = "akshare"
    rate_limit_per_minute: int = 200
    use_qfq: bool = True

    def __post_init__(self):
        try:
            import akshare  # noqa: F401
        except ImportError as e:
            raise RuntimeError("akshare not installed; run `pip install akshare`") from e

    @staticmethod
    def _to_ts_code(code: str) -> str:
        if "." in code:
            return code
        if code.startswith(("60", "68", "11", "13")):
            return f"{code}.SH"
        if code.startswith(("00", "30", "12", "15", "16")):
            return f"{code}.SZ"
        if code.startswith(("8", "43", "92")):
            return f"{code}.BJ"
        return f"{code}.SH"

    @staticmethod
    def _strip_code(ts_code: str) -> str:
        return ts_code.split(".")[0]

    @staticmethod
    def _fmt(d: date) -> str:
        return d.strftime("%Y%m%d")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _hist_one(self, code: str, start: date, end: date, adjust: str) -> pl.DataFrame:
        """Pull one stock's daily history via EastMoney (primary)."""
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=self._fmt(start), end_date=self._fmt(end),
            adjust=adjust,
        )
        if df is None or len(df) == 0:
            return pl.DataFrame()
        df = pl.from_pandas(df)
        rename = {
            "日期": "trade_date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low",
            "成交量": "vol", "成交额": "amount",
            "涨跌幅": "pct_chg", "换手率": "turnover_rate",
        }
        df = df.rename({k: v for k, v in rename.items() if k in df.columns})
        df = df.with_columns(
            pl.lit(self._to_ts_code(code)).alias("ts_code"),
            pl.col("trade_date").cast(pl.Utf8).str.replace_all("-", "").alias("trade_date"),
        )
        df = df.with_columns(pl.col("close").shift(1).alias("pre_close"))
        keep = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]
        return df.select([c for c in keep if c in df.columns])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _hist_one_sina(self, code: str, start: date, end: date, adjust: str) -> pl.DataFrame:
        """Pull one stock's daily history via Sina (fallback — less rate-limited)."""
        import akshare as ak
        ts_code = self._to_ts_code(code)
        suffix = ts_code.split(".")[1].lower()  # SH/SZ/BJ → sh/sz/bj
        sina_sym = f"{suffix}{code}"
        df = ak.stock_zh_a_daily(
            symbol=sina_sym,
            start_date=self._fmt(start), end_date=self._fmt(end),
            adjust=adjust,
        )
        if df is None or len(df) == 0:
            return pl.DataFrame()
        df = pl.from_pandas(df)
        # Sina columns: date open high low close volume amount outstanding_share turnover
        df = df.rename({"date": "trade_date", "volume": "vol"})
        df = df.with_columns(
            pl.lit(ts_code).alias("ts_code"),
            pl.col("trade_date").cast(pl.Utf8).str.replace_all("-", "").alias("trade_date"),
            pl.col("vol").cast(pl.Int64),         # match EastMoney schema
        ).with_columns(pl.col("close").shift(1).alias("pre_close"))
        keep = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]
        return df.select([c for c in keep if c in df.columns])

    def hist_one_any(self, code: str, start: date, end: date, adjust: str = "qfq") -> pl.DataFrame:
        """Try EastMoney then Sina. Returns empty DataFrame if both fail."""
        try:
            df = self._hist_one(code, start, end, adjust)
            if not df.is_empty():
                return df
        except Exception:
            pass
        try:
            return self._hist_one_sina(code, start, end, adjust)
        except Exception:
            return pl.DataFrame()

    def daily(self, symbols, start, end) -> pl.DataFrame:
        if symbols is None:
            sb = self.stock_basic()
            symbols = sb["ts_code"].to_list()
        symbols = list(symbols)
        log.info(f"akshare daily: {len(symbols)} symbols {start}..{end}")
        adjust = "qfq" if self.use_qfq else ""
        frames = []
        for i, ts_code in enumerate(symbols):
            code = self._strip_code(ts_code)
            try:
                df = self._hist_one(code, start, end, adjust=adjust)
                if not df.is_empty():
                    frames.append(df)
            except Exception as e:
                log.warning(f"akshare daily fail {ts_code}: {e}")
            if (i + 1) % 50 == 0:
                log.info(f"  progress: {i + 1}/{len(symbols)}")
        return pl.concat(frames) if frames else pl.DataFrame()

    def adj_factor(self, symbols, start, end) -> pl.DataFrame:
        """AkShare doesn't expose adj_factor directly — synthesized as 1.0."""
        if symbols is None:
            sb = self.stock_basic()
            symbols = sb["ts_code"].to_list()
        symbols = list(symbols)
        cal = self.trade_calendar(start, end)
        if cal.is_empty():
            return pl.DataFrame()
        dates = cal["cal_date"].to_list()
        rows = [{"ts_code": s, "trade_date": d, "adj_factor": 1.0}
                for s in symbols for d in dates]
        return pl.DataFrame(rows)

    def daily_basic(self, symbols, start, end) -> pl.DataFrame:
        """Today's PE/PB/MV snapshot broadcast across requested trade dates.

        Not historically accurate — for real research replace with per-stock
        `stock_a_indicator_lg` (slow) or Tushare daily_basic after upgrade.
        """
        try:
            import akshare as ak
            snap = ak.stock_zh_a_spot_em()
        except Exception as e:
            log.warning(f"akshare spot fail: {e}")
            return pl.DataFrame()
        if snap is None or len(snap) == 0:
            return pl.DataFrame()
        snap = pl.from_pandas(snap)
        rename = {
            "代码": "code", "名称": "name",
            "市盈率-动态": "pe_ttm", "市净率": "pb",
            "总市值": "total_mv", "流通市值": "circ_mv",
            "换手率": "turnover_rate",
        }
        snap = snap.rename({k: v for k, v in rename.items() if k in snap.columns})
        snap = snap.with_columns(
            pl.col("code").map_elements(self._to_ts_code, return_dtype=pl.Utf8).alias("ts_code")
        )
        cal = self.trade_calendar(start, end)
        if cal.is_empty():
            return pl.DataFrame()
        dates = cal["cal_date"].to_list()
        keep = ["ts_code", "pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate"]
        snap = snap.select([c for c in keep if c in snap.columns])
        if symbols is not None:
            snap = snap.filter(pl.col("ts_code").is_in(list(symbols)))
        out = [snap.with_columns(pl.lit(d).alias("trade_date")) for d in dates]
        return pl.concat(out) if out else pl.DataFrame()

    def trade_calendar(self, start, end) -> pl.DataFrame:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is None or len(df) == 0:
            return pl.DataFrame()
        df = pl.from_pandas(df)
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        df = df.with_columns(pl.col(col).cast(pl.Utf8).str.replace_all("-", "").alias("cal_date"))
        return df.filter(
            (pl.col("cal_date") >= self._fmt(start)) & (pl.col("cal_date") <= self._fmt(end))
        ).select("cal_date")

    def stock_basic(self) -> pl.DataFrame:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is None or len(df) == 0:
            return pl.DataFrame()
        df = pl.from_pandas(df).rename({"code": "_code", "name": "name"})
        df = df.with_columns(
            pl.col("_code").map_elements(self._to_ts_code, return_dtype=pl.Utf8).alias("ts_code"),
            pl.col("_code").alias("symbol"),
        )
        return df.with_columns(
            pl.lit("").alias("area"),
            pl.lit("").alias("industry"),
            pl.lit("19900101").alias("list_date"),
            pl.lit(None, dtype=pl.Utf8).alias("delist_date"),
            pl.lit("主板").alias("market"),
        ).select(["ts_code", "symbol", "name", "area", "industry", "list_date", "delist_date", "market"])

    # -- alt-data --------------------------------------------------------------

    def lhb(self, trade_date: date) -> pl.DataFrame:
        import akshare as ak
        df = ak.stock_lhb_detail_em(start_date=self._fmt(trade_date), end_date=self._fmt(trade_date))
        return pl.from_pandas(df) if df is not None and len(df) else pl.DataFrame()

    def hsgt_north_flow(self) -> pl.DataFrame:
        import akshare as ak
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        return pl.from_pandas(df) if df is not None and len(df) else pl.DataFrame()


# ---------------------------------------------------------------------------- #
# Mock                                                                         #
# ---------------------------------------------------------------------------- #


class MockSource:
    """Synthetic data source for tests and offline development."""

    name = "mock"

    def __init__(self, symbols: list[str], seed: int = 42):
        self.symbols = symbols
        self.seed = seed

    def daily(self, symbols, start, end) -> pl.DataFrame:
        import numpy as np
        from open_quant.data.calendar import get_calendar
        rng = np.random.default_rng(self.seed)
        cal = get_calendar()
        dates = cal.range(start, end)
        if not dates:
            return pl.DataFrame()
        syms = list(symbols) if symbols else self.symbols
        rows = []
        for sym in syms:
            base = 10.0 + (hash(sym) % 50)
            ret = rng.normal(0.0003, 0.02, len(dates))
            close = base * np.cumprod(1 + ret)
            prev = np.concatenate([[base], close[:-1]])
            high = np.maximum(close, prev) * (1 + np.abs(rng.normal(0, 0.005, len(dates))))
            low = np.minimum(close, prev) * (1 - np.abs(rng.normal(0, 0.005, len(dates))))
            open_ = prev + (close - prev) * rng.uniform(0.2, 0.8, len(dates))
            vol = rng.integers(1_000_000, 50_000_000, len(dates))
            amount = vol * (high + low) / 2
            for i, d in enumerate(dates):
                rows.append({
                    "ts_code": sym, "trade_date": d.strftime("%Y%m%d"),
                    "open": round(float(open_[i]), 2),
                    "high": round(float(high[i]), 2),
                    "low": round(float(low[i]), 2),
                    "close": round(float(close[i]), 2),
                    "pre_close": round(float(prev[i]), 2),
                    "vol": int(vol[i]), "amount": float(amount[i]),
                })
        return pl.DataFrame(rows)

    def adj_factor(self, symbols, start, end) -> pl.DataFrame:
        from open_quant.data.calendar import get_calendar
        cal = get_calendar()
        dates = cal.range(start, end)
        syms = list(symbols) if symbols else self.symbols
        rows = [{"ts_code": s, "trade_date": d.strftime("%Y%m%d"), "adj_factor": 1.0}
                for s in syms for d in dates]
        return pl.DataFrame(rows)

    def daily_basic(self, symbols, start, end) -> pl.DataFrame:
        import numpy as np
        from open_quant.data.calendar import get_calendar
        rng = np.random.default_rng(self.seed + 1)
        cal = get_calendar()
        dates = cal.range(start, end)
        syms = list(symbols) if symbols else self.symbols
        rows = []
        for s in syms:
            for d in dates:
                rows.append({
                    "ts_code": s, "trade_date": d.strftime("%Y%m%d"),
                    "pe_ttm": float(rng.uniform(8, 80)),
                    "pb": float(rng.uniform(0.5, 8)),
                    "ps_ttm": float(rng.uniform(0.5, 20)),
                    "turnover_rate": float(rng.uniform(0.2, 10)),
                    "total_mv": float(rng.uniform(5e9, 5e11)),
                    "circ_mv": float(rng.uniform(2e9, 2e11)),
                })
        return pl.DataFrame(rows)

    def trade_calendar(self, start, end) -> pl.DataFrame:
        from open_quant.data.calendar import get_calendar
        cal = get_calendar()
        return pl.DataFrame({"cal_date": [d.strftime("%Y%m%d") for d in cal.range(start, end)]})

    def stock_basic(self) -> pl.DataFrame:
        rows = []
        for s in self.symbols:
            rows.append({
                "ts_code": s, "symbol": s.split(".")[0],
                "name": f"MOCK{s[:6]}", "area": "上海", "industry": "测试",
                "list_date": "20100101", "delist_date": None, "market": "主板",
            })
        return pl.DataFrame(rows)
