#!/bin/bash
# Ralphex parallel runner (worktree based)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"

# When sourced from ralphex-common.sh these may already be set; keep defensive defaults.
MODEL="${MODEL:-${RALPHEX_MODEL:-$DEFAULT_MODEL}}"
SANDBOX="${SANDBOX:-${RALPHEX_SANDBOX:-workspace-write}}"

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

_run_log_json() {
  local jobs_file="$1"
  shift
  jq -nc "$@" >>"$jobs_file"
}

_read_agents_md_snippet() {
  local dir="$1"
  local f="$dir/AGENTS.md"
  if [[ -f "$f" ]]; then
    sed -n '1,240p' "$f"
  fi
}

_sync_agent_context() {
  local workspace="$1"
  local wt_dir="$2"
  mkdir -p "$wt_dir/.ralph"
  cp -f "$workspace/.ralph/guardrails.md" "$wt_dir/.ralph/guardrails.md" 2>/dev/null || true
  cp -f "$workspace/.ralph/progress.md" "$wt_dir/.ralph/progress.md" 2>/dev/null || true
  cp -f "$workspace/.ralph/errors.log" "$wt_dir/.ralph/errors.log" 2>/dev/null || true
  cp -f "$workspace/.ralph/activity.log" "$wt_dir/.ralph/activity.log" 2>/dev/null || true
  cp -f "$workspace/AGENTS.md" "$wt_dir/AGENTS.md" 2>/dev/null || true
}

_create_integration_worktree() {
  local workspace="$1"
  local run_id="$2"
  local integration_branch="$3"
  local base_ref="$4"
  local status_dir="$workspace/.ralph/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local integration_dir="$workspace/.ralph/integration/$run_id"

  mkdir -p "$status_dir" "$(dirname "$integration_dir")"
  {
    echo "run_id=$run_id"
    echo "integration_branch=$integration_branch"
    echo "base_ref=$base_ref"
    echo "created_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  } >"$status_dir/run.meta"

  if [[ -d "$integration_dir" ]]; then
    echo "$integration_dir"
    return 0
  fi

  if git -C "$workspace" show-ref --verify --quiet "refs/heads/$integration_branch"; then
    git -C "$workspace" worktree add -f "$integration_dir" "$integration_branch" >>"$merge_log" 2>&1
  else
    git -C "$workspace" worktree add -f -b "$integration_branch" "$integration_dir" "$base_ref" >>"$merge_log" 2>&1
  fi
  echo "$integration_dir"
}

_run_agent_in_worktree() {
  local workspace="$1"
  local run_id="$2"
  local base_ref="$3"
  local task_id="$4"
  local task_desc="$5"
  local line_no="$6"
  local job_id="$7"

  local wt_base="$workspace/.ralph-worktrees/$run_id"
  local wt_dir="$wt_base/job-$job_id"
  local status_dir="$workspace/.ralph/parallel/$run_id"
  local status_file="$status_dir/job-$job_id.status"
  local log_file="$status_dir/job-$job_id.log"
  local jobs_file="$status_dir/jobs.jsonl"

  mkdir -p "$wt_base" "$status_dir"

  local branch="ralphex/parallel-${run_id}-${job_id}-${task_id}-$(slugify "$task_desc")"

  if ! git -C "$workspace" worktree add -f -b "$branch" "$wt_dir" "$base_ref" > "$log_file" 2>&1; then
    echo "FAILED|$task_id|$branch|$wt_dir|worktree_create" > "$status_file"
    _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "FAILED" --arg reason "worktree_create" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
    return 0
  fi

  _sync_agent_context "$workspace" "$wt_dir"

  local agents_md
  agents_md=$(_read_agents_md_snippet "$wt_dir" || true)

  local prompt
  prompt=$(cat <<EOT
You are running in Ralphex parallel mode.

Read and follow AGENTS.md FIRST. Content (truncated):
----------------
${agents_md:-"(AGENTS.md not found)"}
----------------

Complete ONLY this task from RALPH_TASK.md and then stop:
- id: $task_id
- line: $line_no
- description: $task_desc

Rules:
1. Implement required file changes for this task only.
2. Do NOT modify RALPH_TASK.md. The orchestrator will mark tasks complete.
3. Do NOT modify anything under .ralph/.
4. Do NOT commit; leave changes unstaged or staged.
5. End with a concise summary.

Read these files before acting:
- AGENTS.md
- RALPH_TASK.md
- .ralph/guardrails.md
- .ralph/progress.md
- .ralph/errors.log
EOT
)

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "STARTED" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status}'

  set +e
  (
    cd "$wt_dir" || exit 1
    codex exec --json --sandbox "$SANDBOX" --model "$MODEL" "$prompt"
  ) >> "$log_file" 2>&1
  local rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    # Enforce: never commit task/log changes from the agent worktree.
    git -C "$wt_dir" restore -SW -- RALPH_TASK.md 2>/dev/null || true
    rm -rf "$wt_dir/.ralph" >/dev/null 2>&1 || true

    if ! git -C "$wt_dir" diff --quiet; then
      git -C "$wt_dir" add -A
      git -C "$wt_dir" reset -q -- RALPH_TASK.md 2>/dev/null || true
      git -C "$wt_dir" reset -q -- .ralph 2>/dev/null || true
      if ! git -C "$wt_dir" diff --cached --quiet; then
        if ! git -C "$wt_dir" -c user.name="ralphex" -c user.email="ralphex@local" commit -m "ralphex: complete $task_id" >> "$log_file" 2>&1; then
          echo "FAILED|$task_id|$branch|$wt_dir|commit_failed" > "$status_file"
          _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "FAILED" --arg reason "commit_failed" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
          return 0
        fi
      fi
    fi

    local sha
    sha=$(git -C "$wt_dir" rev-parse HEAD 2>/dev/null || echo "")
    echo "SUCCESS|$task_id|$branch|$wt_dir|ok|$sha" > "$status_file"
    _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "SUCCESS" --arg sha "$sha" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, sha:$sha}'
  else
    echo "FAILED|$task_id|$branch|$wt_dir|codex_failed" > "$status_file"
    _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "FAILED" --arg reason "codex_failed" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
  fi

  return 0
}

