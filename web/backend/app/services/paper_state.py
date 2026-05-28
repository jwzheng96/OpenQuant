"""Read paper_state/*.json with mtime-based cache.

Each strategy has its own subdirectory with 6 JSON files:
  cash.json            — { strategy, initial_cash, cash, last_run, saved_at }
  positions.json       — { symbol: { qty, avg_cost, locked_qty, last_locked_date } }
  nav.json             — list of { trade_date, nav, cash, market_value, daily_ret }
  fills.json           — list of { trade_date, symbol, side, qty, price, cost, ... }
  orders.json          — list of { client_id, status, fill_qty, rejected_reason, ... }
  pending_orders.json  — list[ [symbol, signed_qty], ... ]

We cache parsed JSON in-process keyed by (path, mtime). Re-reads happen
automatically when paper_daily.py writes new state.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import get_settings


@dataclass(frozen=True)
class _Entry:
    mtime: float
    data: Any


_cache: dict[str, _Entry] = {}
_lock = Lock()


def _read_cached(path: Path) -> Any:
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    key = str(path)
    with _lock:
        e = _cache.get(key)
        if e is not None and e.mtime == mtime:
            return e.data
        data = json.loads(path.read_text())
        _cache[key] = _Entry(mtime, data)
        return data


# ---------------------------------------------------------------------------- #
# Path helpers                                                                  #
# ---------------------------------------------------------------------------- #


def _state_root() -> Path:
    return get_settings().open_quant_root / "data" / "paper_state"


def state_dir(strategy: str) -> Path:
    return _state_root() / strategy


def list_strategies() -> list[str]:
    """Strategies that have a paper_state directory (i.e., have been backtested)."""
    root = _state_root()
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir() and (p / "cash.json").exists()])


# ---------------------------------------------------------------------------- #
# Individual readers                                                            #
# ---------------------------------------------------------------------------- #


def load_cash(strategy: str) -> dict | None:
    return _read_cached(state_dir(strategy) / "cash.json")


def load_positions(strategy: str) -> dict[str, dict]:
    """{ symbol: { qty, avg_cost, locked_qty, last_locked_date } } — empty dict if missing."""
    return _read_cached(state_dir(strategy) / "positions.json") or {}


def load_nav(strategy: str) -> list[dict]:
    """Time series, oldest first."""
    return _read_cached(state_dir(strategy) / "nav.json") or []


def load_fills(strategy: str) -> list[dict]:
    return _read_cached(state_dir(strategy) / "fills.json") or []


def load_orders(strategy: str) -> list[dict]:
    return _read_cached(state_dir(strategy) / "orders.json") or []


def load_pending(strategy: str) -> list[list]:
    """Raw [symbol, signed_qty] pairs."""
    return _read_cached(state_dir(strategy) / "pending_orders.json") or []


def exists(strategy: str) -> bool:
    return (state_dir(strategy) / "cash.json").exists()


def html_report_path(strategy: str) -> Path | None:
    p = state_dir(strategy) / "report.html"
    return p if p.exists() else None


def clear_cache() -> None:
    """For tests."""
    with _lock:
        _cache.clear()
