#!/usr/bin/env bash
# cron: 0 3 * * * /home/USER/secretary-bot/deploy/backup_db.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$APP_DIR/backups"
set -a; source "$APP_DIR/.env"; set +a
python3 - <<'PY'
import os, sys
url = os.environ["DATABASE_URL"]
print("Backup via mysqldump must read credentials from .env — configure per sib's deploy/backup_db.sh.", file=sys.stderr)
print("Expected: mysqldump --single-transaction <db> > backups/secretary_$(date +%F).sql", file=sys.stderr)
PY
