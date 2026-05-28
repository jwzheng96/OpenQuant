"""Settings — env-var driven via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All app config in one place. Read from env (or .env in dev)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- General ----
    app_name: str = "OpenQuant"
    environment: str = Field(default="dev", description="dev | staging | prod")
    debug: bool = True

    # Path to the project root — used to find configs/strategies/, data/, etc.
    open_quant_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[4],
        description="Filesystem root of the OpenQuant project",
    )

    # ---- Database ----
    database_url: str = Field(
        default="postgresql+psycopg://openquant:openquant@localhost:5433/openquant",
        description="SQLAlchemy URL (use postgresql+psycopg:// for psycopg 3)",
    )
    db_echo: bool = False
    db_pool_size: int = 10

    # ---- Auth ----
    jwt_secret: str = Field(
        default="dev-secret-CHANGE-IN-PROD-min-32-chars-please-1234",
        description="HS256 secret (≥32 chars in prod)",
    )
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 30

    # ---- CORS ----
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:5173",
            "http://localhost:3000",
            "http://openquant.local",
            "https://openquant.local",
        ]
    )

    # ---- Rate limiting ----
    rate_limit_login_per_min: int = 5
    rate_limit_api_per_min: int = 120

    # ---- Backtest queue ----
    backtest_max_concurrent: int = 2
    backtest_timeout_seconds: int = 1800

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
