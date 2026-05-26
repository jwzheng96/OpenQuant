"""A/B evaluation: ml_lgb pure vs ml_lgb + LLM overlay.

Pipeline:
  1. Run paper trading with overlay=OFF on the given period → baseline metrics
  2. Run paper trading with overlay=ON  on the same period   → +agents metrics
  3. Compare:
       - return / Sharpe / MDD
       - per-day stocks held
       - which stocks the overlay DROP'd + their realized return next 5 days
       - LLM cost vs return uplift
  4. Output JSON + Markdown summary

By default, picks a short 3-month window to bound LLM cost:
  ~13 weekly rebalances × 30 stocks × 4 calls × ¥0.003 ≈ ¥5

Usage:
  python scripts/eval_agents.py \
      --config-off configs/strategies/mf_ml_strict.yaml \
      --config-on  configs/strategies/mf_ml_strict_with_agents.yaml \
      --from 2025-06-01 --to 2025-08-31
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from scripts.paper_daily import run_range
from uni_quant.data.api import get_data_api
from uni_quant.utils import get_logger

log = get_logger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _compute_metrics(nav: list[dict], initial_cash: float) -> dict:
    """Standard performance metrics from NAV trajectory."""
    import math
    if not nav:
        return {}
    first, last = nav[0]["nav"], nav[-1]["nav"]
    total_ret = last / initial_cash - 1
    rets = [r.get("daily_ret", 0) or 0 for r in nav[1:]]
    rets_clean = [r for r in rets if math.isfinite(r)]
    if rets_clean:
        mu = sum(rets_clean) / len(rets_clean)
        var = sum((r - mu) ** 2 for r in rets_clean) / max(len(rets_clean) - 1, 1)
        sd = math.sqrt(var)
        sharpe = mu / sd * math.sqrt(252) if sd > 0 else 0
    else:
        sharpe = 0
    peak = initial_cash
    max_dd = 0
    for r in nav:
        peak = max(peak, r["nav"])
        max_dd = min(max_dd, (r["nav"] - peak) / peak)
    return {
        "total_return": total_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "n_days": len(nav),
        "final_nav": last,
    }


def _dropped_symbol_returns(overlay_log: list[dict], start: date, end: date) -> list[dict]:
    """For each DROP decision, look up what the stock actually did in the next 5 days.
    Positive return = overlay was wrong to drop; negative = overlay saved us."""
    api = get_data_api()
    panel = api.get_daily(None, start, end + timedelta(days=15), adjust="fwd")
    if panel.is_empty():
        return []
    closes_by_sym_date = {
        (r["symbol"], r["trade_date"]): r["close"]
        for r in panel.iter_rows(named=True)
    }
    out = []
    for entry in overlay_log:
        d = date.fromisoformat(entry["date"])
        for sym, reason in entry.get("drops", {}).items():
            # find closes on d and d+5 trading days
            future_dates = [td for (s, td) in closes_by_sym_date if s == sym and td > d]
            if len(future_dates) < 5:
                continue
            future_dates.sort()
            d_open = future_dates[0]   # first day after drop
            d_close = future_dates[min(4, len(future_dates) - 1)]
            c0 = closes_by_sym_date.get((sym, d_open))
            c1 = closes_by_sym_date.get((sym, d_close))
            if c0 and c1:
                ret_5d = c1 / c0 - 1
                out.append({"date": str(d), "symbol": sym, "reason": reason,
                            "return_5d_after_drop": ret_5d})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-off", required=True, help="strategy yaml WITHOUT overlay")
    ap.add_argument("--config-on", required=True, help="strategy yaml WITH overlay enabled")
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--initial-cash", type=float, default=1_000_000)
    ap.add_argument("--state-root", default="data/eval_agents")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--out", default="data/eval_agents/comparison.json")
    args = ap.parse_args()

    start, end = _parse_date(args.frm), _parse_date(args.to)
    state_root = Path(args.state_root)
    if args.reset and state_root.exists():
        shutil.rmtree(state_root)
        log.info(f"reset {state_root}")
    state_root.mkdir(parents=True, exist_ok=True)

    # ---- Pass 1: overlay OFF (pure quant baseline) ----
    print(f"\n{'=' * 70}", flush=True)
    print(f" PASS 1/2: overlay OFF (pure quant baseline)", flush=True)
    print(f"{'=' * 70}\n", flush=True)
    t0 = time.time()
    state_off = run_range(
        Path(args.config_off), start, end,
        initial_cash=args.initial_cash,
        state_root=str(state_root / "off"),
    )
    time_off = time.time() - t0
    metrics_off = _compute_metrics(state_off.nav, args.initial_cash)
    print(f"  off complete in {time_off:.1f}s")
    print(f"  total_return: {metrics_off.get('total_return', 0) * 100:+.2f}%")
    print(f"  sharpe:       {metrics_off.get('sharpe', 0):+.2f}")
    print(f"  max_drawdown: {metrics_off.get('max_drawdown', 0) * 100:+.2f}%")
    print(f"  n fills:      {len(state_off.fills)}")

    # ---- Pass 2: overlay ON (quant + LLM agents) ----
    print(f"\n{'=' * 70}", flush=True)
    print(f" PASS 2/2: overlay ON (quant + LLM agents)", flush=True)
    print(f"{'=' * 70}\n", flush=True)
    t0 = time.time()

    # Load config to access overlay_log
    import yaml
    cfg_on = yaml.safe_load(Path(args.config_on).read_text())
    state_on = run_range(
        Path(args.config_on), start, end,
        initial_cash=args.initial_cash,
        state_root=str(state_root / "on"),
    )
    time_on = time.time() - t0
    metrics_on = _compute_metrics(state_on.nav, args.initial_cash)
    print(f"  on complete in {time_on:.1f}s")
    print(f"  total_return: {metrics_on.get('total_return', 0) * 100:+.2f}%")
    print(f"  sharpe:       {metrics_on.get('sharpe', 0):+.2f}")
    print(f"  max_drawdown: {metrics_on.get('max_drawdown', 0) * 100:+.2f}%")
    print(f"  n fills:      {len(state_on.fills)}")

    # ---- Comparison ----
    print(f"\n{'=' * 70}", flush=True)
    print(f" COMPARISON", flush=True)
    print(f"{'=' * 70}", flush=True)
    print(f"\n{'指标':<20} {'OFF':>15} {'ON':>15} {'差异 (ON - OFF)':>20}")
    print("-" * 70)
    for key, label in [
        ("total_return", "累计收益"),
        ("sharpe", "Sharpe"),
        ("max_drawdown", "最大回撤"),
        ("final_nav", "终值 NAV"),
    ]:
        v_off = metrics_off.get(key, 0)
        v_on = metrics_on.get(key, 0)
        diff = v_on - v_off
        if key in ("total_return", "max_drawdown"):
            print(f"{label:<18} {v_off * 100:>+13.2f}% {v_on * 100:>+13.2f}% {diff * 100:>+18.2f}pp")
        elif key == "sharpe":
            print(f"{label:<18} {v_off:>+15.3f} {v_on:>+15.3f} {diff:>+20.3f}")
        else:
            print(f"{label:<18} ¥{v_off:>14,.0f} ¥{v_on:>14,.0f} {diff:>+19,.0f}")

    # Fill differences
    syms_off = {f.symbol for f in state_off.fills}
    syms_on = {f.symbol for f in state_on.fills}
    print(f"\n{'交易股票集合':<20} {len(syms_off):>15} {len(syms_on):>15} "
          f"{len(syms_on) - len(syms_off):>+20}")
    only_off = syms_off - syms_on
    only_on = syms_on - syms_off
    if only_off:
        print(f"  仅 OFF 交易: {sorted(only_off)[:10]}{' ...' if len(only_off) > 10 else ''}")
    if only_on:
        print(f"  仅 ON  交易: {sorted(only_on)[:10]}{' ...' if len(only_on) > 10 else ''}")

    # Save comparison
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "period": [str(start), str(end)],
        "config_off": str(args.config_off),
        "config_on": str(args.config_on),
        "metrics_off": metrics_off,
        "metrics_on": metrics_on,
        "uplift": {
            "return_pp": (metrics_on.get("total_return", 0) - metrics_off.get("total_return", 0)) * 100,
            "sharpe_diff": metrics_on.get("sharpe", 0) - metrics_off.get("sharpe", 0),
            "mdd_diff_pp": (metrics_on.get("max_drawdown", 0) - metrics_off.get("max_drawdown", 0)) * 100,
        },
        "elapsed_off_sec": time_off,
        "elapsed_on_sec": time_on,
        "fills_off": len(state_off.fills),
        "fills_on": len(state_on.fills),
        "symbols_only_off": sorted(only_off),
        "symbols_only_on": sorted(only_on),
    }
    out_path.write_text(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
    print(f"\nsaved comparison → {out_path}")

    # Markdown report
    md_path = out_path.with_suffix(".md")
    _write_markdown(md_path, summary)
    print(f"saved markdown → {md_path}")


def _write_markdown(path: Path, s: dict) -> None:
    m_off, m_on, u = s["metrics_off"], s["metrics_on"], s["uplift"]
    md = f"""# Agent Overlay A/B Evaluation