_merge_success_branch() {
  local repo_dir="$1"
  local branch="$2"
  local log_file="${3:-/dev/null}"

  # Prefer fast-forward merges for parallel task branches to avoid requiring
  # user git identity and to reduce merge commits/noise.
  if git -C "$repo_dir" merge --ff-only "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  # If ff-only is not possible, fall back to a merge commit with a local identity.
  if git -C "$repo_dir" -c user.name="ralphex" -c user.email="ralphex@local" merge --no-ff --no-edit "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  git -C "$repo_dir" merge --abort >/dev/null 2>&1 || true
  return 1
}

_cleanup_worktree() {
  local workspace="$1"
  local wt_dir="$2"
  git -C "$workspace" worktree remove -f "$wt_dir" >/dev/null 2>&1 || true
}

_auto_resolve_merge_conflict() {
  local workspace="$1"
  local run_id="$2"
  local integration_branch="$3"
  local task_id="$4"
  local task_branch="$5"
  local status_dir="$workspace/.ralph/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"

  local mergefix_branch="ralphex/mergefix-${run_id}-${task_id}"
  local fix_dir="$workspace/.ralph/merge-fix/$run_id/$task_id"
  mkdir -p "$(dirname "$fix_dir")"

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$task_branch" --arg status "MERGE_FIX_STARTED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'

  if ! git -C "$workspace" worktree add -f -b "$mergefix_branch" "$fix_dir" "$integration_branch" >>"$merge_log" 2>&1; then
    echo "merge-fix worktree create failed for $mergefix_branch" >>"$merge_log"
    return 1
  fi

  set +e
  (cd "$fix_dir" && git merge "$task_branch") >>"$merge_log" 2>&1
  local merge_rc=$?
  set -e

  if [[ "$merge_rc" -ne 0 ]]; then
    local conflicts
    conflicts=$(cd "$fix_dir" && git diff --name-only --diff-filter=U || true)

    local agents_md
    agents_md=$(_read_agents_md_snippet "$fix_dir" || true)

    local prompt
    prompt=$(cat <<EOT
You are resolving a git merge conflict in VoiceVault.

Read and follow AGENTS.md FIRST. Content (truncated):
----------------
${agents_md:-"(AGENTS.md not found)"}
----------------

Goal:
- Resolve merge conflicts and keep the intent of BOTH branches.

Hard rules:
1. Do NOT modify RALPH_TASK.md or anything under .ralph/.
2. Prefer keeping both changes. If truly incompatible, keep the integration branch behavior unless the task branch is clearly the intended new behavior.
3. After resolving conflicts, run the repo test command if available and report results.

Conflict files (from git):
${conflicts:-"(unable to list conflicts)"}
EOT
)

    set +e
    (cd "$fix_dir" && codex exec --json --sandbox "$SANDBOX" --model "$MODEL" "$prompt") >>"$merge_log" 2>&1
    local rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
      echo "merge-fix codex failed for $mergefix_branch" >>"$merge_log"
      return 1
    fi
  fi

  # Enforce: never commit task/log changes.
  (cd "$fix_dir" && git restore -SW -- RALPH_TASK.md 2>/dev/null || true)
  rm -rf "$fix_dir/.ralph" >/dev/null 2>&1 || true

  local unresolved
  unresolved=$(cd "$fix_dir" && git diff --name-only --diff-filter=U || true)
  if [[ -n "$unresolved" ]]; then
    echo "merge-fix unresolved conflicts for $mergefix_branch: $unresolved" >>"$merge_log"
    return 1
  fi

  (cd "$fix_dir" && git add -A)
  (cd "$fix_dir" && git reset -q -- RALPH_TASK.md 2>/dev/null || true)
  (cd "$fix_dir" && git reset -q -- .ralph 2>/dev/null || true)

  if ! (cd "$fix_dir" && git -c user.name="ralphex" -c user.email="ralphex@local" commit --no-edit) >>"$merge_log" 2>&1; then
    echo "merge-fix commit failed for $mergefix_branch" >>"$merge_log"
    return 1
  fi

  # Best-effort tests (do not block run; log failures).
  local test_cmd
  test_cmd=$(extract_test_command "$fix_dir" || true)
  if [[ -n "$test_cmd" ]]; then
    set +e
    (cd "$fix_dir" && eval "$test_cmd") >>"$merge_log" 2>&1
    local test_rc=$?
    set -e
    if [[ "$test_rc" -ne 0 ]]; then
      echo "merge-fix test command failed: $test_cmd" >>"$merge_log"
    fi
  fi

  # Merge the merge-fix branch into integration.
  local integration_dir
  integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$integration_branch")"
  if ! _merge_success_branch "$integration_dir" "$mergefix_branch" "$merge_log"; then
    echo "merge-fix failed to merge back into integration: $mergefix_branch" >>"$merge_log"
    return 1
  fi

  git -C "$workspace" worktree remove -f "$fix_dir" >/dev/null 2>&1 || true
  git -C "$workspace" branch -D "$mergefix_branch" >/dev/null 2>&1 || true

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$task_branch" --arg status "MERGE_FIX_DONE" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
  return 0
}

