#!/usr/bin/env bash
# cron: * * * * * /home/USER/secretary-bot/deploy/watchdog.sh >> /home/USER/secretary-bot/logs/watchdog.log 2>&1
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
mkdir -p logs
if pgrep -f "secretary-bot/bot.py" >/dev/null; then
  exit 0
fi
PY="$(command -v python3)"
if [ -d .venv ]; then PY="$APP_DIR/.venv/bin/python"; fi
nohup "$PY" bot.py >> logs/bot.log 2>&1 &
echo "$(date -u +%FT%TZ) restarted (pid $!)" >> logs/watchdog.log
