"""Sync 龙虎榜 daily data into Parquet.

ak.stock_lhb_detail_em(start_date, end_date) returns per-stock lhb rows
within the window. Pull monthly chunks to avoid huge responses.

Output: data/parquet/lhb/year=0/month=00/data.parquet (single file —
total volume is small: ~70 rows/day × 600 days × 2 yr ≈ 80k rows)

Columns (we keep):
  symbol, trade_date, lhb_net_buy_amt, lhb_buy_amt, lhb_sell_amt,
  lhb_net_buy_ratio, lhb_turnover_ratio, lhb_reason
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

for _k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
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


def fetch_chunk(start_d: date, end_d: date) -> pl.DataFrame:
    try:
        df = ak.stock_lhb_detail_em(
            start_date=start_d.strftime("%Y%m%d"),
            end_date=end_d.strftime("%Y%m%d"),
        )
        if df is None or len(df) == 0:
            return pl.DataFrame()
    except Exception as e:
        log.warning(f"  chunk {start_d}..{end_d}: {e}")
        return pl.DataFrame()

    df = pl.from_pandas(df)
    # Rename to schema. Drop forward-return columns (上榜后N日) — those are
    # future info that wouldn't be known at decision time.
    renames = {
        "代码":              "code",
        "上榜日":            "trade_date",
        "龙虎榜净买额":      "lhb_net_buy_amt",
        "龙虎榜买入额":      "lhb_buy_amt",
        "龙虎榜卖出额":      "lhb_sell_amt",
        "净买额占总成交比":  "lhb_net_buy_ratio",
        "成交额占总成交比":  "lhb_turnover_ratio",
        "上榜原因":          "lhb_reason",
        "流通市值":          "lhb_circ_mv",
    }
    keep = [v for v in renames.values()]
    df = df.rename({k: v for k, v in renames.items() if k in df.columns})
    df = df.with_columns(
        pl.col("code").cast(pl.Utf8).map_elements(_to_ts_code, return_dtype=pl.Utf8).alias("symbol"),
    )
    # trade_date may already be date type from pandas
    if df.schema["trade_date"] == pl.Utf8:
        df = df.with_columns(pl.col("trade_date").str.to_date("%Y-%m-%d"))
    # Cast numeric columns to Float64 (consistency across chunks)
    for c in ("lhb_net_buy_amt","lhb_buy_amt","lhb_sell_amt",
              "lhb_net_buy_ratio","lhb_turnover_ratio","lhb_circ_mv"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Float64))
    extra = [c for c in keep if c in df.columns and c not in ("code", "trade_date")]
    return df.select(["symbol", "trade_date"] + extra)


def main():
    start = date(2024, 1, 1)
    end = date.today()
    log.info(f"sync lhb {start} → {end}")

    frames: list[pl.DataFrame] = []
    cur = start.replace(day=1)
    while cur <= end:
        # last day of this month
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunk_end = min(nxt - timedelta(days=1), end)
        df = fetch_chunk(cur, chunk_end)
        if not df.is_empty():
            frames.append(df)
            log.info(f"  {cur:%Y-%m}: {len(df)} rows")
        else:
            log.info(f"  {cur:%Y-%m}: 0")
        cur = nxt
        time.sleep(0.3)

    if not frames:
        log.error("no data")
        return
    big = pl.concat(frames, how="diagonal_relaxed")
    log.info(f"\ntotal: {len(big):,} rows / {big['symbol'].n_unique()} symbols / "
             f"{big['trade_date'].min()} → {big['trade_date'].max()}")

    out_dir = Path("data/parquet/lhb/year=0/month=00")
    out_dir.mkdir(parents=True, exist_ok=True)
    big.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")

    # Register view
    from open_quant.data.api import get_data_api
    api = get_data_api()
    try:
        api.query.con.execute("DROP VIEW IF EXISTS lhb")
        api.query.con.execute("""
            CREATE VIEW lhb AS
            SELECT * FROM read_parquet('data/parquet/lhb/year=*/month=*/data.parquet',
                                       hive_partitioning=1)
        """)
        n = api.query.con.execute("SELECT COUNT(*) FROM lhb").fetchone()[0]
        log.info(f"view lhb registered ({n} rows)")
    except Exception as e:
        log.warning(f"view registration: {e}")


if __name__ == "__main__":
    main()
