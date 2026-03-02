# API Service

## Database migrations

The API and Alembic read the same `DATABASE_URL` setting (`app/settings.py`).

Run migrations against the Docker Compose Postgres service:

```bash
docker compose run --rm api sh -lc 'cd /app/apps/api && alembic upgrade head'
```

## Background jobs (RQ)

Run a local worker from the compose stack:

```bash
docker compose -f infra/docker-compose.dev.yml up worker redis
```

Enqueue the built-in stub job:

```bash
docker compose -f infra/docker-compose.dev.yml run --rm worker sh -lc 'cd /workspace/apps/api && uv run --with-requirements requirements.txt python -m app.enqueue_stub_job'
```

## API hardening defaults

The API includes baseline production hardening middleware:

- Request size limits:
  - `MAX_REQUEST_SIZE_BYTES` (default `2097152`, 2 MiB) for general API requests.
  - `MAX_AUDIO_UPLOAD_SIZE_BYTES` (default `26214400`, 25 MiB) for `POST /api/v1/entries/{id}/audio`.
- In-memory fixed-window rate limiting (per client IP):
  - `RATE_LIMIT_WINDOW_SECONDS` (default `60`)
  - `RATE_LIMIT_REQUESTS` (default `120`) for general `/api/v1/*` routes
  - `RATE_LIMIT_AUTH_REQUESTS` (default `20`) for `/api/v1/auth/*` routes
- Strict CORS:
  - `CORS_ALLOWED_ORIGINS` (comma-separated origins)
  - default: `http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000`
  - requests from non-allowed origins are rejected for CORS preflight.

The auth layer also includes baseline security hardening:

- Secure refresh-token cookies:
  - HTTP-only refresh token cookie + companion CSRF cookie are issued on signup/login/refresh.
  - defaults: `AUTH_COOKIE_SECURE=true`, `AUTH_COOKIE_SAMESITE=strict`.
- CSRF protection for cookie-based refresh/logout:
  - `POST /api/v1/auth/refresh` and `POST /api/v1/auth/logout` require `X-CSRF-Token` matching the CSRF cookie when using refresh token cookies.
  - Existing JSON-body refresh token flows remain supported for non-cookie clients.
- Password policy on signup:
  - default minimum length: `PASSWORD_MIN_LENGTH=12`
  - defaults require uppercase, lowercase, and digit (`PASSWORD_REQUIRE_UPPERCASE`, `PASSWORD_REQUIRE_LOWERCASE`, `PASSWORD_REQUIRE_DIGIT`).
- Dependency scanning:
  - CI runs backend dependency scanning using `pip-audit` against `apps/api/requirements.txt`.
