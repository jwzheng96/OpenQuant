"""Pydantic schemas — request / response models for /api/v1/*."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------- #
# Strategies                                                                    #
# ---------------------------------------------------------------------------- #


class FactorWeight(BaseModel):
    name: str
    weight: float
    direction: int = 1


class StrategyMetaResp(BaseModel):
    name: str
    type: str
    factors: list[FactorWeight] = []
    top_n: int
    rebalance_freq: str
    benchmark: str
    backtest_start: str | None = None
    backtest_end: str | None = None
    enabled: bool = False
    is_active: bool = False
    yaml_path: str


class StrategyKPI(BaseModel):
    available: bool
    initial_cash: float | None = None
    nav: float | None = None
    cash: float | None = None
    last_run: str | None = None
    first_date: str | None = None
    last_date: str | None = None
    total_return: float | None = None
    annualized_return: float | None = None
    annualized_vol: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    calmar: float | None = None
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    profit_factor: float | None = None
    turnover_ratio: float | None = None
    n_days: int | None = None
    n_fills: int | None = None


class StrategyOverviewRow(BaseModel):
    """One row of the Strategies table — meta + KPI flattened."""
    meta: StrategyMetaResp
    kpi: StrategyKPI


class StrategyDetailResp(BaseModel):
    meta: StrategyMetaResp
    kpi: StrategyKPI
    yaml: str


class CompareItem(BaseModel):
    name: str
    kpi: StrategyKPI
    nav_rebased: list[dict]      # [{trade_date, value}] rebased to 100 at common start


class StrategyCompareResp(BaseModel):
    items: list[CompareItem]
    common_start: str | None = None
    common_end: str | None = None
    correlation: list[list[float | None]] = []  # symmetric, ordered same as items


# ---------------------------------------------------------------------------- #
# Paper state                                                                   #
# ---------------------------------------------------------------------------- #


class NavPoint(BaseModel):
    trade_date: str
    nav: float
    cash: float
    market_value: float
    daily_ret: float


class PositionRow(BaseModel):
    symbol: str
    name: str
    qty: int
    sellable_qty: int
    avg_cost: float
    last_close: float | None = None
    market_value: float | None = None
    pnl_amount: float | None = None
    pnl_pct: float | None = None
    weight: float | None = None
    locked_qty: int = 0


class FillRow(BaseModel):
    trade_date: str
    symbol: str
    name: str
    side: str
    qty: int
    price: float
    amount: float
    cost: float
    strategy: str
    client_id: str | None = None


class OrderRow(BaseModel):
    client_id: str
    trade_date: str
    symbol: str
    name: str
    side: str
    qty: int
    order_type: str
    status: str
    fill_qty: int = 0
    fill_price: float | None = None
    rejected_reason: str | None = None
    strategy: str


class PendingOrder(BaseModel):
    symbol: str
    name: str
    signed_qty: int


# ---------------------------------------------------------------------------- #
# Dashboard (aggregate)                                                         #
# ---------------------------------------------------------------------------- #


class DashboardKpis(BaseModel):
    nav: float
    initial_cash: float
    total_return: float
    today_pnl_amount: float
    today_pnl_pct: float
    sharpe: float | None = None
    max_drawdown: float | None = None
    position_count: int
    cash: float
    cash_pct: float


class MonthlyReturn(BaseModel):
    month: str = Field(description="YYYY-MM")
    ret: float
    end_nav: float | None = None


class DashboardResp(BaseModel):
    strategy: str
    is_active: bool
    last_run: str | None = None
    kpis: DashboardKpis
    nav: list[NavPoint]
    benchmark: list[dict] = []     # [{trade_date, nav}] rebased to 1.0
    monthly: list[MonthlyReturn]
    recent_fills: list[FillRow]    # last 10 fills


# ---------------------------------------------------------------------------- #
# Data health                                                                   #
# ---------------------------------------------------------------------------- #


class DataHealthResp(BaseModel):
    daily_latest: str | None = None
    daily_symbol_count: int | None = None
    paper_strategies: list[str] = []
    active_strategy: str | None = None
    factors: list[str] = []


# ---------------------------------------------------------------------------- #
# Stock detail (Holdings row click side panel)                                  #
# ---------------------------------------------------------------------------- #


class KlineBar(BaseModel):
    trade_date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    vol: int | None = None


class FactorSnapshot(BaseModel):
    name: str
    latest_date: str | None = None
    latest_value: float | None = None
    series: list[dict] = []        # [{trade_date, value}, ...]


class StockDetailResp(BaseModel):
    symbol: str
    name: str
    current_position: PositionRow | None = None
    kline: list[KlineBar] = []
    fills: list[FillRow] = []
    factors: list[FactorSnapshot] = []


# ---------------------------------------------------------------------------- #
# Generic                                                                       #
# ---------------------------------------------------------------------------- #


class ErrorResp(BaseModel):
    code: str
    message: str
    details: dict | None = None


# ---------------------------------------------------------------------------- #
# Auth                                                                          #
# ---------------------------------------------------------------------------- #


class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


class UserResp(BaseModel):
    id: str
    username: str
    email: str
    role: str
    locale: str
    is_active: bool
    last_login: str | None = None


class LoginResp(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int     # seconds
    user: UserResp


# ---------------------------------------------------------------------------- #
# Backtest runner                                                               #
# ---------------------------------------------------------------------------- #


class BacktestSubmitReq(BaseModel):
    strategy: str = Field(min_length=1, max_length=128)
    start: str = Field(description="YYYY-MM-DD")
    end: str = Field(description="YYYY-MM-DD")
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    reset: bool = True


class TaskResp(BaseModel):
    id: str
    kind: str
    status: str
    created_by: str | None = None
    created_by_username: str | None = None
    params: dict
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    result: dict | None = None
    created_at: str
    duration_seconds: float | None = None


# ---------------------------------------------------------------------------- #
# Alerts                                                                        #
# ---------------------------------------------------------------------------- #


class AlertCreateReq(BaseModel):
    severity: str = Field(pattern="^(info|warning|critical)$")
    source: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)
    payload: dict | None = None


class AlertResp(BaseModel):
    id: int
    severity: str
    source: str
    message: str
    payload: dict | None = None
    acked_by: str | None = None
    acked_by_username: str | None = None
    acked_at: str | None = None
    created_at: str


class AlertSummary(BaseModel):
    unacked_count: int
    critical_unacked: int


# ---------------------------------------------------------------------------- #
# Factor research workbench                                                     #
# ---------------------------------------------------------------------------- #


class FactorListItem(BaseModel):
    name: str
    available: bool = True
    mean_rank_ic: float | None = None
    icir: float | None = None
    n_days: int | None = None
    pos_days_pct: float | None = None
    first_date: str | None = None
    last_date: str | None = None


class IcPoint(BaseModel):
    trade_date: str
    ic: float
    rank_ic: float
    n_obs: int


class QuintilePoint(BaseModel):
    trade_date: str
    q1: float
    q2: float
    q3: float
    q4: float
    q5: float
    top_minus_bottom: float
    cum_q1: float
    cum_q2: float
    cum_q3: float
    cum_q4: float
    cum_q5: float
    cum_top_minus_bottom: float


class DecayPoint(BaseModel):
    horizon: int
    mean_rank_ic: float
    icir: float


class FactorDetailResp(BaseModel):
    summary: FactorListItem
    ic_series: list[IcPoint] = []
    quintile_series: list[QuintilePoint] = []
    decay: list[DecayPoint] = []
