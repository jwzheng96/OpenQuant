"""Liveness + readiness probes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    version: str = "0.1.0"


class ReadyResponse(BaseModel):
    ready: bool
    db_ok: bool


@router.get("/healthz", response_model=HealthResponse, tags=["health"])
async def healthz() -> HealthResponse:
    """Liveness — no dependencies. Always returns 200 if process is up."""
    s = get_settings()
    return HealthResponse(status="ok", app=s.app_name, environment=s.environment)


@router.get(
    "/readyz",
    response_model=ReadyResponse,
    tags=["health"],
    responses={503: {"description": "Not ready"}},
)
async def readyz(session: AsyncSession = Depends(get_session)) -> ReadyResponse:
    """Readiness — checks DB connectivity. 503 if DB unreachable."""
    try:
        await session.execute(text("SELECT 1"))
        return ReadyResponse(ready=True, db_ok=True)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="db unreachable",
        )
