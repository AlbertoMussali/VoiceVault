#!/bin/bash
# Ralphex CLI presentation helpers

set -euo pipefail

if [[ -n "${RALPHEX_UI_SH_LOADED:-}" ]]; then
  return 0
fi
RALPHEX_UI_SH_LOADED=1

_ui_prefix() {
  local stage="$1"
  shift || true
  echo "[$stage] $*"
}

ui_print_run_header() {
  local workspace="$1"
  local run_id="$2"
  local base_branch="$3"
  local mode="$4"
  local model="$5"
  local sandbox="$6"

  _ui_prefix "Plan" "Workspace: $workspace"
  _ui_prefix "Plan" "Run ID: $run_id"
  _ui_prefix "Plan" "Base branch: $base_branch"
  _ui_prefix "Plan" "Execution mode: $mode"
  _ui_prefix "Plan" "Model: $model | Sandbox: $sandbox"
}

ui_run_doctor_or_exit() {
  local workspace="$1"
  local had_errors=0
  local had_warnings=0

  _ui_prefix "Doctor" "Running preflight checks..."

  if git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1; then
    local base
    if git -C "$workspace" show-ref --verify --quiet refs/heads/main; then
      base="main"
    elif git -C "$workspace" show-ref --verify --quiet refs/heads/master; then
      base="master"
    else
      base="$(git -C "$workspace" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    fi
    _ui_prefix "Doctor" "OK: git repo detected (base=$base)"
  else
    _ui_prefix "Doctor" "FAIL: git repository not found"
    had_errors=1
  fi

  if [[ -n "$(git -C "$workspace" ls-files -u 2>/dev/null)" ]]; then
    _ui_prefix "Doctor" "FAIL: unresolved merge conflicts present"
    had_errors=1
  else
    _ui_prefix "Doctor" "OK: no merge conflicts"
  fi

  if command -v codex >/dev/null 2>&1; then
    _ui_prefix "Doctor" "OK: codex available"
  else
    _ui_prefix "Doctor" "FAIL: codex missing in PATH"
    had_errors=1
  fi

  if command -v jq >/dev/null 2>&1; then
    _ui_prefix "Doctor" "OK: jq available"
  else
    _ui_prefix "Doctor" "FAIL: jq missing in PATH"
    had_errors=1
  fi

  if [[ -f "$workspace/AGENTS.md" ]]; then
    _ui_prefix "Doctor" "OK: AGENTS.md present"
  else
    _ui_prefix "Doctor" "WARN: AGENTS.md missing"
    had_warnings=1
  fi

  local task_file
  if [[ -f "$workspace/RALPHEX_TASK.md" ]]; then
    task_file="RALPHEX_TASK.md"
  elif [[ -f "$workspace/RALPH_TASK.md" ]]; then
    task_file="RALPH_TASK.md"
  else
    task_file=""
  fi
  if [[ -n "$task_file" ]]; then
    _ui_prefix "Doctor" "OK: $task_file present"
  else
    _ui_prefix "Doctor" "FAIL: task file missing (RALPHEX_TASK.md/RALPH_TASK.md)"
    had_errors=1
  fi

  if ! git -C "$workspace" diff --quiet || ! git -C "$workspace" diff --cached --quiet; then
    _ui_prefix "Doctor" "WARN: tracked changes present (parallel launch may be refused)"
    had_warnings=1
  else
    _ui_prefix "Doctor" "OK: tracked tree clean"
  fi

  _ui_prefix "Doctor" "Model preflight will run next."

  if [[ "$had_errors" -eq 1 ]]; then
    _ui_prefix "Doctor" "Result: FAIL (blocking)"
    return 2
  fi
  if [[ "$had_warnings" -eq 1 ]]; then
    _ui_prefix "Doctor" "Result: WARN (continuing)"
    return 0
  fi
  _ui_prefix "Doctor" "Result: OK"
  return 0
}

ui_print_task_inventory() {
  local workspace="$1"
  local json
  json=$(get_inventory_counts "$workspace")
  local total completed pending groups_total groups_pending
  total=$(echo "$json" | jq -r '.total_tasks')
  completed=$(echo "$json" | jq -r '.completed_tasks')
  pending=$(echo "$json" | jq -r '.pending_tasks')
  groups_total=$(echo "$json" | jq -r '.total_groups')
  groups_pending=$(echo "$json" | jq -r '.pending_groups')

  _ui_prefix "Plan" "Tasks: total=$total completed=$completed pending=$pending"
  _ui_prefix "Plan" "Groups: total=$groups_total pending=$groups_pending completed=$((groups_total-groups_pending))"
}

