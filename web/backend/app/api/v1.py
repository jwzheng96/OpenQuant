"""API v1 router aggregator.

Sub-routers wired:
  - strategies (Phase 1)  — list / detail / active marker
  - paper      (Phase 1)  — nav / positions / fills / orders / dashboard
  - data       (Phase 1)  — benchmark / health / stock history+factor

Pending: auth (0.5), backtest (2), admin (3).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.data import router as data_router
from app.api.paper import router as paper_router
from app.api.strategies import router as strategies_router

api_v1 = APIRouter(prefix="/api/v1")

api_v1.include_router(strategies_router)
api_v1.include_router(paper_router)
api_v1.include_router(data_router)


@api_v1.get("/", tags=["meta"])
async def root() -> dict:
    """API root — lists available v1 endpoints."""
    return {
        "api_version": "v1",
        "status": "phase_1_active",
        "endpoints": {
            "strategies": "GET /api/v1/strategies, /strategies/{name}",
            "paper":      "GET /api/v1/paper/{name}/{nav|positions|fills|orders|pending|dashboard|report}",
            "data":       "GET /api/v1/data/{benchmark|health|stock/{symbol}/history|stock/{symbol}/factor/{factor}}",
            "auth":       "TBD (Phase 0.5)",
            "backtest":   "TBD (Phase 2)",
            "admin":      "TBD (Phase 3)",
        },
    }
