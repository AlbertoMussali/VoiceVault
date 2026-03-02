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
  # SQLAlchemy URLs use postgresql+psycopg://; pg_dump expects postgresql://.
  printf '%s' "$1" | sed 's#^postgresql+[^:]*://#postgresql://#'
}

require_var DATABASE_URL

BACKUP_ROOT_DIR="${BACKUP_ROOT_DIR:-/var/backups/voicevault}"
BLOB_SOURCE_DIR="${BLOB_SOURCE_DIR:-/var/lib/voicevault/storage}"
TIMESTAMP="${BACKUP_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
ARCHIVE_DIR="$BACKUP_ROOT_DIR/$TIMESTAMP"
DATABASE_URL_NORMALIZED="$(normalize_pg_url "$DATABASE_URL")"

mkdir -p "$ARCHIVE_DIR"

DB_DUMP_PATH="$ARCHIVE_DIR/db.dump"
BLOB_ARCHIVE_PATH="$ARCHIVE_DIR/blobs.tar.gz"
MANIFEST_PATH="$ARCHIVE_DIR/manifest.txt"

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "[dry-run] pg_dump --format=custom --file=$DB_DUMP_PATH \"$DATABASE_URL_NORMALIZED\""
  echo "[dry-run] tar -czf $BLOB_ARCHIVE_PATH -C $BLOB_SOURCE_DIR ."
else
  pg_dump --format=custom --file="$DB_DUMP_PATH" "$DATABASE_URL_NORMALIZED"

  if [ -d "$BLOB_SOURCE_DIR" ]; then
    tar -czf "$BLOB_ARCHIVE_PATH" -C "$BLOB_SOURCE_DIR" .
  else
    echo "Blob source directory not found; writing empty archive: $BLOB_SOURCE_DIR" >&2
    tar -czf "$BLOB_ARCHIVE_PATH" --files-from /dev/null
  fi

  {
    echo "timestamp=$TIMESTAMP"
    echo "database_dump=db.dump"
    echo "blob_archive=blobs.tar.gz"
  } > "$MANIFEST_PATH"

  # Best-effort checksums for integrity checks.
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$ARCHIVE_DIR" && sha256sum db.dump blobs.tar.gz > checksums.sha256)
  fi
fi

echo "Backup created: $ARCHIVE_DIR"
