"""Sync northbound (沪深港通) per-stock daily flow into Parquet.

Uses ak.stock_hsgt_individual_em(symbol=) — one call per stock, returns
full history. We focus on HS300+CSI500 (top ~800 stocks by market cap)
since northbound flow concentrates there; smaller stocks rarely move.

Output: data/parquet/north_flow/year=YYYY/month=MM/data.parquet
Columns: symbol, trade_date, north_hold_qty, north_inc_qty, north_inc_amt
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

for _k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

import polars as pl
import requests

_old_sess_init = requests.Session.__init__
def _new_sess_init(self, *a, **k):
    _old_sess_init(self, *a, **k)
    self.trust_env = False
requests.Session.__init__ = _new_sess_init

import akshare as ak
from pathlib import Path

from open_quant.utils import get_logger

log = get_logger(__name__)


def fetch_one(symbol: str) -> pl.DataFrame:
    """Pull one stock's full northbound history. Returns empty df on any error."""
    code = symbol.split(".")[0]
    try:
        df = ak.stock_hsgt_individual_em(symbol=code)
        if df is None or len(df) == 0:
            return pl.DataFrame()
    except Exception:
        return pl.DataFrame()
    # Source columns: 持股日期, 当日收盘价, 当日涨跌幅, 持股数量, 持股市值,
    #                 持股数量占A股百分比, 今日增持股数, 今日增持资金, 今日持股市值变化
    # Map to our schema
    out = pl.from_pandas(df).rename({
        "持股日期":          "trade_date",
        "持股数量":          "north_hold_qty",
        "持股市值":          "north_hold_mv",
        "今日增持股数":      "north_inc_qty",
        "今日增持资金":      "north_inc_amt",
    })
    # trade_date may be string or already date (polars infers from pandas)
    out = out.with_columns(pl.lit(symbol).alias("symbol"))
    if out.schema["trade_date"] == pl.Utf8:
        out = out.with_columns(pl.col("trade_date").str.to_date("%Y-%m-%d"))
    # Coerce all numeric cols to Float64 — pandas infers Int when no NaN,
    # which causes concat schema clashes across symbols
    for c in ("north_hold_qty", "north_hold_mv", "north_inc_qty", "north_inc_amt"):
        if c in out.columns:
            out = out.with_columns(pl.col(c).cast(pl.Float64))
    return out.select(["symbol", "trade_date", "north_hold_qty", "north_hold_mv",
                       "north_inc_qty", "north_inc_amt"])


def main():
    from open_quant.data.api import get_data_api
    api = get_data_api()

    # Universe: HS300 + CSI500 = ~800 large/mid caps (north flow concentrated here)
    import akshare as ak2
    targets: set[str] = set()
    for idx in ("000300", "000905"):
        df = ak2.index_stock_cons_csindex(symbol=idx)
        for code in df["成分券代码"]:
            c = str(code).zfill(6)
            if c.startswith(("60","688","689")): targets.add(f"{c}.SH")
            elif c.startswith(("000","001","002","003","300","301")): targets.add(f"{c}.SZ")
    targets = sorted(targets)
    log.info(f"north-flow target universe: {len(targets)} stocks (HS300 + CSI500)")

    frames: list[pl.DataFrame] = []
    ok = empty = fail = 0
    t0 = time.time()
    for i, sym in enumerate(targets, 1):
        df = fetch_one(sym)
        if df.is_empty():
            empty += 1
        else:
            frames.append(df)
            ok += 1
        time.sleep(0.1)   # gentle rate limit
        if i % 50 == 0 or i == len(targets):
            elapsed = time.time() - t0
            eta = (len(targets) - i) / max(i/elapsed, 0.01)
            log.info(f"  [{i:3d}/{len(targets)}] ok={ok} empty={empty} elapsed={elapsed:.0f}s eta={eta:.0f}s")

    if not frames:
        log.error("no data!")
        return
    big = pl.concat(frames)
    log.info(f"total rows: {len(big)}, date range {big['trade_date'].min()} → {big['trade_date'].max()}")

    # Write to single parquet (small enough — ~800 stocks × 1700 days ≈ 1.4M rows)
    out_dir = Path("data/parquet/north_flow/year=0/month=00")
    out_dir.mkdir(parents=True, exist_ok=True)
    big.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")

    # Register view
    try:
        api.query.con.execute("DROP VIEW IF EXISTS north_flow")
        api.query.con.execute("""
            CREATE VIEW north_flow AS
            SELECT * FROM read_parquet('data/parquet/north_flow/year=*/month=*/data.parquet',
                                       hive_partitioning=1)
        """)
        n = api.query.con.execute("SELECT COUNT(*) FROM north_flow").fetchone()[0]
        log.info(f"view registered: north_flow ({n} rows)")
    except Exception as e:
        log.warning(f"view registration failed: {e}")


if __name__ == "__main__":
    main()
