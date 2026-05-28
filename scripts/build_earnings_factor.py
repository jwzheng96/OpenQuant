"""Build earnings_pead factor from synced 业绩报表 data.

Methodology:
  - For each (symbol, trade_date d), find most recent earnings announcement
    with announce_date <= d (no lookahead).
  - Trading-day distance: days_since = days from announce to d (1-trading-day
    granularity).
  - PEAD window: 0 ≤ days_since ≤ 10. Outside → 0.
  - Signal: winsorize(ni_yoy, ±150%) × (1 - days_since/10)   (linear decay)
  - Logic: positive earnings surprise + recent announcement → buy.
    Effect fades over 10 trading days (classic PEAD half-life).

Output: data/parquet/factors/name=earnings_pead/data.parquet
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

PEAD_WINDOW = 10           # trading days
NI_YOY_CLIP = 150.0        # winsorize at ±150%


def main():
    api = get_data_api()
    earnings = pl.read_parquet("data/parquet/earnings/year=0/month=00/data.parquet")
    log.info(f"earnings: {len(earnings):,} rows, {earnings['symbol'].n_unique()} symbols")

    # Universe: our 1799 stocks in daily store
    universe = set(api.query.con.execute("SELECT DISTINCT symbol FROM daily").pl()["symbol"].to_list())
    log.info(f"universe: {len(universe)} stocks")

    earnings_u = earnings.filter(pl.col("symbol").is_in(universe))
    log.info(f"earnings in universe: {len(earnings_u):,} ({earnings_u['symbol'].n_unique()} symbols)")

    # Winsorize ni_yoy
    earnings_u = earnings_u.with_columns(
        pl.col("ni_yoy").clip(-NI_YOY_CLIP, NI_YOY_CLIP).alias("ni_yoy_w")
    )

    # Trading days ≥ 2024-01-01 (factor only needs to cover backtest window)
    trading_days = api.query.con.execute("""
        SELECT DISTINCT trade_date FROM daily
        WHERE trade_date BETWEEN '2024-01-01' AND '2026-12-31'
        ORDER BY trade_date
    """).pl()
    log.info(f"trading days: {len(trading_days)}")

    # For each symbol that has at least one earnings announcement, do an
    # asof-join: panel_date → most recent announce_date ≤ panel_date.
    active_symbols = earnings_u["symbol"].unique().to_list()
    log.info(f"symbols with ≥1 earnings: {len(active_symbols)}")

    # Build panel grid for active symbols
    grid = pl.DataFrame({
        "symbol": [s for s in active_symbols for _ in range(len(trading_days))],
        "trade_date": list(trading_days["trade_date"]) * len(active_symbols),
    })
    log.info(f"grid: {len(grid):,} rows")

    # asof join: for each (symbol, trade_date), find latest (symbol, announce_date ≤ trade_date)
    e_sorted = earnings_u.sort(["symbol", "announce_date"])
    g_sorted = grid.sort(["symbol", "trade_date"])
    joined = g_sorted.join_asof(
        e_sorted,
        left_on="trade_date",
        right_on="announce_date",
        by="symbol",
        strategy="backward",   # most recent announce_date ≤ trade_date
    )
    log.info(f"after asof-join: {len(joined):,} rows")

    # Compute trading-day-distance using daily trade_date index
    # (Approximation: calendar-day distance × 5/7. Good enough for 10-day PEAD window.)
    out = (joined
           .with_columns(
               ((pl.col("trade_date") - pl.col("announce_date")).dt.total_days() * 5 / 7).alias("days_since")
           )
           .with_columns(
               pl.when((pl.col("days_since") >= 0) & (pl.col("days_since") <= PEAD_WINDOW))
                 .then(pl.col("ni_yoy_w") * (1 - pl.col("days_since") / PEAD_WINDOW))
                 .otherwise(0.0)
                 .alias("value")
           )
           .with_columns(pl.col("value").fill_null(0.0))
           .select(["symbol", "trade_date", "value"]))

    # Diagnostic
    nz = out.filter(pl.col("value") != 0).height
    log.info(f"output: {len(out):,} rows, non-zero: {nz:,} ({nz/len(out)*100:.1f}%)")
    log.info(f"value: [{out['value'].min():.2f}, {out['value'].max():.2f}], "
             f"mean of non-zero = {out.filter(pl.col('value')!=0)['value'].mean():.2f}")

    out_dir = Path("data/parquet/factors/name=earnings_pead")
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")


if __name__ == "__main__":
    main()
