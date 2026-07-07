#!/usr/bin/env bash
set -euo pipefail

# memory.sh — durable shared memory for multi-agent work. Writes Markdown
# files with frontmatter (metadata.type: shared-agent-memory) plus a MEMORY.md
# index under a per-workspace directory, so Claude-style memory readers and
# Codex can both inspect the same namespace.

root="${AGENT_MEMORY_ROOT:-${AGENT_COLLAB_ROOT:-$HOME/.agents/memory/shared}}"

usage() {
  cat <<'USAGE'
usage:
  memory.sh init [label]
  memory.sh note <agent> <message...>
  memory.sh decision <agent> <message...>
  memory.sh task <agent> <open|doing|blocked|done> <message...>
  memory.sh handoff <from> <to> <message...>
  memory.sh show [path|index|workspace|journal|decisions|tasks|handoffs|all]

env:
  AGENT_MEMORY_ROOT   shared memory root (default: ~/.agents/memory/shared)
USAGE
}

now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

workspace_path() {
  if [ -n "${AGENT_MEMORY_WORKSPACE:-${AGENT_COLLAB_WORKSPACE:-}}" ]; then
    printf '%s' "${AGENT_MEMORY_WORKSPACE:-$AGENT_COLLAB_WORKSPACE}"
  else
    pwd -P
  fi
}

workspace_key() {
  local path base safe sum
  path="$(workspace_path)"
  base="$(basename "$path")"
  safe="$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '-')"
  sum="$(printf '%s' "$path" | cksum | awk '{print $1}')"
  printf '%s-%s' "$safe" "$sum"
}

workspace_dir() {
  printf '%s/%s' "$root" "$(workspace_key)"
}

ensure_markdown_file() {
  local file="$1" title="$2" name="$3" description="$4"
  if [ -f "$file" ]; then
    return
  fi
  {
    echo "---"
    printf 'name: %s\n' "$name"
    printf 'description: %s\n' "$description"
    echo "metadata:"
    echo "  type: shared-agent-memory"
    echo "---"
    echo
    printf '# %s\n' "$title"
  } > "$file"
}

ensure_workspace() {
  local dir path
  dir="$(workspace_dir)"
  path="$(workspace_path)"
  if ! mkdir -p "$dir" 2>/dev/null; then
    echo "error: cannot create shared memory at '$dir'" >&2
    echo "set AGENT_MEMORY_ROOT to a writable directory or grant write access" >&2
    exit 1
  fi
  if [ ! -f "$dir/MEMORY.md" ]; then
    {
      echo "# Memory index"
      echo
      echo "- [Workspace](workspace.md) - workspace path and key"
      echo "- [Tasks](tasks.md) - task ownership and status"
      echo "- [Decisions](decisions.md) - durable decisions and rationale"
      echo "- [Journal](journal.md) - chronological collaboration notes"
      echo "- [Handoffs](handoffs.md) - cross-agent handoff notes"
    } > "$dir/MEMORY.md"
  fi
  ensure_markdown_file "$dir/workspace.md" "Workspace" "shared-workspace" "Shared workspace metadata for agent collaboration"
  ensure_markdown_file "$dir/journal.md" "Journal" "shared-journal" "Chronological shared notes for agent collaboration"
  ensure_markdown_file "$dir/decisions.md" "Decisions" "shared-decisions" "Durable shared decisions for agent collaboration"
  ensure_markdown_file "$dir/tasks.md" "Tasks" "shared-tasks" "Shared task ownership and status for agent collaboration"
  ensure_markdown_file "$dir/handoffs.md" "Handoffs" "shared-handoffs" "Shared cross-agent handoff notes"
  if ! grep -q '^## Current Workspace$' "$dir/workspace.md"; then
    {
      echo
      echo "## Current Workspace"
      echo
      printf -- '- Created: %s\n' "$(now)"
      printf -- '- Workspace: `%s`\n' "$path"
      printf -- '- Key: `%s`\n' "$(workspace_key)"
    } >> "$dir/workspace.md"
  fi
  printf '%s' "$dir"
}

append_section() {
  local file="$1" actor="$2" text="$3"
  {
    printf '\n## %s %s\n\n' "$(now)" "$actor"
    printf '%s\n' "$text"
  } >> "$file"
}

cmd="${1:-}"
shift || true

case "$cmd" in
  init)
    dir="$(ensure_workspace)"
    label="${1:-}"
    if [ -n "$label" ]; then
      {
        echo
        printf -- '- Label: %s\n' "$label"
      } >> "$dir/workspace.md"
    fi
    echo "shared memory: $dir"
    ;;
  note)
    [ "$#" -lt 2 ] && { echo "error: note requires <agent> <message...>" >&2; usage >&2; exit 1; }
    agent="$1"
    shift
    dir="$(ensure_workspace)"
    append_section "$dir/journal.md" "$agent" "$*"
    echo "noted in $dir/journal.md"
    ;;
  decision)
    [ "$#" -lt 2 ] && { echo "error: decision requires <agent> <message...>" >&2; usage >&2; exit 1; }
    agent="$1"
    shift
    dir="$(ensure_workspace)"
    append_section "$dir/decisions.md" "$agent" "$*"
    echo "recorded in $dir/decisions.md"
    ;;
  task)
    [ "$#" -lt 3 ] && { echo "error: task requires <agent> <status> <message...>" >&2; usage >&2; exit 1; }
    agent="$1"
    status="$2"
    shift 2
    case "$status" in
      open|doing|blocked|done) ;;
      *) echo "error: status must be open, doing, blocked, or done" >&2; exit 1 ;;
    esac
    dir="$(ensure_workspace)"
    text="$(printf '%s' "$*" | tr '\r\n' '  ')"
    printf '\n- %s `%s` `%s` %s\n' "$(now)" "$status" "$agent" "$text" >> "$dir/tasks.md"
    echo "recorded in $dir/tasks.md"
    ;;
  handoff)
    [ "$#" -lt 3 ] && { echo "error: handoff requires <from> <to> <message...>" >&2; usage >&2; exit 1; }
    from="$1"
    to="$2"
    shift 2
    dir="$(ensure_workspace)"
    {
      printf '\n## %s %s -> %s\n\n' "$(now)" "$from" "$to"
      printf '%s\n' "$*"
    } >> "$dir/handoffs.md"
    echo "recorded in $dir/handoffs.md"
    ;;
  show)
    target="${1:-all}"
    dir="$(ensure_workspace)"
    case "$target" in
      path)
        echo "$dir"
        ;;
      index)
        cat "$dir/MEMORY.md"
        ;;
      workspace)
        cat "$dir/workspace.md"
        ;;
      journal)
        cat "$dir/journal.md"
        ;;
      decisions)
        cat "$dir/decisions.md"
        ;;
      tasks)
        cat "$dir/tasks.md"
        ;;
      handoffs)
        cat "$dir/handoffs.md"
        ;;
      all)
        echo "# Shared Memory"
        echo
        echo "## Index"
        cat "$dir/MEMORY.md"
        echo
        echo "## Workspace"
        cat "$dir/workspace.md"
        echo
        echo "## Tasks"
        cat "$dir/tasks.md"
        echo
        echo "## Decisions"
        cat "$dir/decisions.md"
        echo
        echo "## Journal"
        cat "$dir/journal.md"
        echo
        echo "## Handoffs"
        cat "$dir/handoffs.md"
        ;;
      *)
        echo "error: unknown show target '$target'" >&2
        usage >&2
        exit 1
        ;;
    esac
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
