#!/bin/bash
# Ralphex task parser (portable bash implementation)

set -euo pipefail

_parse_tasks_stream() {
  local workspace="${1:-.}"
  local task_file="$workspace/RALPH_TASK.md"
  local line_no=0

  [[ -f "$task_file" ]] || return 1

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_no=$((line_no + 1))

    if [[ "$line" =~ ^[[:space:]]*([-*]|[0-9]+\.)[[:space:]]+\[([xX[:space:]])\][[:space:]]+(.*)$ ]]; then
      local status_char="${BASH_REMATCH[2]}"
      local desc="${BASH_REMATCH[3]}"
      local status="pending"
      local group="999999"
      local id="line_${line_no}"

      if [[ "$status_char" == "x" || "$status_char" == "X" ]]; then
        status="completed"
      fi

      if [[ "$line" =~ \<\!--[[:space:]]*group:[[:space:]]*([0-9]+)[[:space:]]*--\> ]]; then
        group="${BASH_REMATCH[1]}"
      fi

      desc=$(echo "$desc" | sed -E 's/[[:space:]]*<!--[[:space:]]*group:[[:space:]]*[0-9]+[[:space:]]*-->[[:space:]]*//g')
      printf '%s|%s|%s|%s|%s\n' "$id" "$status" "$group" "$desc" "$line_no"
    fi
  done < "$task_file"
}

parse_tasks() {
  local workspace="${1:-.}"
  [[ -f "$workspace/RALPH_TASK.md" ]]
}

get_all_tasks() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace" | awk -F'|' '{print $1 "|" $2 "|" $4}'
}

get_all_tasks_with_group() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace"
}

get_pending_groups() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace" | awk -F'|' '$2=="pending" {print $3}' | sort -n | uniq
}

get_tasks_by_group() {
  local workspace="${1:-.}"
  local group="$2"
  _parse_tasks_stream "$workspace" | awk -F'|' -v g="$group" '$2=="pending" && $3==g {print}'
}

get_next_task() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace" | awk -F'|' '$2=="pending" {print $1 "|" $4 "|" $5; exit}'
}

get_task_by_id() {
  local workspace="${1:-.}"
  local task_id="$2"
  _parse_tasks_stream "$workspace" | awk -F'|' -v id="$task_id" '$1==id {print; exit}'
}

mark_task_complete() {
  local workspace="${1:-.}"
  local task_id="$2"
  local task_file="$workspace/RALPH_TASK.md"
  local line_no

  line_no=$(echo "$task_id" | sed -E 's/^line_([0-9]+)$/\1/')
  [[ "$line_no" =~ ^[0-9]+$ ]] || { echo "invalid task id: $task_id" >&2; return 1; }

  if [[ "$OSTYPE" == darwin* ]]; then
    sed -i '' "${line_no}s/\[ \]/[x]/;${line_no}s/\[X\]/[x]/" "$task_file"
  else
    sed -i "${line_no}s/\[ \]/[x]/;${line_no}s/\[X\]/[x]/" "$task_file"
  fi
}

mark_task_incomplete() {
  local workspace="${1:-.}"
  local task_id="$2"
  local task_file="$workspace/RALPH_TASK.md"
  local line_no

  line_no=$(echo "$task_id" | sed -E 's/^line_([0-9]+)$/\1/')
  [[ "$line_no" =~ ^[0-9]+$ ]] || { echo "invalid task id: $task_id" >&2; return 1; }

  if [[ "$OSTYPE" == darwin* ]]; then
    sed -i '' "${line_no}s/\[[xX]\]/[ ]/" "$task_file"
  else
    sed -i "${line_no}s/\[[xX]\]/[ ]/" "$task_file"
  fi
}

get_progress() {
  local workspace="${1:-.}"
  local total done

  total=$(_parse_tasks_stream "$workspace" | wc -l | tr -d ' ')
  done=$(_parse_tasks_stream "$workspace" | awk -F'|' '$2=="completed"' | wc -l | tr -d ' ')
  echo "$done|$total"
}
