#!/bin/bash
# Ralphex loop runner

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"
source "$SCRIPT_DIR/ralphex-parallel.sh"

PARALLEL_MODE=false
MAX_PARALLEL=3
USE_BRANCH=""
OPEN_PR=false
SKIP_CONFIRM=false
WORKSPACE=""

show_help() {
  cat <<'EOT'
Ralphex Loop

Usage:
  ./ralphex-loop.sh [options] [workspace]

Options:
  -m, --model MODEL
  -n, --iterations N
  -s, --sandbox MODE              (workspace-write|danger-full-access|read-only)
  --parallel
  --max-parallel N
  --branch NAME
  --pr
  -y, --yes
  -h, --help
EOT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--model)
      MODEL="$2"; shift 2 ;;
    -n|--iterations)
      MAX_ITERATIONS="$2"; shift 2 ;;
    -s|--sandbox)
      SANDBOX="$2"; shift 2 ;;
    --parallel)
      PARALLEL_MODE=true; shift ;;
    --max-parallel)
      MAX_PARALLEL="$2"; PARALLEL_MODE=true; shift 2 ;;
    --branch)
      USE_BRANCH="$2"; shift 2 ;;
    --pr)
      OPEN_PR=true; shift ;;
    -y|--yes)
      SKIP_CONFIRM=true; shift ;;
    -h|--help)
      show_help; exit 0 ;;
    -*)
      echo "Unknown option: $1" >&2; exit 1 ;;
    *)
      WORKSPACE="$1"; shift ;;
  esac
done

if [[ -z "$WORKSPACE" ]]; then
  WORKSPACE="$(pwd)"
else
  WORKSPACE="$(cd "$WORKSPACE" && pwd)"
fi

show_banner

if ! check_prerequisites "$WORKSPACE"; then
  exit 1
fi

init_ralph_dir "$WORKSPACE"
show_task_summary "$WORKSPACE"

echo "Model: $MODEL"
echo "Sandbox: $SANDBOX"
echo "Max iterations: $MAX_ITERATIONS"

echo "Preflight: validating model access..."
set +e
preflight_out=$(
  (cd "$WORKSPACE" && codex exec --json --sandbox read-only --model "$MODEL" "Reply with OK only.") 2>&1
)
preflight_rc=$?
set -e
if [[ "$preflight_rc" -ne 0 ]]; then
  echo "Preflight failed. Codex could not run with model: $MODEL" >&2
  echo "$preflight_out" | tail -n 25 >&2
  exit 2
fi

if [[ -n "$USE_BRANCH" ]]; then
  git -C "$WORKSPACE" checkout -B "$USE_BRANCH"
fi

if [[ "$SKIP_CONFIRM" != true ]]; then
  read -r -p "Start Ralphex loop? [y/N] " reply
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

if [[ "$PARALLEL_MODE" == true ]]; then
  run_parallel_tasks "$WORKSPACE" "$MAX_PARALLEL" "$USE_BRANCH" "$MAX_ITERATIONS"
  rc=$?
else
  run_ralphex_loop "$WORKSPACE" "$SCRIPT_DIR"
  rc=$?
fi

if [[ "$OPEN_PR" == true ]] && command -v gh >/dev/null 2>&1; then
  current_branch=$(git -C "$WORKSPACE" rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "main" ]] && [[ "$current_branch" != "master" ]]; then
    (cd "$WORKSPACE" && gh pr create --fill) || true
  fi
fi

exit "$rc"
