#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/infra/docker-compose.dev.yml"
ENV_FILE="$ROOT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
fi

API_PORT="${API_PORT:-3000}"
WEB_PORT="${WEB_PORT:-5173}"
API_PYTHON="${API_PYTHON:-3.13}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"
POSTGRES_USER="${POSTGRES_USER:-voicevault}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-voicevault}"
POSTGRES_DB="${POSTGRES_DB:-voicevault_dev}"

DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}}"
REDIS_URL="${REDIS_URL:-redis://localhost:${REDIS_PORT}/0}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:${API_PORT}}"
DEMO_SEED_ENABLED="${DEMO_SEED_ENABLED:-true}"
DEMO_SEED_EMAIL="${DEMO_SEED_EMAIL:-demo@demo}"
DEMO_SEED_PASSWORD="${DEMO_SEED_PASSWORD:-demo}"
DEMO_SEED_DAYS="${DEMO_SEED_DAYS:-30}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
OPENAI_STT_MODEL="${OPENAI_STT_MODEL:-gpt-4o-mini-transcribe}"
OPENAI_SUMMARY_MODEL="${OPENAI_SUMMARY_MODEL:-gpt-4o-mini}"
OPENAI_INDEXING_MODEL="${OPENAI_INDEXING_MODEL:-gpt-4o-mini}"
PGGSSENCMODE="${PGGSSENCMODE:-disable}"
if [[ "$(uname -s)" == "Darwin" ]]; then
  DEFAULT_SIMPLE_WORKER="true"
else
  DEFAULT_SIMPLE_WORKER="false"
fi
VOICEVAULT_SIMPLE_WORKER="${VOICEVAULT_SIMPLE_WORKER:-$DEFAULT_SIMPLE_WORKER}"

INSTALL_DEPS=1
RUN_MIGRATIONS=1
DOWN_ON_EXIT=0
DOCKER_STARTED=0

PIDS=()
NAMES=()

print_help() {
  cat <<'EOF'
Usage: ./scripts/dev-local.sh [options]

Launch local VoiceVault development stack:
- Docker: Postgres + Redis
- Host: API + worker + frontend dev server

Options:
  --skip-install     Skip dependency installation checks
  --skip-migrate     Skip Alembic migrations
  --down-on-exit     Run docker compose down on exit
  -h, --help         Show this help
EOF
}

log() {
  printf '[dev-local] %s\n' "$1"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log "Missing required command: $cmd"
    exit 1
  fi
}

wait_for_service_health() {
  local service="$1"
  local timeout_seconds="${2:-60}"
  local start_ts
  start_ts="$(date +%s)"

  while true; do
    local cid
    cid="$(docker compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      local status
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
      if [[ "$status" == "healthy" || "$status" == "running" ]]; then
        log "$service is $status"
        return 0
      fi
    fi

    if (( "$(date +%s)" - start_ts >= timeout_seconds )); then
      log "Timed out waiting for $service to become healthy."
      exit 1
    fi
    sleep 2
  done
}

