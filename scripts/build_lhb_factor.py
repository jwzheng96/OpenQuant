"""Build lhb_signal factor from synced 龙虎榜 data.

Methodology:
  - daily raw signal = lhb_net_buy_amt / lhb_circ_mv  (净买额占流通市值的比例)
    →  if not on list that day, signal = 0
  - smoothed signal = 5-day rolling sum of raw signal
  - rationale: the realized lhb-list PEAD effect has ~5-day half-life
    (per the 上榜后1日/2日/5日 columns)

Output: data/parquet/factors/name=lhb_signal/data.parquet
  (symbol, trade_date, value)

Coverage: for each panel (symbol, trade_date) in the universe AFTER 2024-01,
emit a value (zero if no lhb activity in window).
"""
from __future__ import annotations

import os
from pathlib import Path

for _k in ("HTTPS_PROXY", "HTTP_PROXY"):
    os.environ.pop(_k, None)

import polars as pl

from open_quant.data.api import get_data_api
from open_quant.utils import get_logger

log = get_logger(__name__)

ROLLING_WINDOW = 5


def main():
    api = get_data_api()
    lhb = pl.read_parquet("data/parquet/lhb/year=0/month=00/data.parquet")
    log.info(f"lhb rows: {len(lhb):,}  symbols: {lhb['symbol'].n_unique()}  "
             f"dates: {lhb['trade_date'].min()} → {lhb['trade_date'].max()}")

    # Universe = anything in daily that overlaps with the lhb window
    daily_range = api.query.con.execute("""
        SELECT MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
        FROM daily WHERE trade_date >= '2024-01-01'
    """).fetchone()
    log.info(f"daily range (≥2024): {daily_range[0]} → {daily_range[1]}")

    # All trading days in our daily store ≥ 2024-01-01
    trading_days = api.query.con.execute("""
        SELECT DISTINCT trade_date FROM daily
        WHERE trade_date BETWEEN '2024-01-01' AND ?
        ORDER BY trade_date
    """, [daily_range[1]]).pl()["trade_date"].to_list()
    log.info(f"trading days: {len(trading_days)}")

    # Universe = symbols in daily
    universe = api.query.con.execute("SELECT DISTINCT symbol FROM daily").pl()["symbol"].to_list()
    log.info(f"universe size: {len(universe)}")

    # Restrict lhb to our universe
    lhb_in = lhb.filter(pl.col("symbol").is_in(universe))
    log.info(f"lhb rows in universe: {len(lhb_in):,} "
             f"({lhb_in['symbol'].n_unique()} symbols actually appear on lhb)")

    # Per (symbol, trade_date): raw signal = lhb_net_buy_amt / lhb_circ_mv
    # (some rows may have multiple entries — sum them)
    lhb_signal = (lhb_in
                  .with_columns(
                      (pl.col("lhb_net_buy_amt") /
                       pl.when(pl.col("lhb_circ_mv") > 0)
                         .then(pl.col("lhb_circ_mv"))
                         .otherwise(1.0)).alias("raw_signal")
                  )
                  .group_by(["symbol", "trade_date"])
                  .agg(pl.col("raw_signal").sum().alias("raw_signal")))
    log.info(f"distinct (symbol,date) on lhb: {len(lhb_signal):,}")

    # Build a full grid of (sym, date) for lhb-active symbols only
    # Then fill missing dates with 0 raw_signal, then rolling sum
    active_symbols = lhb_signal["symbol"].unique().to_list()
    log.info(f"active symbols (have ≥1 lhb day): {len(active_symbols)}")

    grid = pl.DataFrame({
        "symbol": [s for s in active_symbols for _ in trading_days],
        "trade_date": [d for _ in active_symbols for d in trading_days],
    })
    filled = (grid.join(lhb_signal, on=["symbol", "trade_date"], how="left")
              .with_columns(pl.col("raw_signal").fill_null(0.0)))

    # Rolling sum within each symbol
    out = (filled.sort(["symbol", "trade_date"])
           .with_columns(
               pl.col("raw_signal").rolling_sum(ROLLING_WINDOW).over("symbol").alias("value")
           )
           .with_columns(pl.col("value").fill_null(0.0))
           .select(["symbol", "trade_date", "value"]))

    # Diagnostic
    nz = out.filter(pl.col("value") != 0).height
    log.info(f"output rows: {len(out):,} ; non-zero: {nz:,} ({nz/len(out)*100:.1f}%)")
    log.info(f"value range: [{out['value'].min():.4f}, {out['value'].max():.4f}]")

    out_dir = Path("data/parquet/factors/name=lhb_signal")
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")


if __name__ == "__main__":
    main()
