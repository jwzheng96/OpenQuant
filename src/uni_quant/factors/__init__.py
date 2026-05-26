"""Factor engine + library + evaluation in a single tight package."""

from uni_quant.factors.engine import FactorEngine, FactorResult, default_engine
from uni_quant.factors.eval import (
    FactorEvalResult,
    evaluate_factor,
    ic_series,
    quantile_returns,
)
from uni_quant.factors.library import (
    bp_factor,
    momentum_factor,
    reversal_factor,
    roe_factor,
    turnover_factor,
    volatility_factor,
)

__all__ = [
    "FactorEngine",
    "FactorResult",
    "default_engine",
    "FactorEvalResult",
    "evaluate_factor",
    "ic_series",
    "quantile_returns",
    "bp_factor",
    "momentum_factor",
    "reversal_factor",
    "roe_factor",
    "turnover_factor",
    "volatility_factor",
]
