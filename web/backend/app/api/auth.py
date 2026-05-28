"""/api/v1/auth — login / logout / me."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.deps import COOKIE_NAME, get_current_user
from app.models.db_models import User
from app.models.schemas import LoginReq, LoginResp, UserResp

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_to_resp(u: User) -> UserResp:
    return UserResp(
        id=str(u.id),
        username=u.username,
        email=u.email,
        role=u.role,
        locale=u.locale,
        is_active=u.is_active,
        last_login=u.last_login.isoformat() if u.last_login else None,
    )


@router.post("/login", response_model=LoginResp)
async def login(
    body: LoginReq,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResp:
    """Verify username/password → issue access token + set HttpOnly cookie."""
    s = get_settings()

    # Match by username (lowercase compare for friendliness)
    stmt = select(User).where(User.username == body.username)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    token = create_access_token(user_id=user.id, username=user.username, role=user.role)
    ttl_seconds = s.access_token_ttl_minutes * 60

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        secure=s.is_prod,           # only HTTPS in prod
        samesite="strict",
        path="/",
    )

    # Update last_login
    await session.execute(
        update(User).where(User.id == user.id).values(last_login=datetime.now(timezone.utc))
    )

    return LoginResp(
        access_token=token,
        expires_in=ttl_seconds,
        user=_user_to_resp(user),
    )


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the access_token cookie. Server side is stateless for now
    (no refresh-token revocation needed until Phase B adds them)."""
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserResp)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserResp:
    """Returns the current authenticated user."""
    return _user_to_resp(user)
