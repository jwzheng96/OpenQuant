"""Bundled factor library — value, quality, momentum, volatility, microstructure.

All factors share the same signature: take a long-format panel, return a
`symbol/trade_date/value` DataFrame. Higher value = more attractive (we adopt
a "long good" sign convention; reversal-type factors are negated where needed).
"""

from __future__ import annotations

import polars as pl


def bp_factor(panel: pl.DataFrame) -> pl.DataFrame:
    """Book-to-price = 1 / PB. Source: daily_basic.pb."""
    if "pb" not in panel.columns:
        return pl.DataFrame()
    return panel.select([
        "symbol", "trade_date",
        (pl.when(pl.col("pb") > 0).then(1.0 / pl.col("pb")).otherwise(None)).alias("value"),
    ])


def ep_factor(panel: pl.DataFrame) -> pl.DataFrame:
    """Earnings-to-price = 1 / PE_TTM."""
    if "pe_ttm" not in panel.columns:
        return pl.DataFrame()
    return panel.select([
        "symbol", "trade_date",
        (pl.when(pl.col("pe_ttm") > 0).then(1.0 / pl.col("pe_ttm")).otherwise(None)).alias("value"),
    ])


def roe_factor(panel: pl.DataFrame) -> pl.DataFrame:
    """ROE (TTM) — assumes already joined onto panel."""
    if "roe_ttm" not in panel.columns:
        return pl.DataFrame()
    return panel.select(["symbol", "trade_date", pl.col("roe_ttm").alias("value")])


def momentum_factor(panel: pl.DataFrame, lookback: int = 20, skip: int = 5) -> pl.DataFrame:
    """N-day price momentum, skipping the most recent `skip` days to avoid reversal."""
    return (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            pl.col("close").pct_change(lookback).over("symbol").alias("_mom_full"),
            pl.col("close").pct_change(skip).over("symbol").alias("_mom_skip"),
        )
        .with_columns(((1 + pl.col("_mom_full")) / (1 + pl.col("_mom_skip")) - 1).alias("value"))
        .select(["symbol", "trade_date", "value"])
    )


def reversal_factor(panel: pl.DataFrame, lookback: int = 5) -> pl.DataFrame:
    """Short-term reversal — high recent return ⇒ low expected return (negate)."""
    return (
        panel.sort(["symbol", "trade_date"])
        .with_columns((-pl.col("close").pct_change(lookback).over("symbol")).alias("value"))
        .select(["symbol", "trade_date", "value"])
    )


def volatility_factor(panel: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """Low-vol anomaly: negate realized vol so low-vol stocks rank higher."""
    return (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            (-pl.col("close").pct_change().over("symbol").rolling_std(lookback)).alias("value")
        )
        .select(["symbol", "trade_date", "value"])
    )


def turnover_factor(panel: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """Low-turnover proxy = -avg turnover_rate."""
    if "turnover_rate" not in panel.columns:
        return pl.DataFrame()
    return (
        panel.sort(["symbol", "trade_date"])
        .with_columns((-pl.col("turnover_rate").rolling_mean(lookback).over("symbol")).alias("value"))
        .select(["symbol", "trade_date", "value"])
    )


def amihud_illiquidity(panel: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """Amihud illiquidity — abs return / volume. Negate (more liquid = better)."""
    if "amount" not in panel.columns:
        return pl.DataFrame()
    return (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close").pct_change().abs() / pl.col("amount").clip(lower_bound=1.0))
            .rolling_mean(lookback)
            .over("symbol")
            .alias("_amihud")
        )
        .with_columns((-pl.col("_amihud")).alias("value"))
        .select(["symbol", "trade_date", "value"])
    )


def size_factor(panel: pl.DataFrame) -> pl.DataFrame:
    """Negative log market cap — small-cap tilt."""
    if "total_mv" not in panel.columns:
        return pl.DataFrame()
    return panel.select([
        "symbol", "trade_date",
        (-pl.col("total_mv").log()).alias("value"),
    ])


def register_all(engine) -> None:
    engine.register("bp", bp_factor)
    engine.register("ep", ep_factor)
    engine.register("roe_ttm", roe_factor)
    engine.register("mom_20d", lambda p: momentum_factor(p, 20, 5))
    engine.register("mom_60d", lambda p: momentum_factor(p, 60, 5))
    engine.register("reversal_5d", reversal_factor)
    engine.register("vol_20d", volatility_factor)
    engine.register("turnover_20d", turnover_factor)
    engine.register("amihud_20d", amihud_illiquidity)
    engine.register("size", size_factor)
