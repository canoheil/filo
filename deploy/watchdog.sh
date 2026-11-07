#!/usr/bin/env bash
# cron: * * * * * /home/USER/filo/deploy/watchdog.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
mkdir -p logs

# Match the same absolute script path used by nohup below.
if pgrep -f "$APP_DIR/bot.py" >/dev/null; then
  exit 0
fi

# cPanel virtual environment; override PY for another deployment.
PY="${PY:-$HOME/virtualenv/filo/3.11/bin/python3}"
if [ ! -x "$PY" ]; then
  echo "Python executable not found: $PY" >&2
  exit 1
fi

nohup "$PY" "$APP_DIR/bot.py" >> logs/bot.log 2>&1 &
echo "$(date -u +%FT%TZ) restarted (pid $!)" >> logs/watchdog.log
