"""Transaction cost model for A-share stocks and Chinese futures.

Stocks:
  - 佣金       commission_rate * notional, with commission_min floor (默认 5 元)
  - 印花税     0.05% on **sell** only (since 2023-08-28 stamp tax was halved to 0.0005)
  - 过户费     0.001% on both sides (沪深统一, since 2023-04)
  - 滑点       Three models: bps / volume_pct / fixed

Futures:
  - 手续费     per-lot or notional fraction (passed in via config)
  - 平今手续费 some products charge extra for close-today (CFFEX 平今 0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class CostConfig:
    # Stock costs
    commission_rate: float = 2.5e-4         # 万 2.5
    commission_min: float = 5.0
    stamp_tax: float = 5.0e-4               # 卖出 0.05%
    transfer_fee: float = 1.0e-5            # 双边 0.001%
    # Futures costs (default: passed in per-instrument)
    futures_commission_rate: float = 2.3e-5
    futures_min_commission: float = 0.0
    futures_close_today_extra: float = 0.0
    # Slippage
    slippage_model: Literal["bps", "volume_pct", "fixed"] = "bps"
    slippage_bps: float = 5.0               # 5 bps default
    slippage_volume_pct: float = 0.1        # assume 10% of daily volume
    slippage_fixed: float = 0.0
    slippage_impact_coef: float = 0.1       # √(participation) * coef * vol

    # Helper to pre-compute side multipliers
    @property
    def buy_taker_pct(self) -> float:
        return self.commission_rate + self.transfer_fee

    @property
    def sell_taker_pct(self) -> float:
        return self.commission_rate + self.transfer_fee + self.stamp_tax


@dataclass
class FillCost:
    commission: float
    stamp_tax: float
    transfer_fee: float
    slippage: float

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee + self.slippage


class CostModel:
    """Compute realistic A-share fill prices and per-trade costs.

    Usage:
        cm = CostModel(CostConfig())
        adj_price, cost = cm.apply(side="buy", price=10.0, qty=1000,
                                   adv_20d=1_000_000, daily_vol=0.02)
    """

    def __init__(self, config: CostConfig | None = None):
        self.cfg = config or CostConfig()

    # -- slippage ----------------------------------------------------------------

    def slippage_per_share(
        self,
        price: float,
        side: str,
        *,
        adv_20d: float | None = None,
        order_qty: float = 0.0,
        daily_vol: float = 0.02,
    ) -> float:
        """Slippage in absolute price terms, signed by `side`.

        For buys we pay a higher price; for sells we receive a lower price.
        """
        sign = 1.0 if side == "buy" else -1.0
        cfg = self.cfg
        if cfg.slippage_model == "bps":
            return sign * price * cfg.slippage_bps * 1e-4
        if cfg.slippage_model == "fixed":
            return sign * cfg.slippage_fixed
        if cfg.slippage_model == "volume_pct":
            # Almgren-style sqrt impact: σ * coef * √(participation)
            if not adv_20d or adv_20d <= 0 or order_qty <= 0:
                return sign * price * cfg.slippage_bps * 1e-4
            participation = min(order_qty / adv_20d, 1.0)
            impact = cfg.slippage_impact_coef * daily_vol * (participation**0.5)
            return sign * price * impact
        raise ValueError(f"unknown slippage_model: {cfg.slippage_model}")

    # -- costs -------------------------------------------------------------------

    def stock_cost(self, side: str, price: float, qty: int) -> FillCost:
        """Compute the four cost components for a stock fill.

        `qty` is unsigned share count. `side` is 'buy' or 'sell'.
        """
        notional = price * qty
        cfg = self.cfg
        commission = max(notional * cfg.commission_rate, cfg.commission_min) if qty > 0 else 0.0
        transfer = notional * cfg.transfer_fee
        stamp = notional * cfg.stamp_tax if side == "sell" else 0.0
        # Slippage cost is the absolute spread we cross — captured in the adjusted
        # fill price, but we surface it here as an explicit cost component so the
        # PnL attribution adds up.
        return FillCost(commission=commission, stamp_tax=stamp, transfer_fee=transfer, slippage=0.0)

    def apply(
        self,
        *,
        side: str,
        price: float,
        qty: int,
        adv_20d: float | None = None,
        daily_vol: float = 0.02,
    ) -> tuple[float, FillCost]:
        """Return (adjusted_fill_price, cost_components).

        `adjusted_fill_price * qty` is the cash actually paid (buy) or received
        (sell) before subtracting commission/tax/transfer fees.
        """
        slip = self.slippage_per_share(
            price, side, adv_20d=adv_20d, order_qty=qty, daily_vol=daily_vol
        )
        adj_price = price + slip
        cost = self.stock_cost(side, adj_price, qty)
        cost.slippage = abs(slip) * qty
        return adj_price, cost

    # -- futures -----------------------------------------------------------------

    def futures_cost(
        self,
        *,
        notional: float,
        is_close_today: bool = False,
        per_lot_fee: float | None = None,
        lots: int = 1,
    ) -> float:
        """Futures commission. Either a per-lot fee or a rate on notional.

        CTP products vary wildly: 股指期货 万 0.23 of notional; 商品 2-10 元 per lot.
        Pass `per_lot_fee` for the latter, leave None for the former.
        """
        cfg = self.cfg
        if per_lot_fee is not None:
            base = per_lot_fee * lots
        else:
            base = notional * cfg.futures_commission_rate
        base = max(base, cfg.futures_min_commission)
        if is_close_today:
            base += cfg.futures_close_today_extra * lots
        return base
