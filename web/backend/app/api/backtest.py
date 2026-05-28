"""/api/v1/backtest — submit + list + cancel tasks (Phase 2)."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.deps import get_current_user, require_role
from app.models.db_models import Task, User, UserRole
from app.models.schemas import BacktestSubmitReq, TaskResp
from app.services import backtest as svc

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _task_to_resp(t: Task, username: str | None = None) -> TaskResp:
    duration = None
    if t.started_at and t.finished_at:
        duration = (t.finished_at - t.started_at).total_seconds()
    return TaskResp(
        id=str(t.id),
        kind=t.kind,
        status=t.status,
        created_by=str(t.created_by) if t.created_by else None,
        created_by_username=username,
        params=t.params,
        started_at=t.started_at.isoformat() if t.started_at else None,
        finished_at=t.finished_at.isoformat() if t.finished_at else None,
        exit_code=t.exit_code,
        result=t.result,
        created_at=t.created_at.isoformat(),
        duration_seconds=duration,
    )


@router.post(
    "/run",
    response_model=TaskResp,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role(UserRole.trader.value))],
)
async def submit(
    body: BacktestSubmitReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResp:
    """Submit a backtest. Spawns paper_daily.py as a subprocess; returns 202 + task."""
    try:
        task = await svc.submit_backtest(
            session,
            user_id=user.id,
            strategy=body.strategy,
            start=body.start,
            end=body.end,
            initial_cash=body.initial_cash,
            reset=body.reset,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _task_to_resp(task, username=user.username)


@router.get("/tasks", response_model=list[TaskResp])
async def list_tasks(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, le=200),
    status_filter: str | None = Query(None, alias="status"),
    mine_only: bool = Query(False),
) -> list[TaskResp]:
    """Recent tasks. `mine_only=true` filters to caller; otherwise everyone."""
    stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
    if mine_only:
        stmt = stmt.where(Task.created_by == user.id)
    tasks = (await session.execute(stmt)).scalars().all()

    # Resolve usernames once
    user_ids = {t.created_by for t in tasks if t.created_by}
    name_map: dict = {}
    if user_ids:
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        name_map = {uid: name for uid, name in rows.all()}

    return [_task_to_resp(t, username=name_map.get(t.created_by)) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskResp)
async def get_task(
    task_id: UUID,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResp:
    t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="task not found")
    name = None
    if t.created_by:
        name = (
            await session.execute(select(User.username).where(User.id == t.created_by))
        ).scalar_one_or_none()
    return _task_to_resp(t, username=name)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskResp,
    dependencies=[Depends(require_role(UserRole.trader.value))],
)
async def cancel(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResp:
    t = await svc.cancel_task(session, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_to_resp(t, username=user.username)
