"""/api/v1/paper/{name}/*  — paper trading state endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.models.schemas import (
    DashboardKpis,
    DashboardResp,
    FactorSnapshot,
    FillRow,
    KlineBar,
    MonthlyReturn,
    NavPoint,
    OrderRow,
    PendingOrder,
    PositionRow,
    StockDetailResp,
)
from app.services import paper_state, quotes, strategies as strat_svc

router = APIRouter(prefix="/paper/{name}", tags=["paper"])


def _ensure_exists(name: str) -> None:
    if not paper_state.exists(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no paper_state for strategy '{name}' (never backtested)",
        )


# ---------------------------------------------------------------------------- #
# Nav                                                                           #
# ---------------------------------------------------------------------------- #


@router.get("/nav", response_model=list[NavPoint])
async def get_nav(
    name: str,
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
) -> list[NavPoint]:
    _ensure_exists(name)
    nav = paper_state.load_nav(name)
    if start:
        nav = [r for r in nav if r["trade_date"] >= start]
    if end:
        nav = [r for r in nav if r["trade_date"] <= end]
    return [NavPoint(**r) for r in nav]


# ---------------------------------------------------------------------------- #
# Positions                                                                     #
# ---------------------------------------------------------------------------- #


@router.get("/positions", response_model=list[PositionRow])
async def get_positions(name: str) -> list[PositionRow]:
    _ensure_exists(name)
    raw = paper_state.load_positions(name)
    if not raw:
        return []
    nav = paper_state.load_nav(name)
    total_nav = float(nav[-1]["nav"]) if nav else None

    symbols = [s for s, p in raw.items() if p.get("qty", 0) > 0]
    names = quotes.names_of(symbols)
    closes = quotes.latest_close(symbols)

    out: list[PositionRow] = []
    for sym, p in raw.items():
        qty = int(p.get("qty", 0))
        if qty <= 0:
            continue
        locked = int(p.get("locked_qty", 0))
        avg = float(p.get("avg_cost", 0))
        close = closes.get(sym)
        mv = close * qty if close else None
        pnl_amt = (close - avg) * qty if close else None
        pnl_pct = (close / avg - 1.0) if (close and avg > 0) else None
        weight = (mv / total_nav) if (mv is not None and total_nav) else None
        out.append(PositionRow(
            symbol=sym,
            name=names.get(sym, sym),
            qty=qty,
            sellable_qty=qty - locked,
            avg_cost=avg,
            last_close=close,
            market_value=mv,
            pnl_amount=pnl_amt,
            pnl_pct=pnl_pct,
            weight=weight,
            locked_qty=locked,
        ))
    # Sort by market value desc
    out.sort(key=lambda r: r.market_value or 0, reverse=True)
    return out


# ---------------------------------------------------------------------------- #
# Fills                                                                         #
# ---------------------------------------------------------------------------- #


@router.get("/fills", response_model=list[FillRow])
async def get_fills(
    name: str,
    symbol: str | None = Query(None),
    side: str | None = Query(None, description="buy | sell"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> list[FillRow]:
    _ensure_exists(name)
    fills = paper_state.load_fills(name)
    # Filter
    def keep(f: dict) -> bool:
        if symbol and f.get("symbol") != symbol:
            return False
        if side and f.get("side") != side:
            return False
        d = f.get("trade_date", "")
        if start and d < start:
            return False
        if end and d > end:
            return False
        return True
    filtered = [f for f in fills if keep(f)]
    # Newest first
    filtered.sort(key=lambda r: r.get("trade_date", ""), reverse=True)
    page = filtered[offset : offset + limit]
    syms = [f["symbol"] for f in page]
    names = quotes.names_of(syms)
    return [
        FillRow(
            trade_date=f["trade_date"],
            symbol=f["symbol"],
            name=names.get(f["symbol"], f["symbol"]),
            side=f["side"],
            qty=int(f["qty"]),
            price=float(f["price"]),
            amount=float(f["price"]) * int(f["qty"]),
            cost=float(f.get("cost", 0)),
            strategy=f.get("strategy", name),
            client_id=f.get("client_id"),
        )
        for f in page
    ]


# ---------------------------------------------------------------------------- #
# Orders                                                                        #
# ---------------------------------------------------------------------------- #


@router.get("/orders", response_model=list[OrderRow])
async def get_orders(
    name: str,
    status_: str | None = Query(None, alias="status"),
    symbol: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> list[OrderRow]:
    _ensure_exists(name)
    raw = paper_state.load_orders(name)
    def keep(o: dict) -> bool:
        if status_ and o.get("status") != status_:
            return False
        if symbol and o.get("symbol") != symbol:
            return False
        d = o.get("trade_date", "")
        if start and d < start:
            return False
        if end and d > end:
            return False
        return True
    flt = [o for o in raw if keep(o)]
    flt.sort(key=lambda r: r.get("trade_date", ""), reverse=True)
    page = flt[offset : offset + limit]
    syms = [o["symbol"] for o in page]
    names = quotes.names_of(syms)
    return [
        OrderRow(
            client_id=o["client_id"],
            trade_date=o["trade_date"],
            symbol=o["symbol"],
            name=names.get(o["symbol"], o["symbol"]),
            side=o.get("side", ""),
            qty=int(o.get("qty", 0)),
            order_type=o.get("order_type", "market"),
            status=o.get("status", "unknown"),
            fill_qty=int(o.get("fill_qty", 0) or 0),
            fill_price=float(o["fill_price"]) if o.get("fill_price") else None,
            rejected_reason=o.get("rejected_reason"),
            strategy=o.get("strategy", name),
        )
        for o in page
    ]


# ---------------------------------------------------------------------------- #
# Pending orders                                                                #
# ---------------------------------------------------------------------------- #


@router.get("/pending", response_model=list[PendingOrder])
async def get_pending(name: str) -> list[PendingOrder]:
    _ensure_exists(name)
    pending = paper_state.load_pending(name)
    syms = [p[0] for p in pending if isinstance(p, list) and len(p) >= 2]
    names = quotes.names_of(syms)
    return [
        PendingOrder(symbol=p[0], name=names.get(p[0], p[0]), signed_qty=int(p[1]))
        for p in pending
        if isinstance(p, list) and len(p) >= 2
    ]


# ---------------------------------------------------------------------------- #
# Dashboard aggregate (one call → all KPIs + NAV + monthly + recent fills)      #
# ---------------------------------------------------------------------------- #


@router.get("/dashboard", response_model=DashboardResp)
async def get_dashboard(name: str) -> DashboardResp:
    _ensure_exists(name)
    nav_raw = paper_state.load_nav(name)
    fills_raw = paper_state.load_fills(name)
    cash = paper_state.load_cash(name) or {}
    positions = paper_state.load_positions(name)

    if not nav_raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no nav data")

    nav_last = float(nav_raw[-1]["nav"])
    nav_prev = float(nav_raw[-2]["nav"]) if len(nav_raw) >= 2 else nav_last
    today_pnl = nav_last - nav_prev
    today_pnl_pct = (nav_last / nav_prev - 1.0) if nav_prev else 0.0
    initial = float(cash.get("initial_cash", 1_000_000.0))
    cash_now = float(cash.get("cash", 0))

    kpi_dict = strat_svc.compute_kpis(name)

    # Benchmark rebased
    bench_sym = "000300.SH"
    from datetime import date as _d
    try:
        start_d = _d.fromisoformat(nav_raw[0]["trade_date"])
        end_d = _d.fromisoformat(nav_raw[-1]["trade_date"])
        bench = quotes.benchmark_nav(bench_sym, start_d, end_d)
    except Exception:
        bench = []

    monthly_raw = strat_svc.monthly_returns(name)
    monthly = [MonthlyReturn(**m) for m in monthly_raw]

    # Recent 10 fills
    syms_recent = [f["symbol"] for f in fills_raw[-10:]]
    names_map = quotes.names_of(syms_recent)
    recent_fills = [
        FillRow(
            trade_date=f["trade_date"],
            symbol=f["symbol"],
            name=names_map.get(f["symbol"], f["symbol"]),
            side=f["side"],
            qty=int(f["qty"]),
            price=float(f["price"]),
            amount=float(f["price"]) * int(f["qty"]),
            cost=float(f.get("cost", 0)),
            strategy=f.get("strategy", name),
            client_id=f.get("client_id"),
        )
        for f in fills_raw[-10:][::-1]    # newest first
    ]

    active = strat_svc.get_active_strategy()

    return DashboardResp(
        strategy=name,
        is_active=(name == active),
        last_run=cash.get("last_run"),
        kpis=DashboardKpis(
            nav=nav_last,
            initial_cash=initial,
            total_return=kpi_dict.get("total_return", 0.0),
            today_pnl_amount=today_pnl,
            today_pnl_pct=today_pnl_pct,
            sharpe=kpi_dict.get("sharpe"),
            max_drawdown=kpi_dict.get("max_drawdown"),
            position_count=len([p for p in positions.values() if p.get("qty", 0) > 0]),
            cash=cash_now,
            cash_pct=(cash_now / nav_last) if nav_last else 0.0,
        ),
        nav=[NavPoint(**r) for r in nav_raw],
        benchmark=bench,
        monthly=monthly,
        recent_fills=recent_fills,
    )


# ---------------------------------------------------------------------------- #
# HTML report passthrough                                                       #
# ---------------------------------------------------------------------------- #


@router.get("/report", include_in_schema=False)
async def get_html_report(name: str):
    p = paper_state.html_report_path(name)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no report")
    return FileResponse(p, media_type="text/html")


# ---------------------------------------------------------------------------- #
# Stock detail (side panel)                                                     #
# ---------------------------------------------------------------------------- #


@router.get("/stock/{symbol}/detail", response_model=StockDetailResp)
async def get_stock_detail(
    name: str, symbol: str,
    kline_days: int = Query(60, ge=10, le=500),
    factor_days: int = Query(30, ge=5, le=500),
) -> StockDetailResp:
    """All info needed for the Holdings row-click side panel.

    Combines kline / fills (for THIS strategy) / position (if held) / factor snapshots.
    """
    _ensure_exists(name)

    # 1. Current position (None if not held)
    raw_pos = paper_state.load_positions(name).get(symbol)
    cur_pos = None
    if raw_pos and int(raw_pos.get("qty", 0)) > 0:
        nav = paper_state.load_nav(name)
        nav_now = float(nav[-1]["nav"]) if nav else None
        close = quotes.latest_close([symbol]).get(symbol)
        qty = int(raw_pos["qty"])
        avg = float(raw_pos.get("avg_cost", 0))
        locked = int(raw_pos.get("locked_qty", 0))
        mv = close * qty if close else None
        pnl_amt = (close - avg) * qty if close else None
        pnl_pct = (close / avg - 1.0) if (close and avg > 0) else None
        cur_pos = PositionRow(
            symbol=symbol,
            name=quotes.name_of(symbol),
            qty=qty,
            sellable_qty=qty - locked,
            avg_cost=avg,
            last_close=close,
            market_value=mv,
            pnl_amount=pnl_amt,
            pnl_pct=pnl_pct,
            weight=(mv / nav_now) if (mv is not None and nav_now) else None,
            locked_qty=locked,
        )

    # 2. K-line
    kline = [KlineBar(**bar) for bar in quotes.history(symbol, days=kline_days)]

    # 3. All fills for this symbol in this strategy
    sym_fills = [f for f in paper_state.load_fills(name) if f.get("symbol") == symbol]
    sym_fills.sort(key=lambda f: f.get("trade_date", ""))
    nm = quotes.name_of(symbol)
    fill_rows = [
        FillRow(
            trade_date=f["trade_date"],
            symbol=symbol,
            name=nm,
            side=f["side"],
            qty=int(f["qty"]),
            price=float(f["price"]),
            amount=float(f["price"]) * int(f["qty"]),
            cost=float(f.get("cost", 0)),
            strategy=f.get("strategy", name),
            client_id=f.get("client_id"),
        )
        for f in sym_fills
    ]

    # 4. Factor snapshots — scan available factor parquets, find ones with values
    factors: list[FactorSnapshot] = []
    factors_root = get_settings().open_quant_root / "data" / "parquet" / "factors"
    if factors_root.exists():
        for d in sorted(factors_root.iterdir()):
            if not (d.is_dir() and d.name.startswith("name=")):
                continue
            fname = d.name.removeprefix("name=")
            series = quotes.factor_value(symbol, fname, days=factor_days)
            if not series:
                continue
            factors.append(FactorSnapshot(
                name=fname,
                latest_date=series[-1]["trade_date"],
                latest_value=series[-1]["value"],
                series=series,
            ))

    return StockDetailResp(
        symbol=symbol,
        name=nm,
        current_position=cur_pos,
        kline=kline,
        fills=fill_rows,
        factors=factors,
    )