ui_print_group_plan() {
  local workspace="$1"
  local group="$2"
  local blocked="${3:-0}"
  local counts_json
  counts_json=$(get_group_counts "$workspace" "$group")
  local total completed pending
  total=$(echo "$counts_json" | jq -r '.total')
  completed=$(echo "$counts_json" | jq -r '.completed')
  pending=$(echo "$counts_json" | jq -r '.pending')

  _ui_prefix "Group $group" "Plan: total=$total | pending=$pending | already-completed/skipped=$completed | blocked=$blocked"
  echo "$counts_json"
}

ui_detect_group_mode() {
  local workspace="$1"
  local group="$2"
  local max_parallel="$3"
  local pending
  pending=$(get_tasks_by_group "$workspace" "$group" || true)
  if [[ "$max_parallel" -le 1 ]]; then
    echo "sequential"
    return 0
  fi
  local seq_count non_seq_count
  seq_count=$(echo "$pending" | awk -F'|' '$8=="true"' | sed '/^$/d' | wc -l | tr -d ' ')
  non_seq_count=$(echo "$pending" | awk -F'|' '$8!="true"' | sed '/^$/d' | wc -l | tr -d ' ')
  if [[ "$seq_count" -gt 0 && "$non_seq_count" -gt 0 ]]; then
    echo "mixed"
  elif [[ "$seq_count" -gt 0 ]]; then
    echo "sequential"
  else
    echo "parallel"
  fi
}

ui_print_group_start() {
  local group="$1"
  local mode="$2"
  local counts_json="$3"
  local pending completed
  pending=$(echo "$counts_json" | jq -r '.pending')
  completed=$(echo "$counts_json" | jq -r '.completed')
  _ui_prefix "Group $group" "Starting group in $mode mode (pending=$pending, skipped=$completed)"
}

ui_print_group_task_result() {
  local task_id="$1"
  local result="$2"
  local reason="${3:-}"
  if [[ -n "$reason" ]]; then
    _ui_prefix "Task $task_id" "$result ($reason)"
  else
    _ui_prefix "Task $task_id" "$result"
  fi
}

ui_print_orchestrator_start() {
  local group="$1"
  _ui_prefix "Orchestrator g$group" "Entering orchestrator stage (merge -> checkpoint -> cleanup)"
}

ui_print_orchestrator_step() {
  local group="$1"
  local step="$2"
  local detail="${3:-}"
  _ui_prefix "Orchestrator g$group" "$step${detail:+: $detail}"
}

ui_print_orchestrator_done() {
  local group="$1"
  local status="$2"
  local commit_sha="${3:-}"
  if [[ -n "$commit_sha" ]]; then
    _ui_prefix "Orchestrator g$group" "Done: $status (commit=$commit_sha)"
  else
    _ui_prefix "Orchestrator g$group" "Done: $status"
  fi
}

ui_print_group_done() {
  local group="$1"
  local stats_json="$2"
  local merged failed blocked
  merged=$(echo "$stats_json" | jq -r '.merged // 0')
  failed=$(echo "$stats_json" | jq -r '.failed // 0')
  blocked=$(echo "$stats_json" | jq -r '.blocked // 0')
  _ui_prefix "Group $group" "Completed (merged=$merged failed=$failed blocked=$blocked)"
}

ui_print_final_summary() {
  local run_id="$1"
  local aggregate_json="$2"
  _ui_prefix "Summary" "Run $run_id complete summary:"
  _ui_prefix "Summary" "Groups: completed=$(echo "$aggregate_json" | jq -r '.groups.completed') failed=$(echo "$aggregate_json" | jq -r '.groups.failed') skipped=$(echo "$aggregate_json" | jq -r '.groups.skipped')"
  _ui_prefix "Summary" "Tasks: executed=$(echo "$aggregate_json" | jq -r '.tasks.executed') merged=$(echo "$aggregate_json" | jq -r '.tasks.merged') skipped=$(echo "$aggregate_json" | jq -r '.tasks.skipped') blocked=$(echo "$aggregate_json" | jq -r '.tasks.blocked') failed=$(echo "$aggregate_json" | jq -r '.tasks.failed')"
  _ui_prefix "Summary" "Orchestrator: attempted=$(echo "$aggregate_json" | jq -r '.orchestrator.attempted') succeeded=$(echo "$aggregate_json" | jq -r '.orchestrator.succeeded') failed=$(echo "$aggregate_json" | jq -r '.orchestrator.failed') cleanup_ok=$(echo "$aggregate_json" | jq -r '.orchestrator.cleanup_ok')"
  _ui_prefix "Summary" "Head: $(echo "$aggregate_json" | jq -r '.head.sha') on $(echo "$aggregate_json" | jq -r '.head.branch') | main_clean=$(echo "$aggregate_json" | jq -r '.head.main_clean')"
  local next_action
  next_action=$(echo "$aggregate_json" | jq -r '.next_action')
  [[ -n "$next_action" && "$next_action" != "null" ]] && _ui_prefix "Summary" "Next: $next_action"
}
