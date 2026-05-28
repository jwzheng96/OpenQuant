"""/api/v1/data/* — benchmark NAVs, data health, factor inventory."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query

from app.core.config import get_settings
from app.models.schemas import DataHealthResp
from app.services import paper_state, quotes, strategies as strat_svc

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/benchmark")
async def get_benchmark(
    symbol: str = Query("000300.SH"),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> list[dict]:
    start_d = date.fromisoformat(start) if start else date(2024, 1, 1)
    end_d = date.fromisoformat(end) if end else date.today()
    return quotes.benchmark_nav(symbol, start_d, end_d)


@router.get("/health", response_model=DataHealthResp)
async def get_health() -> DataHealthResp:
    factors_dir = get_settings().open_quant_root / "data" / "parquet" / "factors"
    factors = []
    if factors_dir.exists():
        factors = sorted([
            p.name.removeprefix("name=")
            for p in factors_dir.iterdir()
            if p.is_dir() and p.name.startswith("name=")
        ])

    latest = quotes.latest_trade_date()
    symbol_count = None
    try:
        # quick aggregate; reuses lru_cached api connection
        from app.services.quotes import _api
        r = _api().query.con.execute(
            "SELECT COUNT(DISTINCT symbol) FROM daily WHERE trade_date = (SELECT MAX(trade_date) FROM daily)"
        ).fetchone()
        symbol_count = int(r[0]) if r else None
    except Exception:
        pass

    return DataHealthResp(
        daily_latest=latest.isoformat() if latest else None,
        daily_symbol_count=symbol_count,
        paper_strategies=paper_state.list_strategies(),
        active_strategy=strat_svc.get_active_strategy(),
        factors=factors,
    )


@router.get("/stock/{symbol}/history")
async def get_stock_history(symbol: str, days: int = Query(60, ge=1, le=500)) -> list[dict]:
    """OHLCV bars for the stock side-panel kline."""
    return quotes.history(symbol, days=days)


@router.get("/stock/{symbol}/factor/{factor}")
async def get_stock_factor(
    symbol: str, factor: str, days: int = Query(30, ge=1, le=500)
) -> list[dict]:
    return quotes.factor_value(symbol, factor, days=days)
