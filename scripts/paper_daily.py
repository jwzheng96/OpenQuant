"""Daily paper trading runner — stateful, idempotent, A股 rules enforced.

Modes:

  --once DATE        Run a single trading day. State persists between runs.
  --range FROM TO    Run each trading day in [FROM, TO] sequentially.

Each day:
  1. Load prior state (positions, cash, locked_qty).
  2. Unlock T+1 shares from prior day.
  3. Compute target weights via strategy.on_date.
  4. Generate orders (next-day open style — for daily replay this means using
     the date's own open price; matches the backtester semantics).
  5. Apply A股 rules: T+1, lot size, limit-up/down check, suspension skip.
  6. Apply cost model.
  7. Persist updated state + log fills.

At end, generate the rich HTML report and a divergence diff vs backtest.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import yaml

from uni_quant.backtest.ashare_rules import (
    BoardType,
    PriceLimitConfig,
    classify_board,
    is_tradable_at_open,
    round_to_lot,
)
from uni_quant.backtest.cost_model import CostConfig, CostModel
from uni_quant.data.api import get_data_api
from uni_quant.data.universe import annotate_for_backtest
from uni_quant.monitor import daily_report
from uni_quant.paper_state import PaperFill, PaperOrder, PaperPosition, PaperState
from uni_quant.strategies import FactorWeight, MultiFactorStrategy
from uni_quant.utils import get_logger

log = get_logger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_strategy(config_path: Path) -> tuple[MultiFactorStrategy, dict]:
    cfg = yaml.safe_load(Path(config_path).read_text())
    factors = [FactorWeight(**f) for f in cfg.get("factors", [])]

    # Build optional qualitative overlay if enabled in yaml
    overlay = None
    overlay_log = []
    overlay_cfg = cfg.get("qualitative_overlay") or {}
    if overlay_cfg.get("enabled"):
        from uni_quant.agents import QualitativeOverlay
        log.info("qualitative_overlay enabled — building agent layer")
        overlay = QualitativeOverlay.from_config(overlay_cfg)

        def _log_decisions(d, decisions):
            kept = sum(1 for x in decisions.values() if x.action == "KEEP")
            dropped = sum(1 for x in decisions.values() if x.action == "DROP")
            overlay_log.append({"date": str(d), "kept": kept, "dropped": dropped,
                                "drops": {s: x.rationale[:120] for s, x in decisions.items()
                                          if x.action == "DROP"}})
            log.info(f"overlay {d}: kept={kept} dropped={dropped}")

        cfg["_overlay_log"] = overlay_log  # so caller can persist

    strat = MultiFactorStrategy(
        factors=factors,
        top_n=cfg.get("selection", {}).get("top_n", 30),
        rebalance_freq=cfg.get("rebalance", {}).get("frequency", "W-FRI"),
        max_weight=cfg.get("risk_overrides", {}).get("max_position_weight", 0.05),
        neutralize_styles=cfg.get("neutralize", {}).get("enabled", False),
        qualitative_overlay=overlay,
        on_overlay_decisions=(_log_decisions if overlay else None),
    )
    return strat, cfg


def run_one_day(
    api,
    strategy: MultiFactorStrategy,
    cfg: dict,
    target_date: date,
    state: PaperState,
    cost_model: CostModel,
    limits_cfg: PriceLimitConfig,
) -> bool:
    """Process a single trading day. Returns True if traded, False if skipped."""
    if state.already_traded(target_date.isoformat()):
        log.debug(f"{target_date}: already traded, skip")
        return False

    # Pull recent panel (~250 days history needed for factor warm-up)
    panel_start = target_date - timedelta(days=400)
    panel = api.get_daily(None, panel_start, target_date, adjust="fwd")
    if panel.is_empty():
        log.warning(f"{target_date}: no panel")
        return False

    # Check that target_date is actually a trading day for the universe
    day_rows = panel.filter(pl.col("trade_date") == target_date)
    if day_rows.is_empty():
        return False  # non-trading day

    try:
        sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    except Exception:
        sb = panel.select(["symbol"]).unique().with_columns(pl.lit("UNKNOWN").alias("name"))
    panel = annotate_for_backtest(panel, sb)

    # Build price lookup for today (open + close)
    today_rows = panel.filter(pl.col("trade_date") == target_date)
    by_sym = {r["symbol"]: r for r in today_rows.iter_rows(named=True)}

    # Correct timing model (matches event_engine.py exactly):
    #   1. Settle yesterday's pending_orders at today's open
    #   2. Unlock T+1 from prior days
    #   3. MTM at today's close to record NAV
    #   4. Strategy computes target weights using info through today close
    #   5. Generate new pending_orders for tomorrow's open
    #
    # This is correct: signal uses today close → execute next-day open.
    # No future info leakage.

    pending = state.pending_orders
    state.pending_orders = []  # consumed; new ones generated below

    # 1) Settle yesterday's pending_orders at today's open
    for symbol, signed_qty in pending:
        row = by_sym.get(symbol)
        if row is None:
            continue
        board = BoardType(row.get("board", "sse_main"))
        st = bool(row.get("is_st", False))
        side = "buy" if signed_qty > 0 else "sell"
        qty = abs(signed_qty)
        cid = f"{cfg.get('name', 'paper')}-{target_date}-{symbol}-{uuid.uuid4().hex[:6]}"

        prev_close = row.get("pre_close")
        open_px = row.get("open")
        if prev_close is None or open_px is None:
            state.orders.append(PaperOrder(
                client_id=cid, trade_date=target_date.isoformat(),
                symbol=symbol, side=side, qty=qty, order_type="market",
                status="rejected", rejected_reason="missing_price_data",
                strategy=cfg.get("name", "paper"),
            ))
            continue
        if not is_tradable_at_open(
            open_px, prev_close, board, side=side,
            suspended=bool(row.get("suspended", False)), st=st
        ):
            state.orders.append(PaperOrder(
                client_id=cid, trade_date=target_date.isoformat(),
                symbol=symbol, side=side, qty=qty, order_type="market",
                status="rejected", rejected_reason="limit/suspend",
                strategy=cfg.get("name", "paper"),
            ))
            continue

        adj_price, cost = cost_model.apply(
            side=side, price=row["open"], qty=qty,
            adv_20d=row.get("amount"), daily_vol=0.02,
        )

        # Check cash for buys
        if side == "buy" and (adj_price * qty + cost.total) > state.cash + 1e-2:
            state.orders.append(PaperOrder(
                client_id=cid, trade_date=target_date.isoformat(),
                symbol=symbol, side=side, qty=qty, order_type="market",
                status="rejected", rejected_reason="insufficient_cash",
                strategy=cfg.get("name", "paper"),
            ))
            continue

        fill = PaperFill(
            trade_date=target_date.isoformat(), symbol=symbol, side=side,
            qty=qty, price=adj_price, cost=cost.total,
            strategy=cfg.get("name", "paper"), client_id=cid,
        )
        state.apply_fill(fill)
        state.orders.append(PaperOrder(
            client_id=cid, trade_date=target_date.isoformat(),
            symbol=symbol, side=side, qty=qty, order_type="market",
            status="filled", fill_qty=qty, fill_price=adj_price,
            strategy=cfg.get("name", "paper"),
        ))

    # 2) Unlock T+1 — shares bought before today become sellable
    state.unlock_t1(target_date.isoformat())

    # 3) MTM at today's close + record NAV
    close_prices = {s: r["close"] for s, r in by_sym.items()}
    mv = state.market_value(close_prices)
    nav_close = state.cash + mv
    daily_ret = (nav_close / state.nav[-1]["nav"] - 1) if state.nav else 0.0
    state.nav.append({
        "trade_date": target_date.isoformat(),
        "nav": nav_close, "cash": state.cash, "market_value": mv,
        "daily_ret": daily_ret,
    })

    # 4) Compute target weights using info through today close
    target_w = strategy.on_date(target_date, panel, state.positions, state.cash)

    # 5) Generate orders for tomorrow's open (target_w → integer share deltas)
    if target_w:
        new_pending: list[tuple[str, int]] = []
        held_now = set(state.positions)
        target_keys = set(target_w)
        # Sell symbols no longer in target (use sellable_qty after T+1)
        for s in held_now - target_keys:
            p = state.positions[s]
            if p.sellable_qty > 0:
                new_pending.append((s, -p.sellable_qty))
        # Adjust to target weight
        for s, w in target_w.items():
            row = by_sym.get(s)
            if row is None:
                continue
            ref = row["close"]
            if ref is None or ref <= 0:
                continue
            target_value = nav_close * w
            cur_qty = state.positions[s].qty if s in state.positions else 0
            cur_value = cur_qty * ref
            delta_value = target_value - cur_value
            if delta_value > 0:
                qty = round_to_lot(int(delta_value / ref), lot=100)
                if qty > 0:
                    new_pending.append((s, qty))
            elif delta_value < 0 and s in state.positions:
                sellable = state.positions[s].sellable_qty
                qty = min(sellable, round_to_lot(int(-delta_value / ref), lot=100))
                if qty > 0:
                    new_pending.append((s, -qty))
        state.pending_orders = new_pending

    state.last_run = target_date.isoformat()
    return True


def run_range(
    config: Path, start: date, end: date, *,
    initial_cash: float = 1_000_000.0,
    state_root: str = "data/paper_state",
) -> PaperState:
    api = get_data_api()
    strategy, cfg = load_strategy(config)
    state = PaperState.load(cfg.get("name", "paper"), root=state_root,
                            initial_cash=initial_cash)
    if not state.nav:
        state.cash = initial_cash
        state.initial_cash = initial_cash

    cost_model = CostModel(CostConfig())
    limits_cfg = PriceLimitConfig()

    # Get trading days from store
    panel = api.get_daily(None, start, end, adjust="fwd")
    if panel.is_empty():
        log.error(f"no panel data {start}..{end}")
        return state
    trading_days = sorted(panel["trade_date"].unique().to_list())
    log.info(f"running paper trading on {len(trading_days)} trading days")

    for d in trading_days:
        traded = run_one_day(api, strategy, cfg, d, state, cost_model, limits_cfg)
        if traded:
            n_fills_today = sum(1 for f in state.fills if f.trade_date == d.isoformat())
            log.info(f"  {d}: NAV={state.nav[-1]['nav']:,.0f}  fills={n_fills_today}  positions={len(state.positions)}")
        # persist every day so a crash doesn't lose state
        state.save()
    return state


def render_report(state: PaperState, cfg: dict, out_path: Path,
                  benchmark: list[dict] | None = None) -> Path:
    nav_for_report = [
        {**r, "nav": float(r["nav"]), "cash": float(r["cash"]),
         "market_value": float(r["market_value"]), "daily_ret": float(r.get("daily_ret", 0))}
        for r in state.nav
    ]
    fills_for_report = [
        {"trade_date": f.trade_date, "symbol": f.symbol, "side": f.side,
         "qty": f.qty, "price": f.price, "cost": f.cost}
        for f in state.fills
    ]
    return daily_report(
        nav=nav_for_report, fills=fills_for_report,
        strategy=cfg.get("name", "paper"),
        out_path=out_path,
        benchmark=benchmark, initial_cash=state.initial_cash,
    )


def diff_vs_backtest(paper_state: PaperState, backtest_summary_path: Path) -> dict:
    """Compare paper NAV trajectory to a reference backtest run.

    Returns dict with terminal NAV / fill count / max diff. Useful to verify
    no bugs introduced by the stateful paper path vs the in-memory backtester.
    """
    if not backtest_summary_path.exists():
        return {"error": f"missing {backtest_summary_path}"}
    bt = json.loads(backtest_summary_path.read_text())
    paper_terminal_ret = (paper_state.nav[-1]["nav"] / paper_state.initial_cash - 1) if paper_state.nav else 0
    return {
        "paper_total_return": paper_terminal_ret,
        "backtest_total_return": bt.get("total_return"),
        "diff_pct_points": (paper_terminal_ret - bt.get("total_return", 0)) * 100,
        "paper_fills": len(paper_state.fills),
        "backtest_fills": int(bt.get("n_fills", 0)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", help="run single date YYYY-MM-DD")
    ap.add_argument("--from", dest="frm", help="range start")
    ap.add_argument("--to", help="range end")
    ap.add_argument("--initial-cash", type=float, default=1_000_000.0)
    ap.add_argument("--state-root", default="data/paper_state")
    ap.add_argument("--out", default=None, help="HTML report path")
    ap.add_argument("--reset", action="store_true", help="wipe state before running")
    ap.add_argument("--diff-vs", default=None, help="path to backtest summary.json for divergence check")
    args = ap.parse_args()

    if args.reset:
        cfg = yaml.safe_load(Path(args.config).read_text())
        state_dir = Path(args.state_root) / cfg.get("name", "paper")
        if state_dir.exists():
            import shutil
            shutil.rmtree(state_dir)
            log.info(f"reset: removed {state_dir}")

    if args.once:
        d = _parse_date(args.once)
        state = run_range(Path(args.config), d, d,
                          initial_cash=args.initial_cash, state_root=args.state_root)
    elif args.frm and args.to:
        state = run_range(Path(args.config), _parse_date(args.frm), _parse_date(args.to),
                          initial_cash=args.initial_cash, state_root=args.state_root)
    else:
        ap.error("either --once or --from/--to required")

    # Report
    cfg = yaml.safe_load(Path(args.config).read_text())
    out_path = Path(args.out or (Path(args.state_root) / cfg.get("name", "paper") / "report.html"))
    api = get_data_api()
    bench = None
    if state.nav:
        try:
            sd, ed = state.nav[0]["trade_date"], state.nav[-1]["trade_date"]
            b = api.query.con.execute(
                "SELECT trade_date, close FROM daily WHERE symbol = '000300.SH' "
                "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                [_parse_date(sd), _parse_date(ed)],
            ).pl()
            if not b.is_empty():
                bench = [{"trade_date": str(r["trade_date"]), "close": float(r["close"])}
                         for r in b.iter_rows(named=True)]
        except Exception:
            pass

    render_report(state, cfg, out_path, benchmark=bench)
    print(f"\n📊 Paper trading summary for {cfg.get('name')}:", flush=True)
    if state.nav:
        first = state.nav[0]["nav"]; last = state.nav[-1]["nav"]
        print(f"  期间: {state.nav[0]['trade_date']} → {state.nav[-1]['trade_date']}")
        print(f"  累计收益: {(last/first-1)*100:+.2f}%")
        print(f"  终值: ¥{last:,.2f}  现金: ¥{state.cash:,.2f}")
        print(f"  持仓数: {len(state.positions)}")
        print(f"  累计成交: {len(state.fills)} 笔  (filled orders out of {len(state.orders)})")
        rejected = sum(1 for o in state.orders if o.status == "rejected")
        print(f"  rejected orders: {rejected}")
    print(f"\n  HTML 报告: {out_path}")

    if args.diff_vs:
        diff = diff_vs_backtest(state, Path(args.diff_vs))
        print(f"\n🔍 Paper vs Backtest divergence:")
        for k, v in diff.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
