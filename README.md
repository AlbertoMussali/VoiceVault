## VoiceVault Local Development

Run the full local development stack from the repo root:

```bash
./scripts/dev-local.sh
```

This launches:
- Docker services: Postgres (`db`) and Redis (`redis`)
- Host processes: FastAPI API (`uvicorn`), RQ worker, and Vite web app

Shortcuts:
- `make dev-local` runs the same script.
- `./scripts/dev-local.sh --skip-install` skips `npm install` checks.
- `./scripts/dev-local.sh --skip-migrate` skips Alembic migrations.
- `./scripts/dev-local.sh --down-on-exit` also stops Docker services when exiting.

Default URLs:
- Web: `http://localhost:5173`
- API: `http://localhost:3000`
