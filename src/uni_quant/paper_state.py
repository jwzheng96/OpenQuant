"""Paper trading state persistence — JSON-backed positions / orders / NAV.

Each strategy has its own directory under `data/paper_state/{strategy}/` with:
  positions.json — symbol → {qty, avg_cost, locked_qty(T+1), entry_date}
  cash.json      — {cash, initial_cash, last_run}
  nav.json       — list of {trade_date, nav, cash, market_value, daily_ret}
  orders.json    — list of all orders (filled / cancelled / rejected)
  fills.json     — list of executed fills (for daily_report)

The state model mirrors the event-driven backtester's Position/Fill so we can
diff paper vs backtest behavior trivially.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass
class PaperPosition:
    symbol: str
    qty: int = 0
    avg_cost: float = 0.0
    locked_qty: int = 0      # T+1 — shares bought today, not yet sellable
    last_locked_date: str | None = None    # date when locked_qty was incremented

    @property
    def sellable_qty(self) -> int:
        return self.qty - self.locked_qty


@dataclass
class PaperFill:
    trade_date: str
    symbol: str
    side: str            # 'buy' / 'sell'
    qty: int
    price: float
    cost: float
    strategy: str
    client_id: str


@dataclass
class PaperOrder:
    client_id: str
    trade_date: str
    symbol: str
    side: str
    qty: int
    order_type: str
    status: str          # pending / filled / partial / cancelled / rejected
    fill_qty: int = 0
    fill_price: float = 0.0
    rejected_reason: str | None = None
    strategy: str = ""


@dataclass
class PaperState:
    strategy: str
    state_dir: Path
    initial_cash: float = 1_000_000.0
    cash: float = 1_000_000.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    nav: list[dict] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)
    orders: list[PaperOrder] = field(default_factory=list)
    last_run: str | None = None
    # Orders generated at day D-1 close, to be filled at day D open
    # Each is (symbol, signed_qty); persisted between daily runs.
    pending_orders: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, strategy: str, root: str | Path = "data/paper_state",
             initial_cash: float = 1_000_000.0) -> "PaperState":
        state_dir = Path(root) / strategy
        state_dir.mkdir(parents=True, exist_ok=True)
        meta_path = state_dir / "cash.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            cash = float(meta.get("cash", initial_cash))
            initial = float(meta.get("initial_cash", initial_cash))
            last_run = meta.get("last_run")
        else:
            cash = initial_cash
            initial = initial_cash
            last_run = None

        positions: dict[str, PaperPosition] = {}
        pos_path = state_dir / "positions.json"
        if pos_path.exists():
            for s, d in json.loads(pos_path.read_text()).items():
                positions[s] = PaperPosition(symbol=s, **d)

        def _load_list(p: Path, cls_: Any) -> list:
            return [cls_(**r) for r in json.loads(p.read_text())] if p.exists() else []

        pending = []
        pend_path = state_dir / "pending_orders.json"
        if pend_path.exists():
            pending = [(s, int(q)) for s, q in json.loads(pend_path.read_text())]

        return cls(
            strategy=strategy, state_dir=state_dir,
            initial_cash=initial, cash=cash, positions=positions,
            nav=json.loads((state_dir / "nav.json").read_text()) if (state_dir / "nav.json").exists() else [],
            fills=_load_list(state_dir / "fills.json", PaperFill),
            orders=_load_list(state_dir / "orders.json", PaperOrder),
            last_run=last_run,
            pending_orders=pending,
        )

    def save(self) -> None:
        d = self.state_dir
        d.mkdir(parents=True, exist_ok=True)
        # cash + meta
        (d / "cash.json").write_text(json.dumps({
            "strategy": self.strategy,
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "last_run": self.last_run,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }, indent=2))
        # positions (drop empty)
        pos_serializable = {
            s: {"qty": p.qty, "avg_cost": p.avg_cost,
                "locked_qty": p.locked_qty, "last_locked_date": p.last_locked_date}
            for s, p in self.positions.items() if p.qty > 0
        }
        (d / "positions.json").write_text(json.dumps(pos_serializable, indent=2))
        (d / "nav.json").write_text(json.dumps(self.nav, indent=2, default=str))
        (d / "fills.json").write_text(json.dumps([asdict(f) for f in self.fills], indent=2))
        (d / "orders.json").write_text(json.dumps([asdict(o) for o in self.orders], indent=2))
        (d / "pending_orders.json").write_text(json.dumps(self.pending_orders, indent=2))

    def unlock_t1(self, current_date: str) -> None:
        """Unlock T+1 shares — any positions whose locked_qty was set BEFORE today
        becomes sellable today."""
        for p in self.positions.values():
            if p.last_locked_date and p.last_locked_date < current_date:
                p.locked_qty = 0
                p.last_locked_date = None

    def market_value(self, prices: dict[str, float]) -> float:
        mv = 0.0
        for s, p in self.positions.items():
            if p.qty <= 0:
                continue
            px = prices.get(s)
            mv += (px if px else p.avg_cost) * p.qty
        return mv

    def already_traded(self, trade_date: str) -> bool:
        return self.last_run is not None and self.last_run >= trade_date

    def apply_fill(self, f: PaperFill) -> None:
        self.fills.append(f)
        p = self.positions.setdefault(f.symbol, PaperPosition(symbol=f.symbol))
        if f.side == "buy":
            new_qty = p.qty + f.qty
            p.avg_cost = (p.avg_cost * p.qty + f.price * f.qty) / max(new_qty, 1)
            p.qty = new_qty
            p.locked_qty += f.qty
            p.last_locked_date = f.trade_date
            self.cash -= f.price * f.qty + f.cost
        else:
            p.qty -= f.qty
            p.locked_qty = max(0, p.locked_qty - f.qty)
            self.cash += f.price * f.qty - f.cost
            if p.qty <= 0:
                # auto-clean fully-closed positions
                self.positions.pop(f.symbol, None)
