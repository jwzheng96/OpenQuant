"""M4 — Paper trading runner (event-driven, single-day or replay).

Modes:

  --mode replay --from YYYY-MM-DD --to YYYY-MM-DD
     Replay a strategy over historical dates as if it were live; uses stored
     OHLCV to fill PaperBroker orders. Generates fills, positions, P&L.

  --mode live --date YYYY-MM-DD
     One-shot live-style invocation for a single date — pulls the latest panel,
     generates target weights at "open", submits to PaperBroker. Practical use
     is via cron / Prefect schedule (`uni-quant live start --mode paper`).

End of run:
  - Daily NAV / fills written to Postgres (or local SQLite fallback).
  - HTML daily report generated via `monitor.daily_report`.
  - Risk/circuit-breaker checks evaluated; alerts sent on breach.

This is the bridge between research backtest and real broker connectivity.
Same `MultiFactorStrategy` from `strategies.py` drives both.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl
import yaml

from uni_quant.backtest import EventBacktester, BacktestConfig
from uni_quant.data.api import get_data_api
from uni_quant.data.universe import annotate_for_backtest
from uni_quant.execution import (
    Order,
    OrderManagementSystem,
    OrderType,
    PaperBroker,
)
from uni_quant.monitor import AlertManager, Metrics, daily_report
from uni_quant.strategies import FactorWeight, MultiFactorStrategy
from uni_quant.utils import get_logger, load_settings

log = get_logger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_strategy(config_path: Path) -> tuple[MultiFactorStrategy, dict]:
    cfg = yaml.safe_load(Path(config_path).read_text())
    factors = [FactorWeight(**f) for f in cfg.get("factors", [])]
    strat = MultiFactorStrategy(
        factors=factors,
        top_n=cfg.get("selection", {}).get("top_n", 30),
        rebalance_freq=cfg.get("rebalance", {}).get("frequency", "W-FRI"),
        max_weight=cfg.get("risk_overrides", {}).get("max_position_weight", 0.05),
        neutralize_styles=cfg.get("neutralize", {}).get("enabled", False),
    )
    return strat, cfg


@dataclass
class PaperPosition:
    symbol: str
    qty: int
    avg_cost: float
    locked_qty: int = 0


def run_replay(config_path: Path, start: date, end: date, out_dir: Path) -> None:
    """Replay a strategy from `start` to `end` using the event backtester
    (which mirrors what `paper_trade` would do live, day-by-day)."""
    api = get_data_api()
    strat, cfg = load_strategy(config_path)

    panel = api.get_daily(None, start, end, adjust="fwd")
    if panel.is_empty():
        print("no panel data in date range", file=sys.stderr)
        sys.exit(1)
    try:
        sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    except Exception:
        sb = pl.DataFrame({"ts_code": [], "name": []})
    panel = annotate_for_backtest(panel, sb)

    bt = EventBacktester(BacktestConfig(start=start, end=end, initial_cash=1_000_000))
    res = bt.run(panel, strat)

    out_dir.mkdir(parents=True, exist_ok=True)
    nav = [
        {**r, "trade_date": r["trade_date"].isoformat() if hasattr(r["trade_date"], "isoformat") else str(r["trade_date"])}
        for r in res.nav.to_dicts()
    ]
    fills = [
        {"trade_date": f.trade_date.isoformat(), "symbol": f.symbol, "side": f.side,
         "qty": f.qty, "price": f.price, "cost": f.cost}
        for f in res.fills
    ]
    (out_dir / "nav.json").write_text(json.dumps(nav, indent=2, default=str))
    (out_dir / "fills.json").write_text(json.dumps(fills, indent=2, default=str))
    summary = res.summary()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"[replay] {start}..{end} on {cfg.get('name')}", flush=True)
    for k, v in summary.items():
        print(f"  {k}: {v:.4f}", flush=True)

    # Load benchmark (沪深300) for the same period, if available
    benchmark_rows: list[dict] | None = None
    try:
        b = api.query.con.execute(
            "SELECT trade_date, close FROM daily WHERE symbol = '000300.SH' "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [start, end],
        ).pl()
        if not b.is_empty():
            benchmark_rows = [
                {"trade_date": r["trade_date"].isoformat()
                 if hasattr(r["trade_date"], "isoformat") else str(r["trade_date"]),
                 "close": float(r["close"])}
                for r in b.iter_rows(named=True)
            ]
    except Exception as e:
        log.debug(f"benchmark fetch failed: {e}")

    # HTML daily report
    settings = load_settings()
    alerts = AlertManager(
        feishu_webhook=settings.trading.notification.get("feishu_webhook", ""),
        dingtalk_webhook=settings.trading.notification.get("dingtalk_webhook", ""),
        email_cfg=settings.trading.notification.get("email"),
    )
    initial_cash = float(cfg.get("backtest", {}).get("initial_cash", 1_000_000))
    report_path = daily_report(
        nav=nav, fills=fills,
        strategy=cfg.get("name", "unknown"),
        out_path=out_dir / "report.html",
        benchmark=benchmark_rows,
        initial_cash=initial_cash,
    )
    print(f"\nreport: {report_path}", flush=True)

    # Risk check: drawdown breach
    risk = settings.risk.account_level
    max_dd_limit = float(risk.get("max_drawdown_pct", 0.15))
    if abs(summary["max_drawdown"]) >= max_dd_limit:
        msg = (f"max drawdown {summary['max_drawdown']:.2%} "
               f"breached limit {max_dd_limit:.2%} on {cfg.get('name')}")
        log.warning(msg)
        alerts.warn("Drawdown breach", msg)


def run_live_one_day(config_path: Path, target_date: date, broker_kind: str = "paper") -> None:
    """Generate signal for one specific date, submit to broker, log fills.

    For now broker_kind='paper' is the only supported value; QMTBroker/CTPBroker
    require live credentials and a Windows host (QMT) or vnpy CTP installation.
    """
    Metrics.start()  # exposes :9101 for Prometheus
    api = get_data_api()
    strat, cfg = load_strategy(config_path)

    # Use ~6 months of history for factor computation
    from datetime import timedelta
    panel_start = target_date - timedelta(days=240)
    panel = api.get_daily(None, panel_start, target_date, adjust="fwd")
    if panel.is_empty():
        print("no panel data ending at target date", file=sys.stderr)
        sys.exit(1)
    try:
        sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    except Exception:
        sb = pl.DataFrame({"ts_code": [], "name": []})
    panel = annotate_for_backtest(panel, sb)

    # Compute target weights for target_date
    positions: dict = {}
    cash = 1_000_000
    target_w = strat.on_date(target_date, panel, positions, cash)
    if not target_w:
        print(f"[live] no target weights generated for {target_date}", flush=True)
        return

    print(f"[live] target weights for {target_date} ({len(target_w)} stocks):", flush=True)
    for s, w in sorted(target_w.items(), key=lambda x: -x[1])[:15]:
        print(f"  {s}: {w:.4f}", flush=True)

    if broker_kind != "paper":
        print(f"\n[live] broker '{broker_kind}' not implemented yet — exiting", flush=True)
        return

    # Build a price lookup for the PaperBroker
    last_close = {
        r["symbol"]: float(r["close"])
        for r in panel.filter(pl.col("trade_date") == panel["trade_date"].max()).iter_rows(named=True)
    }
    broker = PaperBroker(price_fn=lambda s: last_close.get(s, 0.0))
    broker.connect()
    oms = OrderManagementSystem(broker)

    # Convert weights → share qtys (round down to 100-lot)
    submitted = 0
    for s, w in target_w.items():
        ref = last_close.get(s)
        if not ref or ref <= 0:
            continue
        qty = int((cash * w) / ref // 100 * 100)
        if qty <= 0:
            continue
        o = oms.submit(strategy=cfg.get("name", "unknown"), symbol=s,
                       side="buy", qty=qty, order_type=OrderType.MARKET)
        submitted += 1
        Metrics.orders_submitted.labels(strategy=cfg.get("name", "unknown"), side="buy").inc()
        if o.status.value == "filled":
            Metrics.orders_filled.labels(strategy=cfg.get("name", "unknown"), side="buy").inc()
    print(f"\n[live] submitted {submitted} orders to PaperBroker", flush=True)

    # Print broker positions
    pos = broker.query_positions()
    print(f"\nbroker positions after fills: {len(pos)} symbols", flush=True)
    for s, p in list(pos.items())[:15]:
        print(f"  {s}: qty={p['qty']} avg_cost={p['avg_cost']:.2f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["replay", "live"], required=True)
    ap.add_argument("--config", required=True, help="strategy config yaml")
    ap.add_argument("--from", dest="frm", help="replay: start date YYYY-MM-DD")
    ap.add_argument("--to", help="replay: end date YYYY-MM-DD")
    ap.add_argument("--date", help="live: target date YYYY-MM-DD")
    ap.add_argument("--broker", default="paper")
    ap.add_argument("--out", default="data/paper", help="output dir for replay artifacts")
    args = ap.parse_args()

    if args.mode == "replay":
        if not args.frm or not args.to:
            ap.error("--from and --to required for replay")
        run_replay(Path(args.config), _parse_date(args.frm), _parse_date(args.to),
                   Path(args.out))
    elif args.mode == "live":
        if not args.date:
            ap.error("--date required for live")
        run_live_one_day(Path(args.config), _parse_date(args.date), args.broker)


if __name__ == "__main__":
    main()
