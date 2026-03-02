# Backup and Restore Runbook

This runbook covers scheduled Postgres + blob backups and a restore workflow for the production compose stack.

## What is backed up

- Postgres database dump (`pg_dump` custom format) as `db.dump`
- Blob storage archive (`tar.gz`) from `STORAGE_LOCAL_ROOT` as `blobs.tar.gz`
- Optional checksum file (`checksums.sha256`) when `sha256sum` is available

Each backup lands under:

- `/var/backups/voicevault/<UTC_TIMESTAMP>/`

## Scheduled backup setup

1. Ensure `.env` includes:
   - `DATABASE_URL`
   - `STORAGE_LOCAL_ROOT`
   - `BACKUP_INTERVAL_MINUTES` (default `1440`)
2. Start production stack:

```bash
docker compose -f infra/docker-compose.prod.yml up -d
```

The `backup` service runs `infra/scripts/backup/scheduled_backup.sh`, which calls `create_backup.sh` every `BACKUP_INTERVAL_MINUTES`.

## One-off manual backup

```bash
make backup-create
```

or directly:

```bash
docker compose -f infra/docker-compose.prod.yml run --rm backup sh -lc '/backup/scripts/create_backup.sh'
```

## Restore procedure

1. Identify backup directory to restore:

```bash
docker compose -f infra/docker-compose.prod.yml exec backup sh -lc 'ls -1 /var/backups/voicevault'
```

2. Dry-run restore to validate inputs and command wiring:

```bash
BACKUP_ARCHIVE_DIR=/var/backups/voicevault/<TIMESTAMP> make backup-restore-dry-run
```

3. Execute restore:

```bash
BACKUP_ARCHIVE_DIR=/var/backups/voicevault/<TIMESTAMP> make backup-restore
```

This performs:

- `pg_restore --clean --if-exists --no-owner --no-privileges`
- blob archive extraction into `STORAGE_LOCAL_ROOT`

## Verification checklist after restore

1. API health endpoint returns success:

```bash
curl -fsS http://localhost:8000/health
```

2. Entry counts are non-zero (or expected):

```bash
docker compose -f infra/docker-compose.prod.yml exec db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) from entries;"'
```

3. Blob files exist:

```bash
docker compose -f infra/docker-compose.prod.yml exec api sh -lc 'find "$STORAGE_LOCAL_ROOT" -type f | head'
```

## Tested status

- Restore runbook command path is covered in automated test:
  - `apps/api/tests/test_backup_restore_runbook.py`
- The test executes `restore_backup.sh` in `DRY_RUN=1` mode against a synthetic backup archive and must pass in `make test`.
