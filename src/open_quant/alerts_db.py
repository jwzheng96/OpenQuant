"""Tiny helper to write rows into the `alerts` table from any script.

Standalone — no SQLAlchemy / FastAPI dep. Reads DATABASE_URL or falls back
to the dev default. Safe to import from cron scripts (no async loop).

Usage:
    from open_quant.alerts_db import write_alert
    write_alert("critical", "cron.daily_paper", "MDD breach: -25%",
                payload={"mdd": -0.25, "threshold": -0.20})
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "critical"]

# Default matches web/docker-compose.dev.yml + .env.example
_DEFAULT_DB = "postgresql://openquant:openquant@localhost:5433/openquant"


def _conn_url() -> str:
    url = os.environ.get("DATABASE_URL") or _DEFAULT_DB
    # SQLAlchemy uses postgresql+psycopg:// ; psycopg.connect wants postgresql://
    return url.replace("postgresql+psycopg://", "postgresql://")


def write_alert(
    severity: Severity,
    source: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Insert one alert row. Returns True on success, False on any error
    (we never want a sub-failure to bring down the calling cron job)."""
    try:
        import psycopg

        with psycopg.connect(_conn_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO alerts (severity, source, message, payload) "
                    "VALUES (%s, %s, %s, %s::jsonb)",
                    (
                        severity,
                        source[:64],
                        message,
                        json.dumps(payload) if payload else None,
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"alerts_db.write_alert failed: {e}")
        return False
