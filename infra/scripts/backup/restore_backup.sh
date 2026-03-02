#!/bin/sh
set -eu

require_var() {
  name="$1"
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required env var: $name" >&2
    exit 1
  fi
}

normalize_pg_url() {
  printf '%s' "$1" | sed 's#^postgresql+[^:]*://#postgresql://#'
}

require_var DATABASE_URL
require_var BACKUP_ARCHIVE_DIR

DATABASE_URL_NORMALIZED="$(normalize_pg_url "$DATABASE_URL")"
BLOB_TARGET_DIR="${BLOB_TARGET_DIR:-/var/lib/voicevault/storage}"
DB_DUMP_PATH="$BACKUP_ARCHIVE_DIR/db.dump"
BLOB_ARCHIVE_PATH="$BACKUP_ARCHIVE_DIR/blobs.tar.gz"

if [ ! -f "$DB_DUMP_PATH" ]; then
  echo "Database dump missing: $DB_DUMP_PATH" >&2
  exit 1
fi

if [ ! -f "$BLOB_ARCHIVE_PATH" ]; then
  echo "Blob archive missing: $BLOB_ARCHIVE_PATH" >&2
  exit 1
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "[dry-run] pg_restore --clean --if-exists --no-owner --no-privileges --dbname=\"$DATABASE_URL_NORMALIZED\" \"$DB_DUMP_PATH\""
  echo "[dry-run] mkdir -p \"$BLOB_TARGET_DIR\""
  echo "[dry-run] tar -xzf \"$BLOB_ARCHIVE_PATH\" -C \"$BLOB_TARGET_DIR\""
  exit 0
fi

if [ -f "$BACKUP_ARCHIVE_DIR/checksums.sha256" ] && command -v sha256sum >/dev/null 2>&1; then
  (cd "$BACKUP_ARCHIVE_DIR" && sha256sum -c checksums.sha256)
fi

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DATABASE_URL_NORMALIZED" "$DB_DUMP_PATH"

mkdir -p "$BLOB_TARGET_DIR"
# Keep restored blobs exactly as archived (entry-id keyed paths).
tar -xzf "$BLOB_ARCHIVE_PATH" -C "$BLOB_TARGET_DIR"

echo "Restore completed from: $BACKUP_ARCHIVE_DIR"
