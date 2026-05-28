"""/api/v1/alerts — system alerts dashboard.

Auto-source plumbing (cron failures / MDD breaches / data staleness) lands
in Phase 3+. For now this exposes:
  - GET  /alerts            list with filters
  - GET  /alerts/summary    counts (badge in topbar)
  - POST /alerts/{id}/ack   acknowledge as current user
  - POST /alerts            (admin) manual insert for testing
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.deps import get_current_user, require_role
from app.models.db_models import Alert, User, UserRole
from app.models.schemas import AlertCreateReq, AlertResp, AlertSummary

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _alert_to_resp(a: Alert, acker_name: str | None) -> AlertResp:
    return AlertResp(
        id=a.id,
        severity=a.severity,
        source=a.source,
        message=a.message,
        payload=a.payload,
        acked_by=str(a.acked_by) if a.acked_by else None,
        acked_by_username=acker_name,
        acked_at=a.acked_at.isoformat() if a.acked_at else None,
        created_at=a.created_at.isoformat(),
    )


@router.get("/summary", response_model=AlertSummary)
async def summary(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertSummary:
    unacked = (
        await session.execute(
            select(func.count(Alert.id)).where(Alert.acked_at.is_(None))
        )
    ).scalar_one()
    critical = (
        await session.execute(
            select(func.count(Alert.id)).where(
                Alert.acked_at.is_(None), Alert.severity == "critical"
            )
        )
    ).scalar_one()
    return AlertSummary(unacked_count=int(unacked), critical_unacked=int(critical))


@router.get("", response_model=list[AlertResp])
async def list_alerts(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: str | None = Query(None, pattern="^(info|warning|critical)$"),
    only_unacked: bool = Query(False),
    limit: int = Query(100, le=500),
) -> list[AlertResp]:
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if only_unacked:
        stmt = stmt.where(Alert.acked_at.is_(None))
    alerts = (await session.execute(stmt)).scalars().all()

    # Resolve acker usernames once
    acker_ids = {a.acked_by for a in alerts if a.acked_by}
    name_map: dict = {}
    if acker_ids:
        rows = await session.execute(
            select(User.id, User.username).where(User.id.in_(acker_ids))
        )
        name_map = {uid: name for uid, name in rows.all()}

    return [_alert_to_resp(a, name_map.get(a.acked_by)) for a in alerts]


@router.post("/{alert_id}/ack", response_model=AlertResp)
async def ack_alert(
    alert_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertResp:
    a = (await session.execute(select(Alert).where(Alert.id == alert_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert not found")
    if a.acked_at is None:
        await session.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(acked_by=user.id, acked_at=datetime.now(timezone.utc))
        )
        await session.commit()
        a = (await session.execute(select(Alert).where(Alert.id == alert_id))).scalar_one()
    return _alert_to_resp(a, user.username)


@router.post(
    "",
    response_model=AlertResp,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.admin.value))],
)
async def create_alert(
    body: AlertCreateReq,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertResp:
    """Manual insert — for testing the alert flow. Phase 3 will add
    auto-sources (cron failure / MDD breach / data staleness)."""
    a = Alert(
        severity=body.severity,
        source=body.source,
        message=body.message,
        payload=body.payload,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return _alert_to_resp(a, None)