期间: {s['period'][0]} → {s['period'][1]}

## Metrics

| 指标 | OFF (pure quant) | ON (quant + agents) | 差异 |
|---|---|---|---|
| 累计收益 | {m_off.get('total_return', 0)*100:+.2f}% | {m_on.get('total_return', 0)*100:+.2f}% | {u['return_pp']:+.2f}pp |
| Sharpe | {m_off.get('sharpe', 0):+.3f} | {m_on.get('sharpe', 0):+.3f} | {u['sharpe_diff']:+.3f} |
| 最大回撤 | {m_off.get('max_drawdown', 0)*100:+.2f}% | {m_on.get('max_drawdown', 0)*100:+.2f}% | {u['mdd_diff_pp']:+.2f}pp |
| 终值 NAV | ¥{m_off.get('final_nav', 0):,.0f} | ¥{m_on.get('final_nav', 0):,.0f} | ¥{m_on.get('final_nav', 0) - m_off.get('final_nav', 0):+,.0f} |
| 成交笔数 | {s['fills_off']} | {s['fills_on']} | {s['fills_on'] - s['fills_off']:+d} |

## Verdict
- Return uplift: **{u['return_pp']:+.2f}pp**
- Sharpe uplift: **{u['sharpe_diff']:+.3f}**
- {'✅ overlay 带来正向 alpha' if u['return_pp'] > 0 else '❌ overlay 反而拖累收益'}

## Trade Universe Diff
- 仅 OFF 持有: {len(s['symbols_only_off'])} 只
- 仅 ON 持有: {len(s['symbols_only_on'])} 只

## Timing
- OFF: {s['elapsed_off_sec']:.1f}s
- ON:  {s['elapsed_on_sec']:.1f}s (overhead 主要来自 LLM 调用)
"""
    path.write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
