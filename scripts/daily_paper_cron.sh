#!/bin/bash
# Daily paper trading runner — invoked by launchd at 17:00 Asia/Shanghai.
#
# Steps:
#   1. Sync today's EOD data via direct EM endpoint (~2 min)
#   2. Run paper_daily.py --once today (~1 sec)
#   3. Tag log line with date for tail-able history
#
# Failure modes (all logged, none fatal):
#   - Non-trading day      → sync_today_em writes 0 rows; paper_daily skips
#   - Network failure      → sync exits non-0; paper still tries (may skip on missing data)
#   - DB lock              → both retry once
#
# Log lives at logs/daily_paper.log relative to repo root.

set -uo pipefail

REPO="/Users/zjw/Documents/startup/chuanye"
LOG_DIR="$REPO/logs"
LOG="$LOG_DIR/daily_paper.log"
CONFIG="$REPO/configs/strategies/mf_ml_strict_xgb.yaml"   # XGB beats LGB +4pp Sharpe in 1799-pool OOS

mkdir -p "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

{
    echo "============================================================="
    echo "[$(ts)] daily_paper_cron.sh START"
    cd "$REPO" || { echo "[$(ts)] FATAL cd failed"; exit 1; }

    # shellcheck disable=SC1091
    source .venv/bin/activate

    # Bypass any inherited proxy env vars (Clash etc.)
    unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy ALL_PROXY all_proxy
    export NO_PROXY='*' no_proxy='*'

    TODAY=$(date '+%Y-%m-%d')
    DOW=$(date '+%u')   # 1=Mon ... 7=Sun

    if [ "$DOW" -ge 6 ]; then
        echo "[$(ts)] weekend (DOW=$DOW), skipping"
        exit 0
    fi

    echo "[$(ts)] step 1/3: sync $TODAY"
    if python scripts/sync_today_em.py --date "$TODAY"; then
        echo "[$(ts)] sync OK"
    else
        sync_exit=$?
        echo "[$(ts)] sync FAILED (exit $sync_exit); continuing to paper anyway"
        python - <<PY
from open_quant.alerts_db import write_alert
write_alert("warning", "cron.sync_today_em",
            "数据同步失败 (exit $sync_exit) — paper 仍将尝试运行",
            {"date": "$TODAY", "exit_code": $sync_exit})
PY
    fi

    echo "[$(ts)] step 2/3: paper_daily --once $TODAY"
    if python scripts/paper_daily.py \
        --config "$CONFIG" \
        --once "$TODAY" \
        --state-root data/paper_state; then
        echo "[$(ts)] paper OK"
    else
        paper_exit=$?
        echo "[$(ts)] paper FAILED (exit $paper_exit)"
        python - <<PY
from open_quant.alerts_db import write_alert
write_alert("critical", "cron.paper_daily",
            "Paper trading 失败 (exit $paper_exit) — 当日 NAV 未更新",
            {"date": "$TODAY", "exit_code": $paper_exit})
PY
    fi

    echo "[$(ts)] step 3/3: check_alerts (MDD / data staleness / outliers)"
    python scripts/check_alerts.py || echo "[$(ts)] check_alerts itself failed (non-fatal)"

    echo "[$(ts)] daily_paper_cron.sh END"
} >> "$LOG" 2>&1
