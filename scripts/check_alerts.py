"""Daily health checks — called by launchd cron after paper_daily runs.

Three categories of automatic alerts:

  1. Data staleness
     - daily data not updated in > 3 calendar days → warning
     - > 7 days → critical

  2. Strategy MDD breach for the active strategy
     - current dd > -20% → warning
     - current dd > -30% → critical
     - new MDD (today is the lowest point ever) → warning

  3. Today's daily return outlier
     - daily return < -5% → warning
     - < -8% → critical

Reads paper_state JSON files directly (no SQLAlchemy needed) + queries
DuckDB for daily-data freshness. Writes rows to PostgreSQL `alerts` table
via open_quant.alerts_db.write_alert.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from open_quant.alerts_db import write_alert

ROOT = Path(__file__).resolve().parent.parent
PAPER_STATE_DIR = ROOT / "data" / "paper_state"
CRON_PATH = ROOT / "scripts" / "daily_paper_cron.sh"
DAILY_PARQUET_GLOB = ROOT / "data" / "parquet" / "daily" / "year=*" / "month=*" / "data.parquet"


def active_strategy() -> str | None:
    """Grep the cron script for CONFIG=path/<strategy>.yaml."""
    if not CRON_PATH.exists():
        return None
    m = re.search(r'CONFIG="?\$REPO/configs/strategies/([^"\s.]+)\.yaml"?', CRON_PATH.read_text())
    return m.group(1) if m else None


def check_data_staleness() -> None:
    """Read daily parquet via polars (avoids DuckDB lock contention with uvicorn)."""
    try:
        df = pl.scan_parquet(str(DAILY_PARQUET_GLOB), hive_partitioning=True).select(
            pl.col("trade_date").max().alias("max_d")
        ).collect()
    except Exception as e:
        write_alert("warning", "cron.check_alerts",
                    f"无法读取 daily parquet: {e}",
                    {"glob": str(DAILY_PARQUET_GLOB)})
        return
    if df.is_empty():
        write_alert("critical", "cron.check_alerts", "daily 数据表为空", {"latest": None})
        return
    latest = df["max_d"][0]
    if latest is None:
        write_alert("critical", "cron.check_alerts", "daily 数据表为空", {"latest": None})
        return
    if isinstance(latest, str):
        latest = datetime.strptime(latest, "%Y-%m-%d").date()
    days_old = (date.today() - latest).days
    if days_old > 7:
        write_alert(
            "critical",
            "data.staleness",
            f"日线数据已 {days_old} 天未更新（最新 {latest}）",
            {"latest": str(latest), "days_old": days_old},
        )
    elif days_old > 3:
        write_alert(
            "warning",
            "data.staleness",
            f"日线数据已 {days_old} 天未更新（最新 {latest}）",
            {"latest": str(latest), "days_old": days_old},
        )


def check_strategy_mdd(strategy: str) -> None:
    sd = PAPER_STATE_DIR / strategy
    nav_path = sd / "nav.json"
    if not nav_path.exists():
        return
    nav = json.loads(nav_path.read_text())
    if not nav or len(nav) < 2:
        return

    # Compute current drawdown vs running peak
    peak = 0.0
    peak_date = None
    cur_dd = 0.0
    historical_min_dd = 0.0
    for r in nav:
        v = float(r["nav"])
        if v > peak:
            peak = v
            peak_date = r["trade_date"]
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < historical_min_dd:
            historical_min_dd = dd
    cur_dd = (float(nav[-1]["nav"]) - peak) / peak if peak > 0 else 0.0

    today = nav[-1]["trade_date"]
    is_new_mdd = abs(cur_dd) >= abs(historical_min_dd) - 1e-9 and abs(cur_dd) > 0.05

    if cur_dd < -0.30:
        write_alert(
            "critical",
            "strategy.mdd",
            f"{strategy} 回撤 {cur_dd*100:.1f}% 超过 -30%",
            {"strategy": strategy, "cur_dd": cur_dd, "peak_date": peak_date},
        )
    elif cur_dd < -0.20:
        write_alert(
            "warning",
            "strategy.mdd",
            f"{strategy} 回撤 {cur_dd*100:.1f}% 超过 -20%",
            {"strategy": strategy, "cur_dd": cur_dd, "peak_date": peak_date},
        )
    elif is_new_mdd:
        write_alert(
            "warning",
            "strategy.mdd",
            f"{strategy} 创下新低: {cur_dd*100:.1f}%",
            {"strategy": strategy, "cur_dd": cur_dd, "as_of": today},
        )

    # Today's daily return outlier
    daily_ret = float(nav[-1].get("daily_ret", 0.0))
    if daily_ret < -0.08:
        write_alert(
            "critical",
            "strategy.daily_ret",
            f"{strategy} 当日 -{abs(daily_ret)*100:.2f}%（{today}）",
            {"strategy": strategy, "daily_ret": daily_ret, "date": today},
        )
    elif daily_ret < -0.05:
        write_alert(
            "warning",
            "strategy.daily_ret",
            f"{strategy} 当日 -{abs(daily_ret)*100:.2f}%（{today}）",
            {"strategy": strategy, "daily_ret": daily_ret, "date": today},
        )


def main() -> int:
    try:
        check_data_staleness()
    except Exception as e:
        print(f"check_data_staleness failed: {e}", file=sys.stderr)

    strategy = active_strategy()
    if strategy:
        try:
            check_strategy_mdd(strategy)
        except Exception as e:
            print(f"check_strategy_mdd failed: {e}", file=sys.stderr)
    else:
        print("no active strategy detected", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
