#!/usr/bin/env bash
set -euo pipefail

# handoff.sh — mechanical helpers for the handoff skill. The judgment (memory
# pass, filling sections, verifying against the repo) belongs to the agent;
# this script only does the deterministic file operations, so both Claude and
# Codex share one layout under a tool-neutral root.

root="${AGENT_HANDOFF_ROOT:-$HOME/.agents/handoffs}"

usage() {
  cat <<'USAGE'
usage:
  handoff.sh repo                 print the repo name used for the handoff dir
  handoff.sh dir                  print this repo's handoff directory
  handoff.sh new <slug> [goal...] create a handoff template, print its path
  handoff.sh latest               print newest active handoff (empty if none)
  handoff.sh archive <file>       move a handoff into the archive subdirectory
  handoff.sh list                 list active handoffs for this repo

env:
  AGENT_HANDOFF_ROOT  handoff root (default: ~/.agents/handoffs)
USAGE
}

repo_name() {
  local common
  if common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
    basename "$(dirname "$common")"
  else
    basename "$(pwd -P)"
  fi
}

repo_dir() {
  printf '%s/%s' "$root" "$(repo_name)"
}

cmd="${1:-}"
shift || true

case "$cmd" in
  repo)
    repo_name
    ;;
  dir)
    repo_dir
    ;;
  new)
    slug="${1:-}"
    shift || true
    case "$slug" in
      ''|*[!a-z0-9-]*) echo "error: slug must be kebab-case (lowercase letters, digits, hyphens)" >&2; exit 1 ;;
    esac
    dir="$(repo_dir)"
    if ! mkdir -p "$dir" 2>/dev/null; then
      echo "error: cannot create handoff directory '$dir'" >&2
      echo "set AGENT_HANDOFF_ROOT to a writable directory or grant write access" >&2
      exit 1
    fi
    day="$(date +%Y-%m-%d)"
    out="$dir/$day-$slug.md"
    if [ -e "$out" ]; then
      echo "error: handoff already exists: $out" >&2
      exit 1
    fi
    goal="${*:-TODO}"
    {
      printf '# Handoff: %s\n\n' "$slug"
      printf -- '- **Goal**: %s\n' "$goal"
      echo "- **Done**:"
      echo "  - [ ] TODO"
      echo "- **Outstanding**:"
      echo "  - [ ] TODO"
      echo "- **Next step**: TODO"
      echo "- **Key files**:"
      echo "  - TODO"
      echo "- **Gotchas**:"
      echo "  - TODO"
    } > "$out"
    echo "$out"
    ;;
  latest)
    dir="$(repo_dir)"
    [ -d "$dir" ] || exit 0
    newest=""
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
        newest="$f"
      fi
    done <<EOF
$(find "$dir" -maxdepth 1 -type f -name '*.md' 2>/dev/null)
EOF
    [ -n "$newest" ] && printf '%s\n' "$newest"
    ;;
  list)
    dir="$(repo_dir)"
    [ -d "$dir" ] || exit 0
    find "$dir" -maxdepth 1 -type f -name '*.md' 2>/dev/null | sort
    ;;
  archive)
    file="${1:-}"
    [ -z "$file" ] && { echo "error: archive requires <file>" >&2; exit 1; }
    [ -f "$file" ] || { echo "error: not a file: $file" >&2; exit 1; }
    dest_dir="$(dirname "$file")/archive"
    mkdir -p "$dest_dir"
    mv "$file" "$dest_dir/"
    echo "$dest_dir/$(basename "$file")"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
