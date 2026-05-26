"""Price adjustment (forward / backward / none) for A-shares.

Storage convention: we always persist **un-adjusted** OHLC + a separate
`adj_factor` column. Adjustment happens at query time.

`adj_factor` (Tushare convention) is monotone non-decreasing in time; the
forward-adjusted close on day t with reference day T (latest) is:

    fwd_close[t] = close[t] * adj_factor[t] / adj_factor[T]

The backward-adjusted close uses adj_factor[t] / adj_factor[0]. Volume is
inversely adjusted (split-adjusted) so that traded notional is preserved.
"""

from __future__ import annotations

from typing import Literal

import polars as pl

AdjustMode = Literal["raw", "fwd", "bwd"]


def adjust_ohlcv(
    df: pl.DataFrame,
    mode: AdjustMode = "fwd",
    *,
    price_cols: tuple[str, ...] = ("open", "high", "low", "close", "pre_close"),
    factor_col: str = "adj_factor",
    volume_col: str = "vol",
    group_by: str = "symbol",
) -> pl.DataFrame:
    """Apply forward/backward adjustment within each `symbol` group.

    - mode='raw': passthrough
    - mode='fwd': prices * factor / factor.last()  (reference = today)
    - mode='bwd': prices * factor / factor.first() (reference = listing day)
    """
    if mode == "raw":
        return df
    if factor_col not in df.columns:
        raise ValueError(f"`{factor_col}` column required for adjustment")

    if mode == "fwd":
        ref_expr = pl.col(factor_col).last()
    else:
        ref_expr = pl.col(factor_col).first()

    exprs = []
    for c in price_cols:
        if c in df.columns:
            exprs.append(
                (pl.col(c) * pl.col(factor_col) / ref_expr.over(group_by)).alias(c)
            )
    if volume_col in df.columns:
        # Volume scales inversely so notional stays consistent
        exprs.append(
            (pl.col(volume_col) * ref_expr.over(group_by) / pl.col(factor_col)).alias(volume_col)
        )
    if not exprs:
        return df
    keep_cols = [c for c in df.columns if c not in price_cols and c != volume_col]
    return df.with_columns(exprs).select(keep_cols + [c for c in df.columns if c not in keep_cols])
