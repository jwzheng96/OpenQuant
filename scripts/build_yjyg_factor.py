"""Build earnings_yjyg factor from synced 业绩预告 data.

A-share companies must issue 业绩预告 (earnings forecast) ~30+ days
before the formal quarterly report, when:
  - 净利润同比 > +50% (好预告)
  - 净利润同比 < -50% (坏预告)
  - 由盈转亏 / 由亏转盈

These announcements are legally mandated catalysts. The alpha:
  - Strong positive forecast → market overreacts initially then continues
    rising 5-15 days post-announce
  - Strong negative forecast → opposite, but A股 limit-down on day 1
    captures most of the move (hard to fade)

Factor: winsorize(yjyg_change_pct, ±300%) × decay over 15 trading days.
Output: data/parquet/factors/name=earnings_yjyg/data.parquet
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

PEAD_WINDOW = 15
CLIP = 300.0


def main():
    api = get_data_api()
    yjyg = pl.read_parquet("data/parquet/yjyg/year=0/month=00/data.parquet")
    log.info(f"yjyg: {len(yjyg):,} rows, {yjyg['symbol'].n_unique()} symbols")

    universe = set(api.query.con.execute("SELECT DISTINCT symbol FROM daily").pl()["symbol"].to_list())
    log.info(f"universe: {len(universe)} stocks")

    yjyg_u = yjyg.filter(pl.col("symbol").is_in(universe))
    log.info(f"yjyg in universe: {len(yjyg_u):,} ({yjyg_u['symbol'].n_unique()} symbols)")

    yjyg_u = yjyg_u.with_columns(
        pl.col("yjyg_change_pct").clip(-CLIP, CLIP).alias("yjyg_clipped")
    )

    trading_days = api.query.con.execute("""
        SELECT DISTINCT trade_date FROM daily
        WHERE trade_date BETWEEN '2024-01-01' AND '2026-12-31'
        ORDER BY trade_date
    """).pl()
    log.info(f"trading days: {len(trading_days)}")

    active_symbols = yjyg_u["symbol"].unique().to_list()
    log.info(f"symbols with yjyg: {len(active_symbols)}")

    grid = pl.DataFrame({
        "symbol": [s for s in active_symbols for _ in range(len(trading_days))],
        "trade_date": list(trading_days["trade_date"]) * len(active_symbols),
    })

    e_sorted = yjyg_u.sort(["symbol", "announce_date"])
    g_sorted = grid.sort(["symbol", "trade_date"])
    joined = g_sorted.join_asof(
        e_sorted,
        left_on="trade_date",
        right_on="announce_date",
        by="symbol",
        strategy="backward",
    )

    out = (joined
           .with_columns(
               ((pl.col("trade_date") - pl.col("announce_date")).dt.total_days() * 5 / 7).alias("days_since")
           )
           .with_columns(
               pl.when((pl.col("days_since") >= 0) & (pl.col("days_since") <= PEAD_WINDOW))
                 .then(pl.col("yjyg_clipped") * (1 - pl.col("days_since") / PEAD_WINDOW))
                 .otherwise(0.0)
                 .alias("value")
           )
           .with_columns(pl.col("value").fill_null(0.0))
           .select(["symbol", "trade_date", "value"]))

    nz = out.filter(pl.col("value") != 0).height
    log.info(f"output: {len(out):,} rows, non-zero: {nz:,} ({nz/len(out)*100:.1f}%)")
    log.info(f"value range: [{out['value'].min():.2f}, {out['value'].max():.2f}]")

    out_dir = Path("data/parquet/factors/name=earnings_yjyg")
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_parquet(out_dir / "data.parquet")
    log.info(f"wrote {out_dir / 'data.parquet'}")


if __name__ == "__main__":
    main()
