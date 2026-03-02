#!/bin/sh
set -eu

BACKUP_INTERVAL_MINUTES="${BACKUP_INTERVAL_MINUTES:-1440}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CREATE_SCRIPT="$SCRIPT_DIR/create_backup.sh"

if [ ! -x "$CREATE_SCRIPT" ]; then
  echo "Backup script is not executable: $CREATE_SCRIPT" >&2
  exit 1
fi

while true; do
  "$CREATE_SCRIPT"
  sleep "$((BACKUP_INTERVAL_MINUTES * 60))"
done
