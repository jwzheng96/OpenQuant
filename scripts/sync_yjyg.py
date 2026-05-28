"""Sync 业绩预告 from ak.stock_yjyg_em.

Forecast issued BEFORE official quarterly report — typically 30+ days
ahead of the official report. A-share companies with NI YoY > +50% or
< -50% must issue a forecast (法定催化剂).

One call per quarter end; 13 quarters from 2023-Q1 to 2026-Q1.
Output: data/parquet/yjyg/year=0/month=00/data.parquet
"""
from __future__ import annotations

import os
import re
import time
from datetime import date
from pathlib import Path

for _k in ("HTTPS_PROXY", "HTTP_PROXY"):
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


def _to_ts(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60","688","689","900")): return f"{c}.SH"
    if c.startswith(("000","001","002","003","300","301","200")): return f"{c}.SZ"
    return f"{c}.BJ"


QUARTERS = [
    "20230331","20230630","20230930","20231231",
    "20240331","20240630","20240930","20241231",
    "20250331","20250630","20250930","20251231",
    "20260331",
]


def fetch_quarter(q: str) -> pl.DataFrame:
    try:
        df = ak.stock_yjyg_em(date=q)
        if df is None or len(df) == 0:
            return pl.DataFrame()
    except Exception as e:
        log.warning(f"  {q}: {e}")
        return pl.DataFrame()
    df = pl.from_pandas(df)
    # Source cols: 序号, 股票代码, 股票简称, 预测指标, 业绩变动, 预测数值,
    #              业绩变动幅度, 业绩变动原因, 预告类型, 上年同期值, 公告日期
    renames = {
        "股票代码":      "code",
        "公告日期":      "announce_date",
        "业绩变动幅度":  "yjyg_change_pct",
        "预告类型":      "yjyg_type",
        "预测指标":      "yjyg_indicator",
        "上年同期值":    "yjyg_prior",
        "预测数值":      "yjyg_forecast",
    }
    df = df.rename({k: v for k, v in renames.items() if k in df.columns})
    df = df.with_columns(
        pl.col("code").cast(pl.Utf8).map_elements(_to_ts, return_dtype=pl.Utf8).alias("symbol"),
        pl.lit(q).alias("quarter_end"),
    )
    if df.schema["announce_date"] == pl.Utf8:
        df = df.with_columns(pl.col("announce_date").str.to_date("%Y-%m-%d"))
    for c in ("yjyg_change_pct", "yjyg_prior", "yjyg_forecast"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Float64))

    # 只保留 归属于上市公司股东的净利润 — 主指标
    if "yjyg_indicator" in df.columns:
        df = df.filter(pl.col("yjyg_indicator").str.contains("归属于上市公司股东的净利润"))

    keep = ["symbol", "quarter_end", "announce_date", "yjyg_change_pct",
            "yjyg_type", "yjyg_indicator"]
    return df.select([c for c in keep if c in df.columns])


def main():
    frames: list[pl.DataFrame] = []
    for q in QUARTERS:
        t = time.time()
        df = fetch_quarter(q)
        log.info(f"  {q}: {len(df):,} rows ({time.time()-t:.1f}s)")
        if not df.is_empty():
            frames.append(df)
        time.sleep(0.3)

    if not frames:
        log.error("no data")
        return

    big = pl.concat(frames, how="diagonal_relaxed").drop_nulls(subset=["announce_date"])
    big = big.sort(["symbol", "quarter_end", "announce_date"]).unique(
        subset=["symbol", "quarter_end"], keep="last"
    )
    log.info(f"\ntotal: {len(big):,} rows / {big['symbol'].n_unique()} symbols / "
             f"announce {big['announce_date'].min()} → {big['announce_date'].max()}")

    out_dir = Path("data/parquet/yjyg/year=0/month=00")
    out_dir.mkdir(parents=True, exist_ok=True)
    big.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")

    from open_quant.data.api import get_data_api
    api = get_data_api()
    try:
        api.query.con.execute("DROP VIEW IF EXISTS yjyg")
        api.query.con.execute("""
            CREATE VIEW yjyg AS
            SELECT * FROM read_parquet('data/parquet/yjyg/year=*/month=*/data.parquet',
                                       hive_partitioning=1)
        """)
        n = api.query.con.execute("SELECT COUNT(*) FROM yjyg").fetchone()[0]
        log.info(f"view yjyg registered ({n} rows)")
    except Exception as e:
        log.warning(f"view: {e}")


if __name__ == "__main__":
    main()