start_process() {
  local name="$1"
  local command="$2"

  (
    set -o pipefail
    eval "$command" 2>&1 | sed -u "s/^/[$name] /"
  ) &

  local pid=$!
  PIDS+=("$pid")
  NAMES+=("$name")
  log "Started $name (pid $pid)"
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  log "Stopping local processes..."
  if (( ${#PIDS[@]} > 0 )); then
    for pid in "${PIDS[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
      fi
    done

    for pid in "${PIDS[@]}"; do
      wait "$pid" 2>/dev/null || true
    done
  fi

  if [[ "$DOWN_ON_EXIT" -eq 1 ]]; then
    if [[ "$DOCKER_STARTED" -eq 1 ]]; then
      log "Stopping docker services..."
      docker compose -f "$COMPOSE_FILE" down
    fi
  fi

  exit "$exit_code"
}

wait_until_any_exits() {
  while true; do
    local i
    for i in "${!PIDS[@]}"; do
      local pid="${PIDS[$i]}"
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        wait "$pid"
        local code=$?
        log "${NAMES[$i]} exited with code $code"
        return "$code"
      fi
    done
    sleep 1
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install)
      INSTALL_DEPS=0
      shift
      ;;
    --skip-migrate)
      RUN_MIGRATIONS=0
      shift
      ;;
    --down-on-exit)
      DOWN_ON_EXIT=1
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      log "Unknown option: $1"
      print_help
      exit 1
      ;;
  esac
done

trap cleanup EXIT INT TERM

require_cmd docker
require_cmd uv
require_cmd npm

if ! docker info >/dev/null 2>&1; then
  log "Docker daemon is not running. Start Docker and retry."
  exit 1
fi

if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  if [[ ! -d "$ROOT_DIR/apps/web/node_modules" ]]; then
    log "Installing frontend dependencies..."
    (
      cd "$ROOT_DIR/apps/web"
      npm install
    )
  fi
fi

log "Starting Postgres and Redis via Docker Compose..."
docker compose -f "$COMPOSE_FILE" up -d db redis
DOCKER_STARTED=1

wait_for_service_health db 90
wait_for_service_health redis 90

log "Using Python ${API_PYTHON} for API/worker/migrations."

if [[ "$RUN_MIGRATIONS" -eq 1 ]]; then
  log "Running Alembic migrations..."
  (
    cd "$ROOT_DIR/apps/api"
    DATABASE_URL="$DATABASE_URL" PGGSSENCMODE="$PGGSSENCMODE" uv run --no-project --python "$API_PYTHON" --with-requirements requirements.txt alembic upgrade head
  )
fi

log "Launching API, worker, and web dev server..."
start_process "api" "cd \"$ROOT_DIR/apps/api\" && DATABASE_URL=\"$DATABASE_URL\" REDIS_URL=\"$REDIS_URL\" DEMO_SEED_ENABLED=\"$DEMO_SEED_ENABLED\" DEMO_SEED_EMAIL=\"$DEMO_SEED_EMAIL\" DEMO_SEED_PASSWORD=\"$DEMO_SEED_PASSWORD\" DEMO_SEED_DAYS=\"$DEMO_SEED_DAYS\" OPENAI_API_KEY=\"$OPENAI_API_KEY\" OPENAI_BASE_URL=\"$OPENAI_BASE_URL\" OPENAI_STT_MODEL=\"$OPENAI_STT_MODEL\" OPENAI_SUMMARY_MODEL=\"$OPENAI_SUMMARY_MODEL\" OPENAI_INDEXING_MODEL=\"$OPENAI_INDEXING_MODEL\" PGGSSENCMODE=\"$PGGSSENCMODE\" uv run --no-project --python \"$API_PYTHON\" --with-requirements requirements.txt uvicorn app.main:app --host 0.0.0.0 --port \"$API_PORT\" --reload"
start_process "worker" "cd \"$ROOT_DIR/apps/api\" && DATABASE_URL=\"$DATABASE_URL\" REDIS_URL=\"$REDIS_URL\" OPENAI_API_KEY=\"$OPENAI_API_KEY\" OPENAI_BASE_URL=\"$OPENAI_BASE_URL\" OPENAI_STT_MODEL=\"$OPENAI_STT_MODEL\" OPENAI_SUMMARY_MODEL=\"$OPENAI_SUMMARY_MODEL\" OPENAI_INDEXING_MODEL=\"$OPENAI_INDEXING_MODEL\" PGGSSENCMODE=\"$PGGSSENCMODE\" VOICEVAULT_SIMPLE_WORKER=\"$VOICEVAULT_SIMPLE_WORKER\" uv run --no-project --python \"$API_PYTHON\" --with-requirements requirements.txt python -m app.worker"
start_process "web" "cd \"$ROOT_DIR/apps/web\" && VITE_API_BASE_URL=\"$VITE_API_BASE_URL\" npm run dev -- --host 0.0.0.0 --port \"$WEB_PORT\""

log "VoiceVault local dev stack is running."
log "API: http://localhost:${API_PORT} | Web: http://localhost:${WEB_PORT}"
log "Press Ctrl+C to stop."

wait_until_any_exits
