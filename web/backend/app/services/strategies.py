"""Scan configs/strategies/*.yaml and compute per-strategy KPIs by reading paper_state.

The active strategy is the one referenced in scripts/daily_paper_cron.sh
(grepped from `CONFIG="..."` line).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings
from app.services import paper_state


def _strategies_dir() -> Path:
    return get_settings().open_quant_root / "configs" / "strategies"


def _cron_path() -> Path:
    return get_settings().open_quant_root / "scripts" / "daily_paper_cron.sh"


# ---------------------------------------------------------------------------- #
# Listing                                                                       #
# ---------------------------------------------------------------------------- #


@dataclass
class StrategyMeta:
    name: str
    type: str
    factors: list[dict]
    top_n: int
    rebalance_freq: str
    benchmark: str
    backtest_start: str | None
    backtest_end: str | None
    enabled: bool
    yaml_path: str


def list_yamls() -> list[StrategyMeta]:
    out: list[StrategyMeta] = []
    d = _strategies_dir()
    if not d.exists():
        return []
    for p in sorted(d.glob("*.yaml")):
        try:
            cfg = yaml.safe_load(p.read_text()) or {}
        except Exception:
            continue
        out.append(StrategyMeta(
            name=cfg.get("name", p.stem),
            type=cfg.get("type", "unknown"),
            factors=cfg.get("factors", []) or [],
            top_n=int((cfg.get("selection") or {}).get("top_n", 30)),
            rebalance_freq=str((cfg.get("rebalance") or {}).get("frequency", "W-FRI")),
            benchmark=str((cfg.get("backtest") or {}).get("benchmark", "000300.SH")),
            backtest_start=(cfg.get("backtest") or {}).get("start"),
            backtest_end=(cfg.get("backtest") or {}).get("end"),
            enabled=bool(cfg.get("enabled", False)),
            yaml_path=str(p.relative_to(get_settings().open_quant_root)),
        ))
    return out


def get_yaml(name: str) -> dict | None:
    """Find yaml by `name` field or filename stem."""
    d = _strategies_dir()
    for p in d.glob("*.yaml"):
        try:
            cfg = yaml.safe_load(p.read_text())
        except Exception:
            continue
        if cfg and (cfg.get("name") == name or p.stem == name):
            return cfg
    return None


def get_yaml_text(name: str) -> str | None:
    d = _strategies_dir()
    for p in d.glob("*.yaml"):
        try:
            cfg = yaml.safe_load(p.read_text())
        except Exception:
            continue
        if cfg and (cfg.get("name") == name or p.stem == name):
            return p.read_text()
    return None


# ---------------------------------------------------------------------------- #
# Active strategy detection                                                     #
# ---------------------------------------------------------------------------- #


def get_active_strategy() -> str | None:
    """Grep scripts/daily_paper_cron.sh for the CONFIG=... line."""
    p = _cron_path()
    if not p.exists():
        return None
    m = re.search(r'CONFIG="?\$REPO/configs/strategies/([^"\s.]+)\.yaml"?', p.read_text())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------- #
# KPI computation (reuse open_quant.monitor)                                    #
# ---------------------------------------------------------------------------- #


def compute_kpis(strategy: str) -> dict:
    """Cumret / Sharpe / MDD / vol / win_rate via open_quant.monitor._compute_stats."""
    from open_quant.monitor import _compute_stats
    nav = paper_state.load_nav(strategy)
    fills = paper_state.load_fills(strategy)
    cash = paper_state.load_cash(strategy) or {}
    if not nav:
        return {"available": False}
    initial = float(cash.get("initial_cash", 1_000_000.0))
    stats = _compute_stats(nav, fills, initial)
    stats["available"] = True
    stats["initial_cash"] = initial
    stats["nav"] = float(nav[-1]["nav"])
    stats["cash"] = float(cash.get("cash", 0))
    stats["last_run"] = cash.get("last_run")
    stats["first_date"] = nav[0]["trade_date"]
    stats["last_date"] = nav[-1]["trade_date"]
    return stats


def monthly_returns(strategy: str) -> list[dict]:
    from open_quant.monitor import _monthly_returns
    nav = paper_state.load_nav(strategy)
    return _monthly_returns(nav) if nav else []


def position_pnl(strategy: str) -> list[dict]:
    """Per-symbol realised P&L via FIFO matching."""
    from open_quant.monitor import _position_pnl_from_fills
    fills = paper_state.load_fills(strategy)
    return _position_pnl_from_fills(fills) if fills else []
