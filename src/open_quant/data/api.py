"""High-level data API.

This is the single import users (and other modules) should reach for. It hides
storage details: callers ask for "daily prices, forward adjusted, for these
symbols, in this date range" and get a polars DataFrame.

    from open_quant.data import get_data_api
    api = get_data_api()
    df = api.get_daily(["600519.SH"], "2020-01-01", "2024-12-31", adjust="fwd")
"""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import polars as pl

from open_quant.data.adjust import AdjustMode, adjust_ohlcv
from open_quant.data.store import DuckDBQuery, ParquetStore
from open_quant.data.sources import DataSource, MockSource, TushareSource
from open_quant.utils import get_logger, load_settings

log = get_logger(__name__)


def _to_date(d: date | str) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


class DataAPI:
    """Read-side data facade with optional write methods for sync flows."""

    def __init__(
        self,
        store: ParquetStore,
        query: DuckDBQuery,
        source: DataSource | None = None,
    ):
        self.store = store
        self.query = query
        self.source = source

    # -- read --------------------------------------------------------------------

    def get_daily(
        self,
        symbols: Iterable[str] | None,
        start: date | str,
        end: date | str,
        *,
        adjust: AdjustMode = "fwd",
        include_basic: bool = True,
    ) -> pl.DataFrame:
        df = self.query.daily(symbols, _to_date(start), _to_date(end))
        if df.is_empty():
            return df

        s_d, e_d = _to_date(start), _to_date(end)
        # adj_factor join
        if adjust != "raw":
            try:
                af = self.query.con.execute(
                    "SELECT symbol, trade_date, adj_factor FROM adj_factor "
                    "WHERE trade_date BETWEEN ? AND ?",
                    [s_d, e_d],
                ).pl()
                if not af.is_empty():
                    df = df.join(af, on=["symbol", "trade_date"], how="left").with_columns(
                        pl.col("adj_factor").fill_null(1.0)
                    )
                    df = adjust_ohlcv(df, mode=adjust)
            except duckdb_error:  # type: ignore[name-defined]
                pass

        # daily_basic join — unlocks bp/ep/roe/size factors
        if include_basic:
            try:
                cols = self.query.con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'daily_basic'"
                ).pl()["column_name"].to_list()
                wanted = [c for c in
                          ["symbol", "trade_date", "pe_ttm", "pb", "ps_ttm",
                           "total_mv", "circ_mv", "turnover_rate"]
                          if c in cols]
                if len(wanted) > 2:
                    db = self.query.con.execute(
                        f"SELECT {', '.join(wanted)} FROM daily_basic "
                        "WHERE trade_date BETWEEN ? AND ?",
                        [s_d, e_d],
                    ).pl()
                    if not db.is_empty():
                        df = df.join(db, on=["symbol", "trade_date"], how="left")
            except duckdb_error:  # type: ignore[name-defined]
                pass

        return df

    # -- write ------------------------------------------------------------------

    def sync_daily(self, start: date | str, end: date | str) -> int:
        if self.source is None:
            raise RuntimeError("source not configured; pass one to DataAPI()")
        s, e = _to_date(start), _to_date(end)
        log.info(f"syncing daily {s}..{e} from {self.source.name}")
        df = self.source.daily(None, s, e)
        n = self.store.write_daily(df, dataset="daily")
        # Re-register views so new data is queryable
        self.query._register_views()
        return n

    def sync_adj_factor(self, start: date | str, end: date | str) -> int:
        if self.source is None:
            raise RuntimeError("source not configured")
        s, e = _to_date(start), _to_date(end)
        df = self.source.adj_factor(None, s, e)
        n = self.store.write_daily(df, dataset="adj_factor")
        self.query._register_views()
        return n

    def sync_daily_basic(self, start: date | str, end: date | str) -> int:
        if self.source is None:
            raise RuntimeError("source not configured")
        s, e = _to_date(start), _to_date(end)
        df = self.source.daily_basic(None, s, e)
        n = self.store.write_daily(df, dataset="daily_basic")
        self.query._register_views()
        return n

    def sync_stock_basic(self) -> int:
        if self.source is None:
            raise RuntimeError("source not configured")
        df = self.source.stock_basic()
        if df.is_empty():
            return 0
        # Stock basic is small; overwrite a single parquet
        target = Path(self.store.root) / "stock_basic" / "year=0" / "month=00"
        target.mkdir(parents=True, exist_ok=True)
        df.write_parquet(target / "data.parquet")
        self.query._register_views()
        return len(df)


# Sentinel for missing-table query error
try:
    import duckdb as _duckdb
    duckdb_error = (_duckdb.Error, _duckdb.CatalogException)  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    duckdb_error = (Exception,)


def _build_source(settings, preferred: str | None = None) -> DataSource | None:
    """Choose primary data source. Preference order:

      1. `preferred` arg (CLI override): 'tushare' | 'akshare' | 'mock'.
      2. Env var UNI_QUANT_SOURCE=...
      3. Config `data_sources.primary_source` (TODO: surface in pydantic model).
      4. AkShare if installed (free, no token).
      5. Tushare if token configured.
      6. MockSource fallback.
    """
    import os
    from open_quant.data.sources import AkShareSource

    ts_cfg = settings.data_sources.tushare
    pref = preferred or os.environ.get("UNI_QUANT_SOURCE", "").lower() or None

    def _try_akshare():
        try:
            return AkShareSource()
        except RuntimeError as e:
            log.debug(f"AkShare unavailable: {e}")
            return None

    def _try_tushare():
        if ts_cfg.token and ts_cfg.token != "REPLACE_WITH_YOUR_TOKEN":
            try:
                return TushareSource(token=ts_cfg.token,
                                     rate_limit_per_minute=ts_cfg.rate_limit_per_minute)
            except Exception as e:  # pragma: no cover
                log.warning(f"failed to init Tushare: {e}")
        return None

    if pref == "tushare":
        return _try_tushare() or _try_akshare() or MockSource(["600519.SH"])
    if pref == "akshare":
        return _try_akshare() or _try_tushare() or MockSource(["600519.SH"])
    if pref == "mock":
        return MockSource(symbols=["600519.SH", "000001.SZ", "300750.SZ", "688981.SH"])

    # Default order: akshare (free) → tushare → mock
    src = _try_akshare()
    if src:
        log.info(f"using data source: {src.name}")
        return src
    src = _try_tushare()
    if src:
        log.info(f"using data source: {src.name}")
        return src
    log.warning("no real data source available — using MockSource (synthetic data)")
    return MockSource(symbols=["600519.SH", "000001.SZ", "300750.SZ", "688981.SH"])


@lru_cache(maxsize=1)
def get_data_api(source: str | None = None) -> DataAPI:
    settings = load_settings()
    storage = settings.data_sources.storage
    store = ParquetStore(storage.parquet_root)
    query = DuckDBQuery(storage.duckdb_path, storage.parquet_root)
    src = _build_source(settings, preferred=source)
    return DataAPI(store=store, query=query, source=src)
