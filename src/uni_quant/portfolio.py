"""Risk + portfolio construction in one tight module.

Three pieces:
  1. Cross-sectional neutralization (industry / market-cap / styles).
  2. A minimal Barra-style style-factor risk model (size/momentum/vol/beta/bp).
  3. cvxpy-backed optimizer: long-only, weight cap, turnover cap, optional
     industry/style exposure caps.

`riskfolio-lib` is an option if you want full mean-variance with shrinkage —
but for ~5000-stock A-share universe the simple QP below is fast and predictable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import polars as pl

try:
    import cvxpy as cp
except ImportError:  # cvxpy is optional for non-optimizer flows
    cp = None  # type: ignore


# ---------------------------------------------------------------------------- #
# Neutralization                                                               #
# ---------------------------------------------------------------------------- #


def neutralize_cross_section(
    factor: pl.DataFrame,
    exposures: pl.DataFrame,
    *,
    style_cols: Sequence[str] = ("log_mv",),
    industry_col: str | None = "industry",
) -> pl.DataFrame:
    """Per-date OLS residual neutralization.

    Inputs:
        factor    — long: symbol, trade_date, value
        exposures — long: symbol, trade_date, log_mv, beta, ..., industry
    Output:
        long: symbol, trade_date, value  (residuals after regressing on cols)
    """
    if factor.is_empty() or exposures.is_empty():
        return factor

    merged = factor.join(exposures, on=["symbol", "trade_date"], how="inner").drop_nulls()
    results = []
    for td, group in merged.group_by("trade_date"):
        y = group["value"].to_numpy().astype(np.float64)
        X_parts = [np.ones((len(group), 1))]
        for c in style_cols:
            if c in group.columns:
                X_parts.append(group[c].to_numpy().reshape(-1, 1).astype(np.float64))
        if industry_col and industry_col in group.columns:
            # one-hot industries (drop first to avoid collinearity)
            ind = group[industry_col].to_numpy()
            uniq = sorted(set(ind))[1:]
            ohe = np.array([[1.0 if v == u else 0.0 for u in uniq] for v in ind])
            if ohe.size:
                X_parts.append(ohe)
        X = np.hstack(X_parts)
        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            resid = y - X @ beta
        except np.linalg.LinAlgError:
            resid = y
        results.append(
            pl.DataFrame({
                "symbol": group["symbol"],
                "trade_date": group["trade_date"],
                "value": resid,
            })
        )
    return pl.concat(results) if results else factor


# ---------------------------------------------------------------------------- #
# Style factors (Barra-lite)                                                   #
# ---------------------------------------------------------------------------- #


def style_exposures(panel: pl.DataFrame) -> pl.DataFrame:
    """Compute a compact set of style exposures from the panel.

    Returns: symbol, trade_date, log_mv, beta(252d), mom_120d, vol_60d, bp.
    """
    sp = panel.sort(["symbol", "trade_date"])
    exprs = ["symbol", "trade_date"]
    if "total_mv" in sp.columns:
        exprs.append(pl.col("total_mv").log().alias("log_mv"))
    if "close" in sp.columns:
        exprs.extend([
            pl.col("close").pct_change(120).over("symbol").alias("mom_120d"),
            pl.col("close").pct_change().over("symbol").rolling_std(60).alias("vol_60d"),
        ])
    if "pb" in sp.columns:
        exprs.append(
            pl.when(pl.col("pb") > 0).then(1.0 / pl.col("pb")).otherwise(None).alias("bp")
        )
    return sp.select(exprs)


# ---------------------------------------------------------------------------- #
# Optimizer                                                                    #
# ---------------------------------------------------------------------------- #


@dataclass
class OptimizerConstraints:
    max_weight: float = 0.05
    min_weight: float = 0.0
    gross_target: float = 1.0       # sum of weights == gross_target
    long_only: bool = True
    turnover_cap: float | None = None
    industry_cap: float | None = None   # max absolute industry exposure


def optimize_target_weights(
    alpha: dict[str, float],
    *,
    current: dict[str, float] | None = None,
    industries: dict[str, str] | None = None,
    constraints: OptimizerConstraints | None = None,
) -> dict[str, float]:
    """Solve a QP maximizing alpha · w subject to constraints.

    Falls back to a closed-form rank-based allocation if cvxpy isn't installed.
    """
    if not alpha:
        return {}
    constraints = constraints or OptimizerConstraints()
    symbols = sorted(alpha)
    a = np.array([alpha[s] for s in symbols], dtype=np.float64)

    if cp is None:
        # Equal-weight on top-k where k = floor(1 / max_weight)
        k = max(1, int(1.0 / constraints.max_weight))
        idx = np.argsort(-a)[:k]
        w = np.zeros_like(a)
        w[idx] = constraints.gross_target / k
        return {s: float(w[i]) for i, s in enumerate(symbols)}

    w = cp.Variable(len(symbols))
    cons = [cp.sum(w) == constraints.gross_target, w <= constraints.max_weight]
    if constraints.long_only:
        cons.append(w >= constraints.min_weight)
    if constraints.turnover_cap is not None and current:
        cur = np.array([current.get(s, 0.0) for s in symbols])
        cons.append(cp.norm(w - cur, 1) <= constraints.turnover_cap)
    if constraints.industry_cap is not None and industries:
        for ind in set(industries.values()):
            mask = np.array([1.0 if industries.get(s) == ind else 0.0 for s in symbols])
            cons.append(cp.abs(mask @ w) <= constraints.industry_cap)

    prob = cp.Problem(cp.Maximize(a @ w), cons)
    try:
        prob.solve(solver="OSQP")
    except Exception:
        prob.solve()
    if w.value is None:
        return {}
    return {s: float(max(0.0, w.value[i])) for i, s in enumerate(symbols)}
