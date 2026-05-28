"""FastAPI entrypoint.

Run from project root:
    uvicorn web.backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.health import router as health_router
from app.api.v1 import api_v1
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(level="DEBUG" if settings.debug else "INFO", json_logs=settings.is_prod)
log = get_logger("openquant.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app.starting", environment=settings.environment, debug=settings.debug)
    yield
    log.info("app.shutting_down")


limiter = Limiter(key_func=get_remote_address)


app = FastAPI(
    title=f"{settings.app_name} Web API",
    description="Industrial-grade quant trading dashboard backend.",
    version="0.1.0",
    docs_url="/api/docs" if settings.debug else None,    # hide in prod
    redoc_url="/api/redoc" if settings.debug else None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---- Middleware ----
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ---- Routers ----
app.include_router(health_router)        # /healthz, /readyz
app.include_router(api_v1)               # /api/v1/*


@app.get("/", include_in_schema=False)
async def root_redirect() -> dict:
    """Friendly landing for browsers hitting /."""
    return {
        "name": settings.app_name,
        "api": "/api/v1",
        "docs": "/api/docs" if settings.debug else "disabled in prod",
        "health": "/healthz",
    }
