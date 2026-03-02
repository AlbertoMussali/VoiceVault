.PHONY: dev-up dev-down dev-local lint test backup-create backup-restore backup-restore-dry-run

dev-up:
	@if [ -f docker-compose.yml ] || [ -f compose.yml ] || [ -f compose.yaml ]; then \
		docker compose up -d; \
	else \
		echo "No Docker Compose file found; skipping dev-up."; \
	fi

dev-down:
	@if [ -f docker-compose.yml ] || [ -f compose.yml ] || [ -f compose.yaml ]; then \
		docker compose down; \
	else \
		echo "No Docker Compose file found; skipping dev-down."; \
	fi

dev-local:
	./scripts/dev-local.sh

lint:
	@echo "No lint configuration found; lint target is a no-op."

test:
	UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest discover -s apps/api/tests

backup-create:
	docker compose -f infra/docker-compose.prod.yml run --rm backup sh -lc '/backup/scripts/create_backup.sh'

backup-restore:
	@if [ -z "$$BACKUP_ARCHIVE_DIR" ]; then \
		echo "BACKUP_ARCHIVE_DIR is required. Example: BACKUP_ARCHIVE_DIR=/var/backups/voicevault/20260302T000000Z make backup-restore"; \
		exit 1; \
	fi
	docker compose -f infra/docker-compose.prod.yml run --rm -e BACKUP_ARCHIVE_DIR=$$BACKUP_ARCHIVE_DIR backup sh -lc '/backup/scripts/restore_backup.sh'

backup-restore-dry-run:
	@if [ -z "$$BACKUP_ARCHIVE_DIR" ]; then \
		echo "BACKUP_ARCHIVE_DIR is required. Example: BACKUP_ARCHIVE_DIR=/var/backups/voicevault/20260302T000000Z make backup-restore-dry-run"; \
		exit 1; \
	fi
	docker compose -f infra/docker-compose.prod.yml run --rm -e DRY_RUN=1 -e BACKUP_ARCHIVE_DIR=$$BACKUP_ARCHIVE_DIR backup sh -lc '/backup/scripts/restore_backup.sh'
