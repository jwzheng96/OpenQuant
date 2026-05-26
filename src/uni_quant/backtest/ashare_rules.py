"""A-share market microstructure rules.

Single source of truth for price limits, T+1, board classification, lot size,
suspension, and ST rules — used by both the event-driven backtester and live
execution. Rule numbers reflect the current regime (post-2023 北交所 30%, 创业板
科创板 20%) and are configurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

import numpy as np


class BoardType(str, Enum):
    SSE_MAIN = "sse_main"      # 沪市主板  60xxxx
    SZSE_MAIN = "szse_main"    # 深市主板  00xxxx (含中小板归入主板)
    CHINEXT = "chinext"        # 创业板    30xxxx
    STAR = "star"              # 科创板    688xxx
    BSE = "bse"                # 北交所    8xxxxx / 4xxxxx / 920xxx
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PriceLimitConfig:
    """Price-limit percentage by board and ST status.

    Defaults reflect the current China A-share regime. ST/*ST tickers use a
    narrower band (5%) on main boards; ChiNext/STAR tickers keep their 20%
    even with ST tags per latest rules.
    """

    sse_main: float = 0.10
    szse_main: float = 0.10
    chinext: float = 0.20
    star: float = 0.20
    bse: float = 0.30
    st_main: float = 0.05      # ST on SSE/SZSE main only

    def for_board(self, board: BoardType, st: bool) -> float:
        if st and board in (BoardType.SSE_MAIN, BoardType.SZSE_MAIN):
            return self.st_main
        return {
            BoardType.SSE_MAIN: self.sse_main,
            BoardType.SZSE_MAIN: self.szse_main,
            BoardType.CHINEXT: self.chinext,
            BoardType.STAR: self.star,
            BoardType.BSE: self.bse,
            BoardType.UNKNOWN: self.sse_main,
        }[board]


def classify_board(symbol: str) -> BoardType:
    """Classify A-share ticker by code prefix.

    Accepts both `600000.SH` and `600000.SS` style suffixes; only the leading
    digits matter.
    """
    code = symbol.split(".")[0]
    if not code or not code[0].isdigit():
        return BoardType.UNKNOWN
    if code.startswith("688"):
        return BoardType.STAR
    if code.startswith("60"):
        return BoardType.SSE_MAIN
    if code.startswith("30"):
        return BoardType.CHINEXT
    if code.startswith(("00", "001", "002", "003")):
        return BoardType.SZSE_MAIN
    if code.startswith(("8", "4", "920", "430")):
        return BoardType.BSE
    return BoardType.UNKNOWN


def is_st(name: str | None) -> bool:
    """Detect ST / *ST / SST / S*ST from the listed name."""
    if not name:
        return False
    upper = name.upper().replace(" ", "")
    return "ST" in upper or "*ST" in upper


class LimitBounds(NamedTuple):
    """Price limit bounds based on previous close (un-adjusted)."""

    upper: float
    lower: float


def price_limit_bounds(
    prev_close: float,
    board: BoardType,
    *,
    st: bool = False,
    config: PriceLimitConfig | None = None,
) -> LimitBounds:
    """Compute today's price-limit bounds from yesterday's un-adjusted close.

    A-share rules round to two decimal places. The limit is **inclusive**:
    a trade at exactly `upper` is allowed but the board prints no further bids
    above it.
    """
    cfg = config or PriceLimitConfig()
    pct = cfg.for_board(board, st)
    upper = round(prev_close * (1 + pct) + 1e-9, 2)
    lower = round(prev_close * (1 - pct) - 1e-9, 2)
    return LimitBounds(upper=upper, lower=lower)


def is_limit_up(price: float, prev_close: float, board: BoardType, *, st: bool = False) -> bool:
    return abs(price - price_limit_bounds(prev_close, board, st=st).upper) < 1e-4


def is_limit_down(price: float, prev_close: float, board: BoardType, *, st: bool = False) -> bool:
    return abs(price - price_limit_bounds(prev_close, board, st=st).lower) < 1e-4


def round_to_lot(qty: float, *, lot: int = 100) -> int:
    """Round to nearest 100-share lot, truncating toward zero.

    Sells are exempt from the lot rule for the residual position (last odd lot
    must be sold in one go) — the caller should pass the full residual qty in
    that case and skip rounding.
    """
    if qty <= 0:
        return 0
    return int(qty // lot) * lot


def is_tradable_at_open(
    open_price: float,
    prev_close: float,
    board: BoardType,
    *,
    side: str,                          # "buy" or "sell"
    suspended: bool = False,
    st: bool = False,
) -> bool:
    """Can we get filled at the open?

    - 停牌 → never
    - 一字涨停 (open == upper-limit) → buy fails, sell ok
    - 一字跌停 (open == lower-limit) → sell fails, buy ok
    """
    if suspended:
        return False
    bounds = price_limit_bounds(prev_close, board, st=st)
    if side == "buy" and open_price >= bounds.upper - 1e-4:
        return False
    if side == "sell" and open_price <= bounds.lower + 1e-4:
        return False
    return True


def vectorized_tradable_mask(
    open_prices: np.ndarray,
    prev_closes: np.ndarray,
    boards: np.ndarray,
    st_flags: np.ndarray,
    suspended: np.ndarray,
    *,
    side: str,
    config: PriceLimitConfig | None = None,
) -> np.ndarray:
    """Vectorized version of `is_tradable_at_open` for whole-universe checks.

    `boards` is an array of strings matching BoardType values. Returns a
    boolean mask: True = can fill at open.
    """
    cfg = config or PriceLimitConfig()
    pct = np.empty_like(open_prices, dtype=np.float64)
    # main boards
    main_mask = np.isin(boards, [BoardType.SSE_MAIN.value, BoardType.SZSE_MAIN.value])
    pct[main_mask] = np.where(st_flags[main_mask], cfg.st_main, cfg.sse_main)
    pct[boards == BoardType.CHINEXT.value] = cfg.chinext
    pct[boards == BoardType.STAR.value] = cfg.star
    pct[boards == BoardType.BSE.value] = cfg.bse
    pct[boards == BoardType.UNKNOWN.value] = cfg.sse_main

    upper = np.round(prev_closes * (1 + pct), 2)
    lower = np.round(prev_closes * (1 - pct), 2)

    if side == "buy":
        tradable = open_prices < upper - 1e-4
    elif side == "sell":
        tradable = open_prices > lower + 1e-4
    else:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    return tradable & ~suspended
