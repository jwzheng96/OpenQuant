"""FastAPI dependencies — auth resolution + RBAC guards.

Auth flow:
  - JWT lives in an HTTP-only cookie called `access_token`.
  - For dev / curl testing, an `Authorization: Bearer <token>` header is
    also accepted.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_session
from app.models.db_models import User, UserRole

COOKIE_NAME = "access_token"


def _extract_token(
    bearer: str | None,
    cookie: str | None,
) -> str | None:
    if bearer and bearer.startswith("Bearer "):
        return bearer.removeprefix("Bearer ").strip()
    return cookie


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Cookie()] = None,
) -> User:
    token = _extract_token(authorization, access_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = decode_token(token)
    if not claims or claims.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        )
    user_id = claims.get("sub")
    try:
        uid = UUID(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad subject")
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found or disabled",
        )
    return user


def require_role(*roles: str):
    """Returns a dependency that ensures the current user has one of the given roles."""
    role_set = set(roles)

    async def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in role_set and user.role != UserRole.admin.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role: {', '.join(roles)} (you are {user.role})",
            )
        return user

    return _check


# Convenience pre-bound deps
require_trader = require_role(UserRole.trader.value)
require_admin = require_role(UserRole.admin.value)
