"""/api/v1/factors — research workbench endpoints.

   GET  /factors                 list with summary stats
   GET  /factors/{name}          detail: IC series + quintile + decay
   POST /factors/{name}/rebuild  invalidate + recompute cache (admin)
"""
from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.deps import get_current_user, require_role
from app.models.db_models import User, UserRole
from app.models.schemas import (
    DecayPoint,
    FactorDetailResp,
    FactorListItem,
    IcPoint,
    QuintilePoint,
)
from app.services import factors as svc

router = APIRouter(prefix="/factors", tags=["factors"])


@router.get("", response_model=list[FactorListItem])
async def list_factors(
    _user: Annotated[User, Depends(get_current_user)],
    horizon: int = Query(5, ge=1, le=60),
) -> list[FactorListItem]:
    """List all factors + summary stats. Summary uses cached IC where available."""
    metas = svc.list_factors()
    out: list[FactorListItem] = []
    for m in metas:
        if m.has_cache:
            s = svc.get_factor_summary(m.name, horizon=horizon)
            out.append(FactorListItem(**s))
        else:
            out.append(FactorListItem(name=m.name, available=False))
    return out


@router.get("/{name}", response_model=FactorDetailResp)
async def factor_detail(
    name: str,
    _user: Annotated[User, Depends(get_current_user)],
    horizon: int = Query(5, ge=1, le=60),
) -> FactorDetailResp:
    summary = svc.get_factor_summary(name, horizon=horizon)
    if not summary.get("available"):
        # Force compute on first request
        ic = svc.get_ic_series(name, horizon=horizon, use_cache=False)
        if ic.is_empty():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"factor '{name}' has no usable data",
            )
        summary = svc.get_factor_summary(name, horizon=horizon)

    ic_df = svc.get_ic_series(name, horizon=horizon)
    qdf = svc.get_quintile_returns(name, horizon=horizon)
    decay = svc.get_decay_curve(name)

    def _stringify_date(d) -> str:
        return d.isoformat() if hasattr(d, "isoformat") else str(d)

    ic_series = (
        [
            IcPoint(
                trade_date=_stringify_date(r["trade_date"]),
                ic=float(r["ic"]),
                rank_ic=float(r["rank_ic"]),
                n_obs=int(r["n_obs"]),
            )
            for r in ic_df.iter_rows(named=True)
        ]
        if not ic_df.is_empty()
        else []
    )

    quintile_series = (
        [
            QuintilePoint(
                trade_date=_stringify_date(r["trade_date"]),
                q1=float(r["q1"]),
                q2=float(r["q2"]),
                q3=float(r["q3"]),
                q4=float(r["q4"]),
                q5=float(r["q5"]),
                top_minus_bottom=float(r["top_minus_bottom"]),
                cum_q1=float(r["cum_q1"]),
                cum_q2=float(r["cum_q2"]),
                cum_q3=float(r["cum_q3"]),
                cum_q4=float(r["cum_q4"]),
                cum_q5=float(r["cum_q5"]),
                cum_top_minus_bottom=float(r["cum_top_minus_bottom"]),
            )
            for r in qdf.iter_rows(named=True)
        ]
        if not qdf.is_empty()
        else []
    )

    return FactorDetailResp(
        summary=FactorListItem(**summary),
        ic_series=ic_series,
        quintile_series=quintile_series,
        decay=[DecayPoint(**d) for d in decay],
    )


@router.post(
    "/{name}/rebuild",
    dependencies=[Depends(require_role(UserRole.admin.value))],
)
async def rebuild(name: str) -> dict:
    """Invalidate cached analytics + force recompute (admin only)."""
    cache_dir = svc._analytics_root() / name
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    # Trigger eager recompute
    ic = svc.get_ic_series(name, horizon=5, use_cache=False)
    return {"name": name, "recomputed": True, "ic_days": len(ic)}
