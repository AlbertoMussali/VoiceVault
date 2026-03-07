# VoiceVault

VoiceVault is a small monorepo for a voice-based work journal.

The project combines:

- a FastAPI backend for auth, entries, search, audit logging, exports, and background jobs
- a React + Vite frontend for the authenticated app
- local Docker services for Postgres and Redis

At a high level, the app lets you capture journal entries, attach audio, generate transcripts, search across past entries, and produce summary/export artifacts from that history.

## Repository Layout

```text
.
├── apps/
│   ├── api/    # FastAPI service, Alembic migrations, backend tests
│   └── web/    # React + Vite frontend
├── docs/       # Process and operations docs
├── infra/      # Docker Compose and backup scripts
├── scripts/    # Local development helpers
└── Makefile    # Convenience targets
```

## Requirements

- Python 3.13
- `uv`
- Node.js + `npm`
- Docker Desktop or another working Docker engine

## Quick Start

1. Copy the local environment template:

```bash
cp .env.example .env
```

2. If you want transcription, transcript indexing, or ask/summarization features, add `OPENAI_API_KEY` to `.env`.

Without an OpenAI key, the local stack still runs, and you can use the seeded demo data plus non-AI parts of the app.

3. Start the local stack from the repo root:

```bash
./scripts/dev-local.sh
```

This starts:

- Postgres and Redis in Docker
- the FastAPI API on `http://localhost:3000`
- the background worker
- the Vite frontend on `http://localhost:5173`

The same flow is available through:

```bash
make dev-local
```

## Quick Usage

After startup:

1. Open `http://localhost:5173`
2. Sign in with the seeded demo account:
   - email: `demo@demo`
   - password: `demo`
3. Create or inspect entries in the app
4. Use search, ask/summaries, audit log, and export flows from the authenticated UI

Useful local endpoints:

- frontend: `http://localhost:5173`
- API: `http://localhost:3000`
- API docs: `http://localhost:3000/docs`
- health check: `http://localhost:3000/health`

To stop the local stack, press `Ctrl+C`. If you also want the script to tear down Docker services on exit, run:

```bash
./scripts/dev-local.sh --down-on-exit
```

## Common Commands

Backend tests:

```bash
uv run python -m unittest discover -s apps/api/tests
```

Frontend tests:

```bash
cd apps/web && npm test
```

Frontend build:

```bash
cd apps/web && npm run build
```

Manual backend migrations against the local Postgres service:

```bash
cd apps/api && DATABASE_URL=postgresql+psycopg://voicevault:voicevault@localhost:5432/voicevault_dev uv run --no-project --python 3.13 --with-requirements requirements.txt alembic upgrade head
```

## Detailed Docs

- Backend-specific notes: [apps/api/README.md](apps/api/README.md)
- Backup and restore runbook: [docs/operations/backup-restore-runbook.md](docs/operations/backup-restore-runbook.md)
- Project definition of done / smoke plan: [docs/definition-of-done.md](docs/definition-of-done.md)

## AI Transparency

AI coding tools, including Codex, have been used during development of this repository for tasks such as drafting code, refactoring, documentation updates, and implementation support.

That does not change the need for normal engineering controls. Generated code and generated documentation should be treated as reviewed artifacts, not as trusted source material by default. If you contribute here, assume AI-assisted changes still need human review, testing, and verification against the actual behavior of the system.

## License

This repository is available under the [MIT License](LICENSE). For the standard license text and usage conditions, see the root `LICENSE` file.
