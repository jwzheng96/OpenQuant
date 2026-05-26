"""Event-driven A-share backtester.

Design:
  - Daily bar resolution by default (intraday extensible).
  - Strategy emits **target weights** (or absolute target qty) per trade date.
  - Orders fill at **next day's open**, subject to A-share rules:
        T+1, price limits, suspension, lot size, ST, board.
  - Cost model applied per fill (commission + stamp + transfer + slippage).
  - Daily mark-to-market on close, P&L attribution.

The engine is intentionally simple and synchronous — for parameter sweeps use
the vectorized layer (`backtest.vectorized`) and only run the event engine for
final validation before paper/live deployment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

import numpy as np
import polars as pl

from uni_quant.backtest.ashare_rules import (
    BoardType,
    PriceLimitConfig,
    classify_board,
    is_tradable_at_open,
    round_to_lot,
)
from uni_quant.backtest.cost_model import CostConfig, CostModel
from uni_quant.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------- #
# Data containers                                                              #
# ---------------------------------------------------------------------------- #


@dataclass
class Position:
    symbol: str
    qty: int = 0
    avg_cost: float = 0.0
    locked_qty: int = 0           # T+1: shares bought today (cannot sell)

    @property
    def sellable_qty(self) -> int:
        return self.qty - self.locked_qty


@dataclass
class Fill:
    trade_date: date
    symbol: str
    side: str
    qty: int
    price: float                  # adjusted fill price
    cost: float                   # total cost (commission+tax+transfer+slippage)


@dataclass
class BacktestResult:
    nav: pl.DataFrame             # trade_date, nav, cash, market_value, daily_ret
    fills: list[Fill]
    positions_eod: dict[date, dict[str, Position]]
    config: "BacktestConfig"

    @property
    def total_return(self) -> float:
        if self.nav.is_empty():
            return 0.0
        n = self.nav["nav"].to_numpy()
        return float(n[-1] / n[0] - 1)

    @property
    def sharpe(self) -> float:
        if self.nav.height < 2:
            return 0.0
        r = self.nav["daily_ret"].to_numpy()
        if r.std() == 0:
            return 0.0
        return float(r.mean() / r.std() * np.sqrt(252))

    @property
    def max_drawdown(self) -> float:
        if self.nav.is_empty():
            return 0.0
        n = self.nav["nav"].to_numpy()
        peaks = np.maximum.accumulate(n)
        return float(((n - peaks) / peaks).min())

    def summary(self) -> dict[str, float]:
        return {
            "total_return": self.total_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "n_fills": float(len(self.fills)),
        }


# ---------------------------------------------------------------------------- #
# Strategy protocol                                                            #
# ---------------------------------------------------------------------------- #


class TargetWeightStrategy(Protocol):
    """Strategies emit `target weights` keyed by symbol for each rebalance date."""

    def on_date(
        self,
        d: date,
        panel: pl.DataFrame,
        positions: dict[str, Position],
        cash: float,
    ) -> dict[str, float] | None:
        """Return mapping symbol -> target weight in [0,1]; None means no rebalance."""
        ...


# ---------------------------------------------------------------------------- #
# Config                                                                       #
# ---------------------------------------------------------------------------- #


@dataclass
class BacktestConfig:
    start: date
    end: date
    initial_cash: float = 1_000_000
    cost: CostConfig = field(default_factory=CostConfig)
    limits: PriceLimitConfig = field(default_factory=PriceLimitConfig)
    lot: int = 100
    benchmark: str | None = "000300.SH"


# ---------------------------------------------------------------------------- #
# Engine                                                                       #
# ---------------------------------------------------------------------------- #


class EventBacktester:
    """Iterate trade dates, call the strategy, generate orders, settle fills."""

    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.cm = CostModel(config.cost)

    def run(self, panel: pl.DataFrame, strategy: TargetWeightStrategy) -> BacktestResult:
        cfg = self.cfg
        panel = self._prepare_panel(panel)
        if panel.is_empty():
            raise ValueError("empty panel — no data in date range")

        trade_dates = sorted(panel["trade_date"].unique().to_list())
        cash = cfg.initial_cash
        positions: dict[str, Position] = {}
        fills: list[Fill] = []
        positions_eod: dict[date, dict[str, Position]] = {}
        nav_rows = []
        pending_orders: list[tuple[str, int]] = []      # (symbol, signed_qty) to fill at next open

        for i, td in enumerate(trade_dates):
            day_slice = panel.filter(pl.col("trade_date") == td)
            row_by_symbol = {r["symbol"]: r for r in day_slice.iter_rows(named=True)}

            # 1) Fill pending orders at this day's open
            if pending_orders:
                fills_today = self._settle(td, pending_orders, row_by_symbol, positions, cash_ref=lambda v: None)
                for f in fills_today:
                    fills.append(f)
                cash = self._update_cash_from_fills(cash, fills_today)
                pending_orders = []

            # 2) Unlock T+1: shares bought before today are now sellable
            for p in positions.values():
                p.locked_qty = 0

            # 3) Mark to market
            mv = self._mark_to_market(positions, row_by_symbol)
            nav = cash + mv

            # 4) Strategy emits target weights → next-day orders
            target = strategy.on_date(td, panel.filter(pl.col("trade_date") <= td), positions, cash)
            if target is not None and i + 1 < len(trade_dates):
                next_td = trade_dates[i + 1]
                pending_orders = self._generate_orders(
                    target, positions, cash, mv, row_by_symbol, next_td_row_by_symbol=None
                )

            positions_eod[td] = {s: Position(**vars(p)) for s, p in positions.items() if p.qty > 0}
            daily_ret = 0.0 if not nav_rows else (nav / nav_rows[-1]["nav"] - 1)
            nav_rows.append({
                "trade_date": td, "nav": nav, "cash": cash,
                "market_value": mv, "daily_ret": daily_ret,
            })

        nav_df = pl.DataFrame(nav_rows)
        return BacktestResult(
            nav=nav_df, fills=fills, positions_eod=positions_eod, config=cfg
        )

    # -- internals ---------------------------------------------------------------

    def _prepare_panel(self, panel: pl.DataFrame) -> pl.DataFrame:
        df = panel.filter(
            (pl.col("trade_date") >= self.cfg.start) & (pl.col("trade_date") <= self.cfg.end)
        )
        # Ensure required columns
        required = {"symbol", "trade_date", "open", "close", "pre_close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"panel missing columns: {missing}")
        if "board" not in df.columns:
            df = df.with_columns(
                pl.col("symbol").map_elements(lambda s: classify_board(s).value, return_dtype=pl.Utf8).alias("board")
            )
        if "is_st" not in df.columns:
            df = df.with_columns(pl.lit(False).alias("is_st"))
        if "suspended" not in df.columns:
            df = df.with_columns(pl.lit(False).alias("suspended"))
        return df.sort(["trade_date", "symbol"])

    def _generate_orders(
        self,
        target_weights: dict[str, float],
        positions: dict[str, Position],
        cash: float,
        market_value: float,
        today_rows: dict,
        next_td_row_by_symbol,
    ) -> list[tuple[str, int]]:
        """Translate target weights into integer share orders for the **next** open."""
        nav = cash + market_value
        orders: list[tuple[str, int]] = []
        target_keys = set(target_weights)
        held = set(positions)
        # Sell to zero anything not in target
        for s in held - target_keys:
            p = positions[s]
            if p.sellable_qty > 0:
                orders.append((s, -p.sellable_qty))
        # Adjust to target weight
        for s, w in target_weights.items():
            row = today_rows.get(s)
            if row is None or row["close"] is None:
                continue
            tgt_value = nav * w
            cur_qty = positions[s].qty if s in positions else 0
            cur_value = cur_qty * row["close"]
            delta_value = tgt_value - cur_value
            ref_price = row["close"]
            if delta_value > 0:
                # Buy — round down to lot
                qty = round_to_lot(int(delta_value / ref_price), lot=self.cfg.lot)
                if qty > 0:
                    orders.append((s, qty))
            elif delta_value < 0 and s in positions:
                qty = min(positions[s].sellable_qty, round_to_lot(int(-delta_value / ref_price), lot=self.cfg.lot))
                if qty > 0:
                    orders.append((s, -qty))
        return orders

    def _settle(
        self,
        td: date,
        orders: list[tuple[str, int]],
        rows: dict,
        positions: dict[str, Position],
        cash_ref,
    ) -> list[Fill]:
        fills: list[Fill] = []
        for symbol, signed_qty in orders:
            row = rows.get(symbol)
            if row is None or row.get("suspended"):
                continue
            board = BoardType(row.get("board", "sse_main"))
            st = bool(row.get("is_st", False))
            side = "buy" if signed_qty > 0 else "sell"
            qty = abs(signed_qty)
            if not is_tradable_at_open(
                row["open"], row["pre_close"], board, side=side, suspended=row.get("suspended", False), st=st
            ):
                log.debug(f"{td} {symbol} {side} blocked by limit/suspension")
                continue
            adj_price, cost = self.cm.apply(
                side=side, price=row["open"], qty=qty,
                adv_20d=row.get("amount"), daily_vol=0.02,
            )
            f = Fill(trade_date=td, symbol=symbol, side=side, qty=qty, price=adj_price, cost=cost.total)
            fills.append(f)
            self._apply_fill(positions, f)
        return fills

    @staticmethod
    def _apply_fill(positions: dict[str, Position], f: Fill) -> None:
        p = positions.setdefault(f.symbol, Position(symbol=f.symbol))
        if f.side == "buy":
            new_qty = p.qty + f.qty
            p.avg_cost = (p.avg_cost * p.qty + f.price * f.qty) / max(new_qty, 1)
            p.qty = new_qty
            p.locked_qty += f.qty  # T+1 lock
        else:
            p.qty -= f.qty
            p.locked_qty = max(0, p.locked_qty - f.qty)

    @staticmethod
    def _update_cash_from_fills(cash: float, fills: list[Fill]) -> float:
        for f in fills:
            notional = f.price * f.qty
            if f.side == "buy":
                cash -= notional + f.cost
            else:
                cash += notional - f.cost
        return cash

    @staticmethod
    def _mark_to_market(positions: dict[str, Position], rows: dict) -> float:
        mv = 0.0
        for s, p in positions.items():
            r = rows.get(s)
            if r is None:
                # Stale price (停牌) — use last cost
                mv += p.qty * p.avg_cost
            else:
                mv += p.qty * r["close"]
        return mv
