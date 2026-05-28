"""/api/v1/watchlist — per-user stock watchlist.

Simple single-list-per-user model (no folders / no shared lists). Each row
is keyed by (user_id, symbol).

Endpoints:
  GET    /watchlist              list + enrich with name + N-day returns
  POST   /watchlist              add a symbol
  DELETE /watchlist/{symbol}     remove
  PATCH  /watchlist/{symbol}     update note
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.deps import get_current_user
from app.models.db_models import User, Watchlist
from app.models.schemas import WatchlistAddReq, WatchlistItem
from app.services import quotes

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _pct_change(latest: float | None, baseline: float | None) -> float | None:
    if latest is None or baseline is None or baseline == 0:
        return None
    return latest / baseline - 1.0


@router.get("", response_model=list[WatchlistItem])
async def list_watchlist(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[WatchlistItem]:
    rows = (
        await session.execute(
            select(Watchlist)
            .where(Watchlist.user_id == user.id)
            .order_by(Watchlist.added_at.desc())
        )
    ).scalars().all()

    if not rows:
        return []

    symbols = [r.symbol for r in rows]
    names = quotes.names_of(symbols)
    closes = quotes.latest_close(symbols)

    # Enrich with returns — single bulk query per stock (cached internally)
    out: list[WatchlistItem] = []
    for r in rows:
        hist = quotes.history(r.symbol, days=22)   # ≥ 20-day window
        last_close = closes.get(r.symbol)
        prev = hist[-2]["close"] if len(hist) >= 2 else None
        d5 = hist[-6]["close"] if len(hist) >= 6 else None
        d20 = hist[-21]["close"] if len(hist) >= 21 else None
        out.append(
            WatchlistItem(
                symbol=r.symbol,
                name=names.get(r.symbol, r.symbol),
                note=r.note,
                added_at=r.added_at.isoformat(),
                last_close=last_close,
                pct_chg_today=_pct_change(last_close, prev),
                pct_chg_5d=_pct_change(last_close, d5),
                pct_chg_20d=_pct_change(last_close, d20),
            )
        )
    return out


@router.post("", response_model=WatchlistItem, status_code=status.HTTP_201_CREATED)
async def add(
    body: WatchlistAddReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WatchlistItem:
    # Verify the symbol exists in our DB
    if quotes.latest_close([body.symbol]).get(body.symbol) is None:
        raise HTTPException(status_code=400, detail=f"未知股票代码: {body.symbol}")
    existing = (
        await session.execute(
            select(Watchlist).where(
                Watchlist.user_id == user.id, Watchlist.symbol == body.symbol
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        await session.execute(
            insert(Watchlist).values(
                user_id=user.id,
                symbol=body.symbol,
                note=body.note,
                added_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    elif body.note is not None and body.note != existing.note:
        await session.execute(
            update(Watchlist)
            .where(Watchlist.user_id == user.id, Watchlist.symbol == body.symbol)
            .values(note=body.note)
        )
        await session.commit()

    row = (
        await session.execute(
            select(Watchlist).where(
                Watchlist.user_id == user.id, Watchlist.symbol == body.symbol
            )
        )
    ).scalar_one()
    close = quotes.latest_close([body.symbol]).get(body.symbol)
    return WatchlistItem(
        symbol=row.symbol,
        name=quotes.name_of(row.symbol),
        note=row.note,
        added_at=row.added_at.isoformat(),
        last_close=close,
    )


@router.patch("/{symbol}", response_model=WatchlistItem)
async def update_note(
    symbol: str,
    body: WatchlistAddReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WatchlistItem:
    row = (
        await session.execute(
            select(Watchlist).where(
                Watchlist.user_id == user.id, Watchlist.symbol == symbol
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not in watchlist")
    await session.execute(
        update(Watchlist)
        .where(Watchlist.user_id == user.id, Watchlist.symbol == symbol)
        .values(note=body.note)
    )
    await session.commit()
    return WatchlistItem(
        symbol=symbol,
        name=quotes.name_of(symbol),
        note=body.note,
        added_at=row.added_at.isoformat(),
    )


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(
    symbol: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    await session.execute(
        delete(Watchlist).where(
            Watchlist.user_id == user.id, Watchlist.symbol == symbol
        )
    )
    await session.commit()
