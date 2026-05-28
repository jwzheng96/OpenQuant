"""Backtest task lifecycle.

Submitting a backtest:
  1. Validate strategy yaml exists.
  2. Insert row in `tasks` table (status=queued, log_path resolved).
  3. Fire-and-forget asyncio.create_task to run paper_daily.py as subprocess.
  4. Subprocess stdout/stderr piped to log file; task row updated on
     start / finish / failure.

Cancelling:
  - Sets status=cancelled in DB and kills the subprocess via PID.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import _get_sessionmaker
from app.models.db_models import Task, TaskKind, TaskStatus

log = get_logger("backtest")

# In-process registry of running task → subprocess PID, for cancellation
_RUNNING_PIDS: dict[str, int] = {}


def _task_log_dir() -> Path:
    p = get_settings().open_quant_root / "data" / "task_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def submit_backtest(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    strategy: str,
    start: str,
    end: str,
    initial_cash: float = 1_000_000.0,
    reset: bool = True,
) -> Task:
    s = get_settings()
    yaml_path = s.open_quant_root / "configs" / "strategies" / f"{strategy}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"strategy yaml not found: {strategy}")

    tid = uuid4()
    log_path = str(_task_log_dir() / f"{tid}.log")
    Path(log_path).touch()  # so SSE can tail from empty

    task = Task(
        id=tid,
        kind=TaskKind.backtest.value,
        status=TaskStatus.queued.value,
        created_by=user_id,
        params={
            "strategy": strategy,
            "from": start,
            "to": end,
            "initial_cash": initial_cash,
            "reset": reset,
        },
        log_path=log_path,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    # Fire-and-forget runner
    asyncio.create_task(
        _run_backtest_subprocess(
            tid, s.open_quant_root, strategy, start, end, initial_cash, reset, log_path
        )
    )
    return task


async def _set_status(tid: UUID, **fields) -> None:
    Session = _get_sessionmaker()
    async with Session() as s:
        await s.execute(update(Task).where(Task.id == tid).values(**fields))
        await s.commit()


async def _run_backtest_subprocess(
    tid: UUID,
    root: Path,
    strategy: str,
    start: str,
    end: str,
    initial_cash: float,
    reset: bool,
    log_path: str,
) -> None:
    cmd = [
        sys.executable,
        "scripts/paper_daily.py",
        "--config", f"configs/strategies/{strategy}.yaml",
        "--from", start,
        "--to", end,
        "--initial-cash", str(initial_cash),
    ]
    if reset:
        cmd.append("--reset")

    env = os.environ.copy()
    # Reuse the main project venv; PYTHONPATH includes src/ so open_quant resolves
    env["PYTHONPATH"] = f"{root}/src:" + env.get("PYTHONPATH", "")
    # Disable Clash proxy for sub-process — AkShare/EM may go thru this
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"

    log.info("backtest.starting", task_id=str(tid), cmd=" ".join(cmd))
    await _set_status(tid, status=TaskStatus.running.value,
                      started_at=datetime.now(timezone.utc))

    try:
        with open(log_path, "ab", buffering=0) as logf:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(root),
                stdout=logf,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            _RUNNING_PIDS[str(tid)] = proc.pid
            try:
                exit_code = await proc.wait()
            finally:
                _RUNNING_PIDS.pop(str(tid), None)
    except Exception as e:
        log.exception("backtest.crashed", task_id=str(tid), error=str(e))
        await _set_status(
            tid,
            status=TaskStatus.failed.value,
            finished_at=datetime.now(timezone.utc),
            exit_code=-1,
            result={"error": str(e)},
        )
        return

    # Final state
    final_status = TaskStatus.success.value if exit_code == 0 else TaskStatus.failed.value
    # Cancelled?  We set the status in DB before kill — recheck.
    Session = _get_sessionmaker()
    async with Session() as s:
        cur = (
            await s.execute(select(Task.status).where(Task.id == tid))
        ).scalar_one_or_none()
    if cur == TaskStatus.cancelled.value:
        final_status = TaskStatus.cancelled.value

    # Compute result summary if success — read paper_state cash + nav last
    result_summary: dict = {"exit_code": exit_code}
    if final_status == TaskStatus.success.value:
        try:
            import json as _json
            ps = root / "data" / "paper_state" / strategy
            cash = _json.loads((ps / "cash.json").read_text())
            nav = _json.loads((ps / "nav.json").read_text())
            if nav:
                initial = float(cash.get("initial_cash", initial_cash))
                final_nav = float(nav[-1]["nav"])
                result_summary.update({
                    "nav": final_nav,
                    "initial_cash": initial,
                    "total_return": (final_nav / initial - 1.0) if initial else None,
                    "n_days": len(nav),
                })
        except Exception:
            pass

    await _set_status(
        tid,
        status=final_status,
        finished_at=datetime.now(timezone.utc),
        exit_code=exit_code,
        result=result_summary,
    )
    log.info("backtest.finished", task_id=str(tid), status=final_status, exit=exit_code)


async def cancel_task(session: AsyncSession, tid: UUID) -> Task | None:
    """Mark cancelled in DB + SIGTERM the subprocess (if running)."""
    task = (await session.execute(select(Task).where(Task.id == tid))).scalar_one_or_none()
    if task is None:
        return None
    if task.status not in (TaskStatus.queued.value, TaskStatus.running.value):
        return task

    pid = _RUNNING_PIDS.get(str(tid))
    if pid:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)

    await session.execute(
        update(Task).where(Task.id == tid).values(
            status=TaskStatus.cancelled.value,
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    await session.refresh(task)
    return task
