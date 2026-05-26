"""Factor evaluation: IC / RankIC / IR / quantile returns / turnover / decay.

Lightweight implementation using polars — `alphalens` can give richer plots but
adds a heavy pandas dependency for what is straightforwardly a few groupbys.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl


@dataclass
class FactorEvalResult:
    factor_name: str
    ic_mean: float
    ic_std: float
    icir: float
    rank_ic_mean: float
    rank_ic_std: float
    rank_icir: float
    quantile_returns: pl.DataFrame  # cols: trade_date, quantile, ret
    decay: dict[int, float] = field(default_factory=dict)
    turnover: float | None = None

    def summary(self) -> dict[str, float]:
        return {
            "ic": self.ic_mean, "ic_std": self.ic_std, "icir": self.icir,
            "rank_ic": self.rank_ic_mean, "rank_ic_std": self.rank_ic_std,
            "rank_icir": self.rank_icir,
            "turnover": self.turnover or 0.0,
        }


def _forward_return(panel: pl.DataFrame, horizon: int = 1) -> pl.DataFrame:
    """Compute `horizon`-day forward return for each (symbol, trade_date)."""
    return (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1).alias("fwd_ret")
        )
        .select(["symbol", "trade_date", "fwd_ret"])
    )


def ic_series(
    factor: pl.DataFrame,
    panel: pl.DataFrame,
    *,
    horizon: int = 1,
    method: str = "pearson",
) -> pl.DataFrame:
    """Per-date IC series. Returns DataFrame with trade_date, ic columns."""
    fwd = _forward_return(panel, horizon)
    merged = factor.join(fwd, on=["symbol", "trade_date"], how="inner").drop_nulls()
    if merged.is_empty():
        return pl.DataFrame({"trade_date": [], "ic": []})

    if method == "spearman":
        merged = merged.with_columns(
            pl.col("value").rank().over("trade_date").alias("value"),
            pl.col("fwd_ret").rank().over("trade_date").alias("fwd_ret"),
        )

    return (
        merged.group_by("trade_date")
        .agg(pl.corr("value", "fwd_ret").alias("ic"))
        .sort("trade_date")
    )


def quantile_returns(
    factor: pl.DataFrame,
    panel: pl.DataFrame,
    *,
    n_quantiles: int = 5,
    horizon: int = 1,
) -> pl.DataFrame:
    """Per-date per-quantile mean forward return.

    Falls back to rank-based banding when `qcut` can't form `n_quantiles`
    buckets (small universes or ties). Drops dates where fewer than
    `n_quantiles * 2` symbols are present to avoid degenerate buckets.
    """
    fwd = _forward_return(panel, horizon)
    merged = factor.join(fwd, on=["symbol", "trade_date"], how="inner").drop_nulls()
    if merged.is_empty():
        return pl.DataFrame()
    counts = merged.group_by("trade_date").agg(pl.len().alias("_n"))
    valid_dates = counts.filter(pl.col("_n") >= n_quantiles * 2)["trade_date"].to_list()
    if not valid_dates:
        return pl.DataFrame()
    merged = merged.filter(pl.col("trade_date").is_in(valid_dates))
    # Manual rank-based bucketing — robust to ties + tiny universes
    return (
        merged.with_columns(
            (
                ((pl.col("value").rank(method="average").over("trade_date") - 1)
                 / pl.count().over("trade_date") * n_quantiles)
                .floor().clip(0, n_quantiles - 1).cast(pl.Int32).cast(pl.Utf8)
            ).alias("quantile")
        )
        .group_by(["trade_date", "quantile"])
        .agg(pl.col("fwd_ret").mean().alias("ret"))
        .sort(["trade_date", "quantile"])
    )


def evaluate_factor(
    factor: pl.DataFrame,
    panel: pl.DataFrame,
    *,
    name: str = "factor",
    n_quantiles: int = 5,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    primary_horizon: int = 1,
) -> FactorEvalResult:
    """Compute the full evaluation suite."""
    primary_ic = ic_series(factor, panel, horizon=primary_horizon, method="pearson")
    primary_rank_ic = ic_series(factor, panel, horizon=primary_horizon, method="spearman")

    def _agg(s):
        arr = s.drop_nulls().to_numpy()
        if len(arr) == 0:
            return 0.0, 0.0, 0.0
        m = float(np.mean(arr))
        sd = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        ir = m / sd * np.sqrt(252) if sd > 0 else 0.0
        return m, sd, ir

    ic_mean, ic_std, ic_ir = _agg(primary_ic["ic"])
    ric_mean, ric_std, ric_ir = _agg(primary_rank_ic["ic"])

    q_ret = quantile_returns(factor, panel, n_quantiles=n_quantiles, horizon=primary_horizon)

    decay = {}
    for h in horizons:
        s = ic_series(factor, panel, horizon=h, method="spearman")
        m, _, _ = _agg(s["ic"])
        decay[h] = m

    # Rough turnover proxy: top-quantile membership churn
    turnover = None
    try:
        top = (
            factor.sort(["trade_date", "value"], descending=[False, True])
            .group_by("trade_date")
            .agg(pl.col("symbol").head(int(len(factor["symbol"].unique()) / n_quantiles)).alias("top"))
            .sort("trade_date")
        )
        prev = None
        churn = []
        for row in top.iter_rows(named=True):
            cur = set(row["top"])
            if prev is not None and cur:
                churn.append(len(cur - prev) / len(cur))
            prev = cur
        turnover = float(np.mean(churn)) if churn else None
    except Exception:
        pass

    return FactorEvalResult(
        factor_name=name,
        ic_mean=ic_mean, ic_std=ic_std, icir=ic_ir,
        rank_ic_mean=ric_mean, rank_ic_std=ric_std, rank_icir=ric_ir,
        quantile_returns=q_ret,
        decay=decay,
        turnover=turnover,
    )
