"""Execution layer: OMS state machine + broker adapters.

The OMS is the source of truth for live orders. It:
  - Generates a client-side `client_id` per order (idempotent retries).
  - Tracks state transitions (pending → submitted → partial → filled/cancelled).
  - Reconciles broker callbacks against local state every minute.
  - Refuses orders that violate pre-trade risk limits.

Broker adapters wrap the venue-specific SDK. We provide:
  - `PaperBroker`   — local simulator (next-bar fill at limit/market).
  - `QMTBroker`     — stub for xtquant (only importable on Windows w/ QMT installed).
  - `CTPBroker`     — stub for vnpy_ctp (works on Linux/Mac with vnpy installed).

The stubs implement the protocol but raise NotImplementedError on connect — they
exist so the rest of the system can be wired up before the brokerage account.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

from open_quant.utils import get_logger

log = get_logger(__name__)


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    client_id: str
    strategy: str
    symbol: str
    side: str                   # "buy" | "sell"
    qty: int
    order_type: OrderType
    price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    broker_id: str | None = None
    filled_qty: int = 0
    avg_price: float | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    rejected_reason: str | None = None


class Broker(Protocol):
    name: str

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def submit(self, order: Order) -> str: ...                # returns broker_id
    def cancel(self, broker_id: str) -> None: ...
    def query_order(self, broker_id: str) -> Order | None: ...
    def query_positions(self) -> dict[str, dict]: ...
    def on_fill(self, callback: Callable[[Order], None]) -> None: ...


# ---------------------------------------------------------------------------- #
# OMS                                                                           #
# ---------------------------------------------------------------------------- #


class OrderManagementSystem:
    """In-memory OMS — Postgres persistence wired in via `on_state_change`."""

    def __init__(self, broker: Broker, risk_check: Callable[[Order], str | None] | None = None):
        self.broker = broker
        self.risk_check = risk_check or (lambda o: None)
        self._orders: dict[str, Order] = {}
        self._listeners: list[Callable[[Order], None]] = []
        self.broker.on_fill(self._handle_broker_fill)

    def add_listener(self, fn: Callable[[Order], None]) -> None:
        self._listeners.append(fn)

    def submit(self, strategy: str, symbol: str, side: str, qty: int,
               order_type: OrderType = OrderType.MARKET, price: float | None = None) -> Order:
        cid = f"{strategy}-{uuid.uuid4().hex[:12]}"
        o = Order(client_id=cid, strategy=strategy, symbol=symbol, side=side,
                  qty=qty, order_type=order_type, price=price)
        rejection = self.risk_check(o)
        if rejection:
            o.status = OrderStatus.REJECTED
            o.rejected_reason = rejection
            self._record(o)
            return o
        self._orders[cid] = o
        try:
            broker_id = self.broker.submit(o)
            o.broker_id = broker_id
            o.status = OrderStatus.SUBMITTED
        except Exception as e:
            o.status = OrderStatus.REJECTED
            o.rejected_reason = f"broker submit failed: {e}"
        self._record(o)
        return o

    def cancel(self, client_id: str) -> None:
        o = self._orders.get(client_id)
        if o is None or o.broker_id is None:
            return
        if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return
        try:
            self.broker.cancel(o.broker_id)
        except Exception as e:
            log.warning(f"cancel failed {client_id}: {e}")

    def reconcile(self) -> None:
        """Poll broker for status of all non-terminal orders."""
        for o in list(self._orders.values()):
            if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
                continue
            if o.broker_id is None:
                continue
            try:
                fresh = self.broker.query_order(o.broker_id)
            except Exception as e:
                log.warning(f"reconcile {o.client_id}: {e}")
                continue
            if fresh and fresh.status != o.status:
                o.status = fresh.status
                o.filled_qty = fresh.filled_qty
                o.avg_price = fresh.avg_price
                o.updated_at = datetime.now()
                self._record(o)

    def _handle_broker_fill(self, fresh: Order) -> None:
        o = self._orders.get(fresh.client_id)
        if o is None:
            log.warning(f"broker fill for unknown client_id {fresh.client_id}")
            return
        o.status = fresh.status
        o.filled_qty = fresh.filled_qty
        o.avg_price = fresh.avg_price
        o.broker_id = fresh.broker_id or o.broker_id
        o.updated_at = datetime.now()
        self._record(o)

    def _record(self, o: Order) -> None:
        for fn in self._listeners:
            try:
                fn(o)
            except Exception:
                log.exception("OMS listener failed")


# ---------------------------------------------------------------------------- #
# Brokers                                                                       #
# ---------------------------------------------------------------------------- #


class PaperBroker:
    """In-memory broker for paper trading. Fills at the next ref price."""

    name = "paper"

    def __init__(self, price_fn: Callable[[str], float]):
        self.price_fn = price_fn
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, dict] = {}
        self._fill_cb: Callable[[Order], None] | None = None

    def connect(self): pass
    def disconnect(self): pass

    def submit(self, order: Order) -> str:
        broker_id = f"paper-{uuid.uuid4().hex[:10]}"
        order.broker_id = broker_id
        self._orders[broker_id] = order
        # Immediate fill at reference price
        ref = self.price_fn(order.symbol)
        if order.order_type == OrderType.LIMIT and order.price is not None:
            if order.side == "buy" and order.price < ref:
                # Not crossing — leave pending
                return broker_id
            if order.side == "sell" and order.price > ref:
                return broker_id
        order.filled_qty = order.qty
        order.avg_price = ref
        order.status = OrderStatus.FILLED
        pos = self._positions.setdefault(order.symbol, {"qty": 0, "avg_cost": 0.0})
        if order.side == "buy":
            new_qty = pos["qty"] + order.qty
            pos["avg_cost"] = (pos["avg_cost"] * pos["qty"] + ref * order.qty) / max(new_qty, 1)
            pos["qty"] = new_qty
        else:
            pos["qty"] -= order.qty
        if self._fill_cb:
            self._fill_cb(order)
        return broker_id

    def cancel(self, broker_id: str) -> None:
        o = self._orders.get(broker_id)
        if o and o.status not in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            o.status = OrderStatus.CANCELLED

    def query_order(self, broker_id: str) -> Order | None:
        return self._orders.get(broker_id)

    def query_positions(self) -> dict[str, dict]:
        return dict(self._positions)

    def on_fill(self, callback: Callable[[Order], None]) -> None:
        self._fill_cb = callback


class QMTBroker:
    """xtquant (QMT/MiniQMT) adapter — Windows-only, needs QMT client running."""

    name = "qmt"

    def __init__(self, qmt_path: str, account_id: str):
        self.qmt_path = qmt_path
        self.account_id = account_id
        self._xt = None
        self._trader = None
        self._fill_cb: Callable[[Order], None] | None = None

    def connect(self) -> None:
        try:
            from xtquant import xtdata, xttrader  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "xtquant is shipped with QMT/MiniQMT; install QMT and add its "
                "userdata_mini/bin64 dir to sys.path"
            ) from e
        self._xt = xttrader.XtQuantTrader(self.qmt_path, int(time.time()))
        if not self._xt.start():
            raise RuntimeError("XtQuantTrader failed to start")
        if not self._xt.connect():
            raise RuntimeError("XtQuantTrader failed to connect")
        # account subscribe / register callbacks would go here

    def disconnect(self) -> None:
        if self._xt:
            self._xt.stop()

    def submit(self, order: Order) -> str:
        raise NotImplementedError("Wire xtquant.order_stock here; needs live QMT")

    def cancel(self, broker_id: str) -> None:
        raise NotImplementedError

    def query_order(self, broker_id: str) -> Order | None:
        raise NotImplementedError

    def query_positions(self) -> dict[str, dict]:
        raise NotImplementedError

    def on_fill(self, callback: Callable[[Order], None]) -> None:
        self._fill_cb = callback


class CTPBroker:
    """vnpy + CTP adapter — works cross-platform with vnpy_ctp installed."""

    name = "ctp"

    def __init__(self, settings: dict):
        self.settings = settings
        self._engine = None
        self._fill_cb: Callable[[Order], None] | None = None

    def connect(self) -> None:
        try:
            from vnpy.event import EventEngine  # noqa: F401
            from vnpy.trader.engine import MainEngine  # noqa: F401
            from vnpy_ctp import CtpGateway  # noqa: F401
        except ImportError as e:
            raise RuntimeError("install vnpy + vnpy_ctp for CTP support") from e
        # Real wire-up here; left as stub.

    def disconnect(self) -> None: pass
    def submit(self, order: Order) -> str: raise NotImplementedError
    def cancel(self, broker_id: str) -> None: raise NotImplementedError
    def query_order(self, broker_id: str) -> Order | None: raise NotImplementedError
    def query_positions(self) -> dict[str, dict]: raise NotImplementedError
    def on_fill(self, callback): self._fill_cb = callback
