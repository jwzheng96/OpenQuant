"""SSE — Server-Sent Events for live task log streaming.

Frontend connects via:
    new EventSource('/api/v1/events/tasks/{id}')
and receives one `message` event per log line, plus a final `done` event
when the task ends. Connection auto-closes after `done`.

Auth: relies on the cookie sent by the browser (HttpOnly access_token).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db.session import get_session
from app.deps import get_current_user
from app.models.db_models import Task, TaskStatus, User

router = APIRouter(prefix="/events", tags=["events"])

TERMINAL_STATES = {
    TaskStatus.success.value,
    TaskStatus.failed.value,
    TaskStatus.cancelled.value,
}


async def _tail_task_log(task_id: UUID) -> AsyncGenerator[dict, None]:
    """Stream the task log + emit terminal event when the task ends."""
    from app.db.session import _get_sessionmaker
    Session = _get_sessionmaker()

    # Lookup log_path
    async with Session() as s:
        t = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not t:
            yield {"event": "error", "data": "task not found"}
            return
        log_path = t.log_path

    if not log_path or not Path(log_path).exists():
        yield {"event": "error", "data": "log file not found"}
        return

    f = open(log_path, "r", encoding="utf-8", errors="replace")
    try:
        # Initial dump — flush whatever's already there
        existing = f.read()
        if existing:
            for line in existing.splitlines():
                yield {"event": "message", "data": line}

        # Tail with periodic terminal-state check
        idle_ticks = 0
        while True:
            line = f.readline()
            if line:
                stripped = line.rstrip("\n")
                if stripped:
                    yield {"event": "message", "data": stripped}
                idle_ticks = 0
                continue

            # No data — sleep + check task terminal status
            await asyncio.sleep(0.4)
            idle_ticks += 1
            if idle_ticks % 3 == 0:    # ~every 1.2s
                async with Session() as s:
                    st = (
                        await s.execute(select(Task.status).where(Task.id == task_id))
                    ).scalar_one_or_none()
                if st in TERMINAL_STATES:
                    # Drain any final buffered output
                    tail = f.read()
                    for ln in tail.splitlines():
                        yield {"event": "message", "data": ln}
                    yield {"event": "done", "data": str(st)}
                    return
    finally:
        f.close()


@router.get("/tasks/{task_id}")
async def stream_task(
    task_id: UUID,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventSourceResponse:
    """SSE: streams task log lines + final `done` event."""
    exists = (await session.execute(select(Task.id).where(Task.id == task_id))).scalar_one_or_none()
    if not exists:
        raise HTTPException(status_code=404, detail="task not found")
    return EventSourceResponse(_tail_task_log(task_id))
