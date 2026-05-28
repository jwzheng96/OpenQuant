"""Sync quarterly 业绩报表 from ak.stock_yjbb_em.

One call per quarter end (e.g. '20240331'); each returns ~5500 rows
covering all A-share companies that reported by query time. We pull
2023-Q1 through 2026-Q1 (13 quarters).

Output: data/parquet/earnings/year=0/month=00/data.parquet
Columns: symbol, quarter_end, announce_date, ni_yoy, rev_yoy,
         roe, gross_margin, eps
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

for _k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

import polars as pl
import requests
_old = requests.Session.__init__
def _new(self, *a, **k):
    _old(self, *a, **k); self.trust_env = False
requests.Session.__init__ = _new

import akshare as ak

from open_quant.utils import get_logger

log = get_logger(__name__)


def _to_ts_code(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60","688","689","900")): return f"{c}.SH"
    if c.startswith(("000","001","002","003","300","301","200")): return f"{c}.SZ"
    return f"{c}.BJ"


QUARTER_ENDS = [
    "20230331", "20230630", "20230930", "20231231",
    "20240331", "20240630", "20240930", "20241231",
    "20250331", "20250630", "20250930", "20251231",
    "20260331",
]


def fetch_quarter(qend: str) -> pl.DataFrame:
    try:
        df = ak.stock_yjbb_em(date=qend)
        if df is None or len(df) == 0:
            return pl.DataFrame()
    except Exception as e:
        log.warning(f"  {qend}: {e}")
        return pl.DataFrame()

    df = pl.from_pandas(df)
    renames = {
        "股票代码":           "code",
        "最新公告日期":       "announce_date",
        "净利润-同比增长":    "ni_yoy",
        "营业总收入-同比增长":"rev_yoy",
        "净资产收益率":       "roe",
        "销售毛利率":         "gross_margin",
        "每股收益":           "eps",
    }
    df = df.rename({k: v for k, v in renames.items() if k in df.columns})
    df = df.with_columns(
        pl.col("code").cast(pl.Utf8).map_elements(_to_ts_code, return_dtype=pl.Utf8).alias("symbol"),
        pl.lit(qend).alias("quarter_end"),
    )
    # announce_date sometimes string, sometimes already date
    if df.schema["announce_date"] == pl.Utf8:
        df = df.with_columns(pl.col("announce_date").str.to_date("%Y-%m-%d"))
    # Numeric coercion
    for c in ("ni_yoy", "rev_yoy", "roe", "gross_margin", "eps"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Float64))

    keep = ["symbol", "quarter_end", "announce_date", "ni_yoy", "rev_yoy",
            "roe", "gross_margin", "eps"]
    return df.select([c for c in keep if c in df.columns])


def main():
    frames: list[pl.DataFrame] = []
    for q in QUARTER_ENDS:
        t = time.time()
        df = fetch_quarter(q)
        log.info(f"  {q}: {len(df):,} rows ({time.time()-t:.1f}s)")
        if not df.is_empty():
            frames.append(df)
        time.sleep(0.5)

    if not frames:
        log.error("no data")
        return

    big = pl.concat(frames, how="diagonal_relaxed").drop_nulls(subset=["announce_date"])
    # Some companies announce a quarter multiple times (revisions) — keep last
    big = big.sort(["symbol", "quarter_end", "announce_date"]).unique(
        subset=["symbol", "quarter_end"], keep="last"
    )
    log.info(f"\ntotal: {len(big):,} rows / {big['symbol'].n_unique()} symbols / "
             f"{len(big['quarter_end'].unique())} quarters / "
             f"announce date range {big['announce_date'].min()} → {big['announce_date'].max()}")

    out_dir = Path("data/parquet/earnings/year=0/month=00")
    out_dir.mkdir(parents=True, exist_ok=True)
    big.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")

    # Register view
    from open_quant.data.api import get_data_api
    api = get_data_api()
    try:
        api.query.con.execute("DROP VIEW IF EXISTS earnings")
        api.query.con.execute("""
            CREATE VIEW earnings AS
            SELECT * FROM read_parquet('data/parquet/earnings/year=*/month=*/data.parquet',
                                       hive_partitioning=1)
        """)
        n = api.query.con.execute("SELECT COUNT(*) FROM earnings").fetchone()[0]
        log.info(f"view earnings registered ({n} rows)")
    except Exception as e:
        log.warning(f"view: {e}")


if __name__ == "__main__":
    main()
