# API Service

## Database migrations

The API and Alembic read the same `DATABASE_URL` setting (`app/settings.py`).

Run migrations against the Docker Compose Postgres service:

```bash
docker compose run --rm api sh -lc 'cd /app/apps/api && alembic upgrade head'
```
