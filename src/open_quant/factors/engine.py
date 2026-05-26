"""Factor calculation engine — wraps a panel DataFrame and offers helpers.

Input contract: a polars DataFrame in **long** format with columns at minimum:
    symbol, trade_date, open, high, low, close, volume, amount

Optional joined columns: pe_ttm, pb, ps_ttm, turnover_rate, total_mv, circ_mv,
adj_factor, board, is_st.

Output: a pl.DataFrame with `symbol, trade_date, factor` columns. Factor names
are consistent (no per-call variability) so they can be persisted and joined.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import polars as pl

FactorFn = Callable[[pl.DataFrame], pl.DataFrame]


@dataclass
class FactorResult:
    name: str
    data: pl.DataFrame             # long: symbol, trade_date, value

    def to_wide(self) -> pl.DataFrame:
        return self.data.pivot(values="value", index="trade_date", on="symbol", aggregate_function=None)


class FactorEngine:
    """Stateless engine — registry of named factor functions."""

    def __init__(self):
        self._registry: dict[str, FactorFn] = {}

    def register(self, name: str, fn: FactorFn) -> None:
        if name in self._registry:
            raise ValueError(f"factor {name!r} already registered")
        self._registry[name] = fn

    def names(self) -> list[str]:
        return sorted(self._registry)

    def compute(self, name: str, panel: pl.DataFrame) -> FactorResult:
        if name not in self._registry:
            raise KeyError(f"unknown factor {name!r}")
        df = self._registry[name](panel)
        if df is None or df.is_empty():
            return FactorResult(name=name, data=pl.DataFrame())
        if not {"symbol", "trade_date", "value"}.issubset(df.columns):
            raise ValueError(f"factor {name!r} must return symbol/trade_date/value, got {df.columns}")
        return FactorResult(name=name, data=df.sort(["symbol", "trade_date"]))

    def compute_many(self, names: list[str], panel: pl.DataFrame) -> dict[str, FactorResult]:
        return {n: self.compute(n, panel) for n in names}


def default_engine() -> FactorEngine:
    """Engine pre-loaded with library + Alpha101 + Alpha191 (+ ml_lgb if trained)."""
    from open_quant.factors.alpha101 import register_alpha101
    from open_quant.factors.alpha191 import register_alpha191
    from open_quant.factors.library import register_all
    eng = FactorEngine()
    register_all(eng)
    register_alpha101(eng)
    register_alpha191(eng)
    _maybe_register_ml(eng)
    return eng


def _maybe_register_ml(eng: FactorEngine) -> None:
    """Register any pre-trained ml_* factors found on disk."""
    from pathlib import Path
    import polars as pl

    candidates = list(Path("data/parquet/factors").glob("name=ml_*/data.parquet"))
    for cand in candidates:
        # name=ml_lgb → factor name "ml_lgb"
        factor_name = cand.parent.name.removeprefix("name=")
        try:
            cached = pl.read_parquet(cand)
        except Exception:
            continue
        if cached.is_empty() or not {"symbol", "trade_date", "value"}.issubset(cached.columns):
            continue

        # Bind cached frame via closure
        def _make_fn(df):
            def fn(panel: pl.DataFrame) -> pl.DataFrame:
                return df.filter(
                    pl.col("trade_date").is_in(panel["trade_date"].unique())
                ).select(["symbol", "trade_date", "value"])
            return fn

        try:
            eng.register(factor_name, _make_fn(cached))
        except ValueError:
            pass
