"""Prefect flows for daily/intraday scheduling.

Schedules (Asia/Shanghai):
  - 17:00  daily_data_sync   : Tushare daily / basic / adj / fundamentals
  - 22:00  factor_calc       : compute & persist factor panel
  - 08:30  signal_gen        : produce target weights for today's open
  - 09:25→15:00  live_loop   : poll OMS, push metrics, run risk checks
  - 15:30  eod_recon         : daily pnl, reconcile, send report

These functions are runnable directly (`python -m open_quant.pipelines daily`) or
served via `prefect deploy`. They are kept thin — heavy logic lives in domain
modules.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

try:
    from prefect import flow, task
except ImportError:  # allow import without prefect at unit-test time
    def flow(fn=None, **_):  # type: ignore
        return fn if fn else lambda f: f

    def task(fn=None, **_):  # type: ignore
        return fn if fn else lambda f: f

from open_quant.data import get_data_api
from open_quant.factors import default_engine
from open_quant.utils import get_logger, load_settings

log = get_logger(__name__)


@task
def sync_stock_basic() -> int:
    return get_data_api().sync_stock_basic()


@task
def sync_daily(start: date, end: date) -> int:
    return get_data_api().sync_daily(start, end)


@task
def sync_adj(start: date, end: date) -> int:
    return get_data_api().sync_adj_factor(start, end)


@task
def sync_daily_basic(start: date, end: date) -> int:
    return get_data_api().sync_daily_basic(start, end)


@flow(name="daily_data_sync")
def daily_data_sync(days_back: int = 1) -> dict[str, int]:
    today = date.today()
    start = today - timedelta(days=days_back)
    return {
        "stock_basic": sync_stock_basic(),
        "daily": sync_daily(start, today),
        "adj": sync_adj(start, today),
        "daily_basic": sync_daily_basic(start, today),
    }


@flow(name="factor_calc")
def factor_calc(lookback_days: int = 250) -> int:
    """Compute the bundled factor library over the recent window and persist."""
    api = get_data_api()
    end = date.today()
    start = end - timedelta(days=int(lookback_days * 1.5))   # calendar days slack
    panel = api.get_daily(None, start, end, adjust="fwd")
    if panel.is_empty():
        log.warning("empty panel, skipping factor calc")
        return 0
    eng = default_engine()
    written = 0
    for name in eng.names():
        r = eng.compute(name, panel)
        if r.data.is_empty():
            continue
        target = api.store.root / "factors" / f"name={name}"
        target.mkdir(parents=True, exist_ok=True)
        r.data.write_parquet(target / "data.parquet")
        written += r.data.height
    log.info(f"factor_calc wrote {written} rows")
    return written


@flow(name="signal_gen")
def signal_gen(strategy_name: str) -> dict:
    """Produce target weights for today and persist to Postgres for OMS."""
    settings = load_settings()
    api = get_data_api()
    panel = api.get_daily(None, date.today() - timedelta(days=120), date.today(), adjust="fwd")
    if panel.is_empty():
        return {}
    # Strategy instantiation is a real concern (loading by name from registry);
    # left as TODO. For now this is a hook for the orchestrator.
    log.info(f"signal_gen for {strategy_name}: panel rows={panel.height}")
    return {}


@flow(name="eod_recon")
def eod_recon() -> None:
    log.info("eod_recon: reconcile positions, write pnl, send report")
    # Postgres + monitor.daily_report wiring would go here.


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "daily":
        print(daily_data_sync(days_back=7))
    elif len(sys.argv) > 1 and sys.argv[1] == "factors":
        print(factor_calc())
    else:
        print("usage: python -m open_quant.pipelines [daily|factors]")
