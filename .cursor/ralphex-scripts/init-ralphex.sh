#!/bin/bash
# Initialize Ralphex in a project

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="${1:-.}"
TARGET="$(cd "$TARGET" && pwd)"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is required." >&2
  exit 1
fi

mkdir -p "$TARGET/.cursor/ralphex-scripts"
mkdir -p "$TARGET/.ralph"

if [[ ! -f "$TARGET/RALPH_TASK.md" ]]; then
  cat > "$TARGET/RALPH_TASK.md" <<'EOT'
---
task: Describe your task
test_command: "echo no tests configured"
---

# Task

Describe what to build.

## Success Criteria

1. [ ] First criterion
2. [ ] Second criterion
EOT
fi

for f in "$SCRIPT_DIR"/*.sh; do
  cp "$f" "$TARGET/.cursor/ralphex-scripts/$(basename "$f")"
  chmod +x "$TARGET/.cursor/ralphex-scripts/$(basename "$f")"
done

[[ -f "$TARGET/.ralph/.iteration" ]] || echo "0" > "$TARGET/.ralph/.iteration"
[[ -f "$TARGET/.ralph/session_id" ]] || : > "$TARGET/.ralph/session_id"
[[ -f "$TARGET/.ralph/activity.log" ]] || echo "# Activity Log" > "$TARGET/.ralph/activity.log"
[[ -f "$TARGET/.ralph/errors.log" ]] || echo "# Error Log" > "$TARGET/.ralph/errors.log"
[[ -f "$TARGET/.ralph/progress.md" ]] || echo "# Progress Log" > "$TARGET/.ralph/progress.md"
[[ -f "$TARGET/.ralph/guardrails.md" ]] || echo "# Ralphex Guardrails" > "$TARGET/.ralph/guardrails.md"

if [[ -f "$TARGET/.gitignore" ]]; then
  if ! grep -q '^\.cursor/ralph-config\.json$' "$TARGET/.gitignore"; then
    {
      echo ""
      echo "# Ralphex config"
      echo ".cursor/ralphex-config.json"
    } >> "$TARGET/.gitignore"
  fi
else
  echo ".cursor/ralphex-config.json" > "$TARGET/.gitignore"
fi

echo "Ralphex initialized at $TARGET"
echo "Run: $TARGET/.cursor/ralphex-scripts/ralphex-loop.sh"
