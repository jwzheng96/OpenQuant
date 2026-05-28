"""/api/v1/strategies/*"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.models.schemas import (
    CompareItem,
    FactorWeight,
    StrategyCompareResp,
    StrategyDetailResp,
    StrategyKPI,
    StrategyMetaResp,
    StrategyOverviewRow,
)
from app.services import paper_state, strategies as svc

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _meta_to_schema(m, active: str | None) -> StrategyMetaResp:
    return StrategyMetaResp(
        name=m.name,
        type=m.type,
        factors=[FactorWeight(**f) if isinstance(f, dict) else f for f in m.factors],
        top_n=m.top_n,
        rebalance_freq=m.rebalance_freq,
        benchmark=m.benchmark,
        backtest_start=str(m.backtest_start) if m.backtest_start else None,
        backtest_end=str(m.backtest_end) if m.backtest_end else None,
        enabled=m.enabled,
        is_active=(m.name == active),
        yaml_path=m.yaml_path,
    )


@router.get("", response_model=list[StrategyOverviewRow])
async def list_strategies() -> list[StrategyOverviewRow]:
    """Full strategy list — meta + KPI per row.

    Strategies that haven't been backtested return `kpi.available=False`.
    """
    active = svc.get_active_strategy()
    rows: list[StrategyOverviewRow] = []
    for m in svc.list_yamls():
        kpi_dict = svc.compute_kpis(m.name) if paper_state.exists(m.name) else {"available": False}
        rows.append(StrategyOverviewRow(
            meta=_meta_to_schema(m, active),
            kpi=StrategyKPI(**kpi_dict),
        ))
    return rows


@router.get("/_compare", response_model=StrategyCompareResp)
async def compare_strategies(
    names: str = Query(..., description="Comma-separated strategy names, ≥ 2"),
) -> StrategyCompareResp:
    """A/B (or N-way) comparison: per-strategy KPI + NAV rebased to 100 at the
    common start date + daily-return correlation matrix.
    """
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    if len(name_list) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 strategy names")

    # Load NAVs
    raw_navs: dict[str, list[dict]] = {}
    for n in name_list:
        if not paper_state.exists(n):
            raise HTTPException(status_code=404, detail=f"strategy '{n}' not backtested")
        raw_navs[n] = paper_state.load_nav(n)
        if not raw_navs[n]:
            raise HTTPException(status_code=404, detail=f"strategy '{n}' has empty nav")

    # Find common date range (intersection of dates across all)
    date_sets = [set(r["trade_date"] for r in nv) for nv in raw_navs.values()]
    common_dates = sorted(set.intersection(*date_sets))
    if not common_dates:
        raise HTTPException(status_code=400, detail="no overlapping dates between strategies")
    start = common_dates[0]
    end = common_dates[-1]

    # Rebase each strategy's NAV to 100 at common start
    import numpy as np
    items: list[CompareItem] = []
    daily_rets: dict[str, list[float]] = {}
    for n in name_list:
        nav = raw_navs[n]
        # only keep rows in common range
        nav = [r for r in nav if start <= r["trade_date"] <= end]
        base_nav = next((r["nav"] for r in nav if r["trade_date"] == start), None)
        if base_nav is None or base_nav <= 0:
            continue
        rebased = [
            {"trade_date": r["trade_date"], "value": (r["nav"] / base_nav) * 100}
            for r in nav
        ]
        # Daily returns aligned to common_dates
        rets_by_date = {r["trade_date"]: r.get("daily_ret", 0.0) for r in nav}
        daily_rets[n] = [rets_by_date.get(d, 0.0) for d in common_dates]
        kpi = svc.compute_kpis(n)
        items.append(CompareItem(
            name=n,
            kpi=StrategyKPI(**kpi),
            nav_rebased=rebased,
        ))

    # Correlation matrix
    correlation: list[list[float | None]] = []
    arrs = [np.asarray(daily_rets[it.name], dtype=float) for it in items]
    for i, ai in enumerate(arrs):
        row: list[float | None] = []
        for j, aj in enumerate(arrs):
            if i == j:
                row.append(1.0)
            elif len(ai) < 5 or float(np.std(ai)) == 0 or float(np.std(aj)) == 0:
                row.append(None)
            else:
                rho = float(np.corrcoef(ai, aj)[0, 1])
                row.append(rho)
        correlation.append(row)

    return StrategyCompareResp(
        items=items,
        common_start=start,
        common_end=end,
        correlation=correlation,
    )


@router.get("/{name}", response_model=StrategyDetailResp)
async def get_strategy(name: str) -> StrategyDetailResp:
    """Single strategy: meta + KPI + raw yaml."""
    active = svc.get_active_strategy()
    metas = {m.name: m for m in svc.list_yamls()}
    m = metas.get(name)
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"strategy '{name}' not found")
    kpi_dict = svc.compute_kpis(name) if paper_state.exists(name) else {"available": False}
    yaml_text = svc.get_yaml_text(name) or ""
    return StrategyDetailResp(
        meta=_meta_to_schema(m, active),
        kpi=StrategyKPI(**kpi_dict),
        yaml=yaml_text,
    )
