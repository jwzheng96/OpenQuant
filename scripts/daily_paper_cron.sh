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
CONFIG="$REPO/configs/strategies/mf_ml_strict.yaml"

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

    echo "[$(ts)] step 1/2: sync $TODAY"
    if python scripts/sync_today_em.py --date "$TODAY"; then
        echo "[$(ts)] sync OK"
    else
        echo "[$(ts)] sync FAILED (exit $?); continuing to paper anyway"
    fi

    echo "[$(ts)] step 2/2: paper_daily --once $TODAY"
    if python scripts/paper_daily.py \
        --config "$CONFIG" \
        --once "$TODAY" \
        --state-root data/paper_state; then
        echo "[$(ts)] paper OK"
    else
        echo "[$(ts)] paper FAILED (exit $?)"
    fi

    echo "[$(ts)] daily_paper_cron.sh END"
} >> "$LOG" 2>&1
