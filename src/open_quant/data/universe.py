"""Universe construction — filter the tradable A-share pool by date.

Rules pulled from `configs/universe.yaml`. The output is a per-date set of
symbols ready for the strategy to consume.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import polars as pl

from open_quant.backtest.ashare_rules import BoardType, classify_board, is_st
from open_quant.utils.config import UniverseRule


def filter_universe(
    daily: pl.DataFrame,
    basic: pl.DataFrame,
    stock_basic: pl.DataFrame,
    rule: UniverseRule,
    *,
    as_of: date,
) -> list[str]:
    """Return list of symbols that pass the rule on `as_of` date.

    Inputs:
        daily      — daily OHLCV for at least 20 trading days up to `as_of`
        basic      — daily_basic (turnover, total_mv) snapshot for `as_of`
        stock_basic — per-symbol list_date / delist_date / name
        rule       — UniverseRule
    """
    if stock_basic.is_empty():
        return []
    sb = stock_basic
    # Board filter
    sb = sb.with_columns(
        pl.col("ts_code").map_elements(lambda s: classify_board(s).value, return_dtype=pl.Utf8).alias("_board")
    )
    keep_boards: set[str] = set()
    if "SSE" in rule.exchanges:
        keep_boards |= {BoardType.SSE_MAIN.value, BoardType.STAR.value}
    if "SZSE" in rule.exchanges:
        keep_boards |= {BoardType.SZSE_MAIN.value, BoardType.CHINEXT.value}
    if "BSE" in rule.exchanges:
        keep_boards |= {BoardType.BSE.value}
    sb = sb.filter(pl.col("_board").is_in(list(keep_boards)))

    # ST filter
    if rule.exclude_st:
        sb = sb.filter(~pl.col("name").map_elements(is_st, return_dtype=pl.Boolean))

    # List-date filter
    cutoff = (as_of - timedelta(days=int(rule.exclude_new_listings_days * 1.5))).strftime("%Y%m%d")
    sb = sb.filter(pl.col("list_date") <= cutoff)

    # Delist filter
    if rule.exclude_will_delist:
        sb = sb.filter(
            pl.col("delist_date").is_null() | (pl.col("delist_date") == "")
        )

    # Blacklist
    if rule.blacklist:
        sb = sb.filter(~pl.col("ts_code").is_in(rule.blacklist))

    candidates = set(sb["ts_code"].to_list())

    # Market cap + turnover filter (from daily_basic)
    if not basic.is_empty():
        snap = basic.filter(pl.col("trade_date") == as_of)
        if "total_mv" in snap.columns:
            snap = snap.filter(pl.col("total_mv") * 1e4 >= rule.min_market_cap)  # tushare: 万元
        candidates &= set(snap["ts_code"].to_list() if "ts_code" in snap.columns else snap["symbol"].to_list())

    # 20d avg turnover from daily.amount
    if not daily.is_empty() and rule.min_avg_turnover_20d > 0:
        window_start = as_of - timedelta(days=40)
        window = (
            daily.filter((pl.col("trade_date") >= window_start) & (pl.col("trade_date") <= as_of))
            .group_by("symbol")
            .agg(pl.col("amount").mean().alias("avg_amount"))
            .filter(pl.col("avg_amount") * 1e3 >= rule.min_avg_turnover_20d)  # amount 单位千元
        )
        candidates &= set(window["symbol"].to_list())

    return sorted(candidates)


def annotate_for_backtest(daily: pl.DataFrame, stock_basic: pl.DataFrame) -> pl.DataFrame:
    """Attach `board` and `is_st` columns used by the event-driven backtester."""
    if daily.is_empty():
        return daily
    names = (
        stock_basic.select(["ts_code", "name"])
        .rename({"ts_code": "symbol"})
        .with_columns(pl.col("name").map_elements(is_st, return_dtype=pl.Boolean).alias("is_st"))
        .drop("name")
    )
    out = daily.join(names, on="symbol", how="left").with_columns(
        pl.col("symbol").map_elements(lambda s: classify_board(s).value, return_dtype=pl.Utf8).alias("board"),
        pl.col("is_st").fill_null(False),
    )
    return out
