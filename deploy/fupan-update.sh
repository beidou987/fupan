#!/usr/bin/env bash
# 复盘终端 · 每日更新入口
# 由 systemd timer 或 cron 调用；成功/失败都写入 state/update.log，失败可选 webhook 告警。
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/fupan}"
LOG="${LOG:-$APP_DIR/state/update.log}"
cd "$APP_DIR" || exit 2
mkdir -p "$(dirname "$LOG")" state

echo "=== $(date '+%F %T') 开始更新 ===" >> "$LOG"

if python3 scripts/market_update.py >> "$LOG" 2>&1; then
  echo "=== $(date '+%F %T') 成功 ===" >> "$LOG"
  exit 0
fi

code=$?
echo "=== $(date '+%F %T') 失败（退出码 $code），站点继续使用上一交易日数据 ===" >> "$LOG"

if [ -n "${ALERT_WEBHOOK:-}" ]; then
  curl -s -m 10 -X POST "$ALERT_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "{\"text\":\"复盘终端数据更新失败，退出码 $code，时间 $(date '+%F %T')，详见 $LOG\"}" \
    >/dev/null 2>&1 || true
fi

exit "$code"