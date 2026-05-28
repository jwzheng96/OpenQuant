"""API v1 router aggregator.

Sub-routers wired:
  - auth       (Phase 0.5) — login / logout / me
  - strategies (Phase 1)   — list / detail / compare
  - paper      (Phase 1)   — nav / positions / fills / orders / dashboard / stock detail
  - data       (Phase 1)   — benchmark / health / stock history+factor
  - backtest   (Phase 2)   — submit / list / single / cancel
  - events     (Phase 2)   — SSE log streaming

Pending: admin (Phase 3).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.data import router as data_router
from app.api.events import router as events_router
from app.api.paper import router as paper_router
from app.api.strategies import router as strategies_router

api_v1 = APIRouter(prefix="/api/v1")

api_v1.include_router(auth_router)
api_v1.include_router(strategies_router)
api_v1.include_router(paper_router)
api_v1.include_router(data_router)
api_v1.include_router(backtest_router)
api_v1.include_router(events_router)


@api_v1.get("/", tags=["meta"])
async def root() -> dict:
    """API root — lists available v1 endpoints."""
    return {
        "api_version": "v1",
        "status": "phase_2_active",
        "endpoints": {
            "auth":       "/auth/{login|logout|me}",
            "strategies": "/strategies, /strategies/{name}, /strategies/_compare",
            "paper":      "/paper/{name}/{nav|positions|fills|orders|pending|dashboard|report|stock/{symbol}/detail}",
            "data":       "/data/{benchmark|health|stock/{symbol}/history|stock/{symbol}/factor/{factor}}",
            "backtest":   "POST /backtest/run, GET /backtest/tasks, GET /backtest/tasks/{id}, POST /backtest/tasks/{id}/cancel",
            "events":     "GET /events/tasks/{id}  [SSE]",
            "admin":      "TBD (Phase 3)",
        },
    }
