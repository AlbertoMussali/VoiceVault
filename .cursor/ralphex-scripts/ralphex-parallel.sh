#!/bin/bash
# Ralphex parallel runner (worktree based)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-task-parser.sh"

# When sourced from ralphex-common.sh these may already be set.
MODEL="${MODEL:-${RALPHEX_MODEL:-gpt-5.3-codex}}"
SANDBOX="${SANDBOX:-${RALPHEX_SANDBOX:-workspace-write}}"

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

_run_agent_in_worktree() {
  local workspace="$1"
  local run_id="$2"
  local task_id="$3"
  local task_desc="$4"
  local line_no="$5"
  local job_id="$6"

  local wt_base="$workspace/.ralph-worktrees/$run_id"
  local wt_dir="$wt_base/job-$job_id"
  local status_dir="$workspace/.ralph/parallel/$run_id"
  local status_file="$status_dir/job-$job_id.status"
  local log_file="$status_dir/job-$job_id.log"

  mkdir -p "$wt_base" "$status_dir"

  local branch="ralphex/parallel-${run_id}-${job_id}-${task_id}-$(slugify "$task_desc")"

  if ! git -C "$workspace" worktree add -f -b "$branch" "$wt_dir" HEAD > "$log_file" 2>&1; then
    echo "FAILED|$task_id|$branch|$wt_dir|worktree_create" > "$status_file"
    return 0
  fi

  local prompt
  prompt=$(cat <<EOT
You are running in Ralphex parallel mode.
Complete ONLY this task from RALPH_TASK.md and then stop:
- id: $task_id
- line: $line_no
- description: $task_desc

Rules:
1. Implement required file changes for this task only.
2. Mark this specific checkbox line as [x].
3. Run relevant tests.
4. Do not edit unrelated task checkboxes.
5. End with a concise summary.
EOT
)

  set +e
  (
    cd "$wt_dir" || exit 1
    codex exec --json --sandbox "$SANDBOX" --model "$MODEL" "$prompt"
  ) >> "$log_file" 2>&1
  local rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    if ! git -C "$wt_dir" diff --quiet; then
      git -C "$wt_dir" add -A
      if ! git -C "$wt_dir" -c user.name="ralphex" -c user.email="ralphex@local" commit -m "ralphex: complete $task_id" >> "$log_file" 2>&1; then
        echo "FAILED|$task_id|$branch|$wt_dir|commit_failed" > "$status_file"
        return 0
      fi
    fi
    echo "SUCCESS|$task_id|$branch|$wt_dir|ok" > "$status_file"
  else
    echo "FAILED|$task_id|$branch|$wt_dir|codex_failed" > "$status_file"
  fi

  return 0
}

_merge_success_branch() {
  local workspace="$1"
  local branch="$2"
  local log_file="${3:-/dev/null}"

  # Prefer fast-forward merges for parallel task branches to avoid requiring
  # user git identity and to reduce merge commits/noise.
  if git -C "$workspace" merge --ff-only "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  # If ff-only is not possible, fall back to a merge commit with a local identity.
  if git -C "$workspace" -c user.name="ralphex" -c user.email="ralphex@local" merge --no-ff --no-edit "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  git -C "$workspace" merge --abort >/dev/null 2>&1 || true
  return 1
}

_cleanup_worktree() {
  local workspace="$1"
  local wt_dir="$2"
  git -C "$workspace" worktree remove -f "$wt_dir" >/dev/null 2>&1 || true
}

run_parallel_tasks() {
  local workspace="$1"
  local max_parallel="${2:-3}"
  local integration_branch="${3:-}"
  local max_tasks="${4:-0}"

  if ! git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Parallel mode requires a git repository." >&2
    return 1
  fi

  local run_id
  run_id=$(date '+%Y%m%d%H%M%S')
  local status_dir="$workspace/.ralph/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  mkdir -p "$status_dir"

  if [[ -n "$integration_branch" ]]; then
    git -C "$workspace" checkout -B "$integration_branch"
  fi

  echo "Ralphex parallel run: $run_id"

  local groups
  groups=$(get_pending_groups "$workspace" || true)
  if [[ -z "$groups" ]]; then
    echo "No pending tasks."
    return 0
  fi

  local merged_count=0
  local failed_count=0
  local launched_tasks=0
  local stop=0
  local job_global=0

  while IFS= read -r group || [[ -n "$group" ]]; do
    if [[ "$stop" -eq 1 ]]; then
      break
    fi
    [[ -z "$group" ]] && continue

    echo "Processing group $group"
    local tasks
    tasks=$(get_tasks_by_group "$workspace" "$group" || true)
    [[ -z "$tasks" ]] && continue

    local pids=""
    local group_status_files=""

    while IFS='|' read -r task_id status group_num task_desc line_no || [[ -n "$task_id" ]]; do
      [[ -z "$task_id" ]] && continue
      job_global=$((job_global + 1))

      group_status_files="$group_status_files $status_dir/job-$job_global.status"
      _run_agent_in_worktree "$workspace" "$run_id" "$task_id" "$task_desc" "$line_no" "$job_global" &
      pids="$pids $!"
      launched_tasks=$((launched_tasks + 1))

      if [[ "$max_tasks" -gt 0 ]] && [[ "$launched_tasks" -ge "$max_tasks" ]]; then
        stop=1
      fi

      # Batch by max_parallel (portable for bash 3)
      if [[ $((job_global % max_parallel)) -eq 0 ]]; then
        for pid in $pids; do wait "$pid"; done
        pids=""
      fi

      if [[ "$stop" -eq 1 ]]; then
        break
      fi
    done <<< "$tasks"

    for pid in $pids; do wait "$pid"; done

    local status_file
    for status_file in $group_status_files; do
      [[ -f "$status_file" ]] || continue
      local outcome task_id branch wt_dir reason
      IFS='|' read -r outcome task_id branch wt_dir reason < "$status_file"

      if [[ "$outcome" == "SUCCESS" ]]; then
        if _merge_success_branch "$workspace" "$branch" "$merge_log"; then
          mark_task_complete "$workspace" "$task_id" || true
          # Keep workspace clean so subsequent merges don't fail due to local
          # checkbox edits in RALPH_TASK.md.
          if ! git -C "$workspace" diff --quiet -- RALPH_TASK.md 2>/dev/null; then
            git -C "$workspace" add RALPH_TASK.md >/dev/null 2>&1 || true
            git -C "$workspace" -c user.name="ralphex" -c user.email="ralphex@local" commit -m "ralphex: mark $task_id complete" >>"$merge_log" 2>&1 || true
          fi
          merged_count=$((merged_count + 1))
        else
          echo "Merge failed for $branch" >> "$status_dir/merge_failures.log"
          failed_count=$((failed_count + 1))
        fi
      else
        echo "$task_id failed: $reason" >> "$status_dir/failures.log"
        failed_count=$((failed_count + 1))
      fi

      _cleanup_worktree "$workspace" "$wt_dir"
      git -C "$workspace" branch -D "$branch" >/dev/null 2>&1 || true
      rm -f "$status_file" >/dev/null 2>&1 || true
    done
  done <<< "$groups"

  echo "Parallel summary: launched=$launched_tasks merged=$merged_count failed=$failed_count"

  if [[ "$failed_count" -gt 0 ]]; then
    return 1
  fi
  return 0
}