_mark_task_complete_in_integration() {
  local integration_dir="$1"
  local task_id="$2"
  local merge_log="$3"

  mark_task_complete "$integration_dir" "$task_id" || true
  if ! git -C "$integration_dir" diff --quiet -- RALPH_TASK.md 2>/dev/null; then
    git -C "$integration_dir" add RALPH_TASK.md >/dev/null 2>&1 || true
    git -C "$integration_dir" -c user.name="ralphex" -c user.email="ralphex@local" commit -m "ralphex: mark $task_id complete" >>"$merge_log" 2>&1 || true
  fi
}

resume_parallel_run() {
  local workspace="$1"
  local run_id="$2"

  local status_dir="$workspace/.ralph/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"
  local meta="$status_dir/run.meta"

  [[ -f "$meta" ]] || { echo "No run meta found for run_id=$run_id" >&2; return 1; }

  local integration_branch
  integration_branch=$(awk -F= '$1=="integration_branch"{print $2}' "$meta")
  [[ -n "$integration_branch" ]] || { echo "Missing integration_branch in $meta" >&2; return 1; }

  local integration_dir
  integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$integration_branch")"

  echo "Resuming Ralphex parallel run: $run_id (integration=$integration_branch)"
  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "RESUME_STARTED" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'

  local status_file
  for status_file in "$status_dir"/job-*.status; do
    [[ -f "$status_file" ]] || continue
    local outcome task_id branch wt_dir reason sha
    IFS='|' read -r outcome task_id branch wt_dir reason sha < "$status_file"
    if [[ "$outcome" != "SUCCESS" ]]; then
      continue
    fi

    if _merge_success_branch "$integration_dir" "$branch" "$merge_log"; then
      _mark_task_complete_in_integration "$integration_dir" "$task_id" "$merge_log"
      _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
    else
      echo "Merge failed for $branch" >>"$status_dir/merge_failures.log"
      if ! _auto_resolve_merge_conflict "$workspace" "$run_id" "$integration_branch" "$task_id" "$branch"; then
        echo "Auto-resolve failed for $branch" >>"$status_dir/merge_failures.log"
        return 1
      fi
      _mark_task_complete_in_integration "$integration_dir" "$task_id" "$merge_log"
    fi
  done

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "RESUME_DONE" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'
  return 0
}

