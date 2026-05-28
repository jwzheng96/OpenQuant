"""Async SQLAlchemy 2.0 session factory (psycopg 3 async)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _make_engine():
    s = get_settings()
    return create_async_engine(
        s.database_url,
        echo=s.db_echo,
        pool_size=s.db_pool_size,
        pool_pre_ping=True,
        future=True,
    )


# Module-level singleton — initialised lazily so tests can override.
_engine: Any = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = _make_engine()
        _sessionmaker = async_sessionmaker(
            _engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a transaction-scoped session."""
    Session = _get_sessionmaker()
    async with Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