run_parallel_tasks() {
  local workspace="$1"
  local max_parallel="${2:-3}"
  local integration_branch="${3:-}"
  local max_tasks="${4:-0}"
  local run_id="${5:-}"
  local base_ref="${6:-main}"

  if ! git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Parallel mode requires a git repository." >&2
    return 1
  fi

  [[ -n "$run_id" ]] || run_id=$(date '+%Y%m%d%H%M%S')
  [[ -n "$integration_branch" ]] || integration_branch="ralphex/integration-$run_id"

  local status_dir="$workspace/.ralph/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"
  mkdir -p "$status_dir"

  init_ralph_dir "$workspace"
  log_activity "$workspace" "parallel run start: run_id=$run_id integration=$integration_branch base=$base_ref"

  echo "Ralphex parallel run: $run_id"

  local integration_dir
  integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$base_ref")"

  local groups
  groups=$(get_pending_groups "$integration_dir" || true)
  if [[ -z "$groups" ]]; then
    echo "No pending tasks."
    return 0
  fi

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg integration_branch "$integration_branch" --arg base_ref "$base_ref" --arg status "RUN_STARTED" '{ts:now|todateiso8601, run_id:$run_id, integration_branch:$integration_branch, base_ref:$base_ref, status:$status}'

  local merged_count=0
  local failed_count=0
  local launched_tasks=0
  local stop=0
  local job_global=0
  local fatal=0

  while IFS= read -r group || [[ -n "$group" ]]; do
    if [[ "$stop" -eq 1 ]]; then
      break
    fi
    [[ -z "$group" ]] && continue

    echo "Processing group $group"
    local tasks
    tasks=$(get_tasks_by_group "$integration_dir" "$group" || true)
    [[ -z "$tasks" ]] && continue

    local pids=""
    local group_status_files=""

    while IFS='|' read -r task_id status group_num task_desc line_no || [[ -n "$task_id" ]]; do
      [[ -z "$task_id" ]] && continue
      job_global=$((job_global + 1))

      group_status_files="$group_status_files $status_dir/job-$job_global.status"
      _run_agent_in_worktree "$workspace" "$run_id" "$integration_branch" "$task_id" "$task_desc" "$line_no" "$job_global" &
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
      local outcome task_id branch wt_dir reason sha
      IFS='|' read -r outcome task_id branch wt_dir reason sha < "$status_file"

      if [[ "$outcome" == "SUCCESS" ]]; then
        if _merge_success_branch "$integration_dir" "$branch" "$merge_log"; then
          _mark_task_complete_in_integration "$integration_dir" "$task_id" "$merge_log"
          _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
          merged_count=$((merged_count + 1))
        else
          echo "Merge failed for $branch" >> "$status_dir/merge_failures.log"
          if _auto_resolve_merge_conflict "$workspace" "$run_id" "$integration_branch" "$task_id" "$branch"; then
            integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$integration_branch")"
            _mark_task_complete_in_integration "$integration_dir" "$task_id" "$merge_log"
            _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGED_AFTER_FIX" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
            merged_count=$((merged_count + 1))
          else
            _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGE_FAILED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
            failed_count=$((failed_count + 1))
            fatal=1
            stop=1
          fi
        fi
      else
        echo "$task_id failed: $reason" >> "$status_dir/failures.log"
        _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "FAILED" --arg reason "$reason" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
        failed_count=$((failed_count + 1))
      fi

      _cleanup_worktree "$workspace" "$wt_dir"
      if [[ "$outcome" == "SUCCESS" ]]; then
        git -C "$workspace" branch -D "$branch" >/dev/null 2>&1 || true
      fi

      if [[ "$fatal" -eq 1 ]]; then
        break
      fi
    done

    if [[ "$fatal" -eq 1 ]]; then
      break
    fi
  done <<< "$groups"

  echo "Parallel summary: launched=$launched_tasks merged=$merged_count failed=$failed_count"
  log_activity "$workspace" "parallel run end: run_id=$run_id merged=$merged_count failed=$failed_count"
  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "RUN_DONE" --arg merged "$merged_count" --arg failed "$failed_count" '{ts:now|todateiso8601, run_id:$run_id, status:$status, merged:($merged|tonumber), failed:($failed|tonumber)}'

  if [[ "$failed_count" -gt 0 ]]; then
    return 1
  fi
  return 0
}

cleanup_parallel_run() {
  local workspace="$1"
  local run_id="$2"
  local integration_branch="$3"
  local integration_dir="$workspace/.ralph/integration/$run_id"
  git -C "$workspace" worktree remove -f "$integration_dir" >/dev/null 2>&1 || true
  git -C "$workspace" branch -D "$integration_branch" >/dev/null 2>&1 || true
}
