#!/usr/bin/env bash
set -euo pipefail

# install-skills.sh — symlink this repo's Agent Skills into the per-user
# skill directories so they work globally, in every repo.
#
#   Claude Code reads ~/.claude/skills
#   Codex reads       ~/.agents/skills
#
# By default it links every skill into both. The links point at the repo, so
# edits here take effect immediately and `git pull` updates every install.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
skills_src="$repo_root/.agents/skills"

claude_dir="$HOME/.claude/skills"
agents_dir="$HOME/.agents/skills"

usage() {
  cat <<'USAGE'
usage: install-skills.sh [options] [skill ...]

Symlink Agent Skills from this repo into the per-user skill directories.
With no skill names, all skills are installed.

options:
  --claude       only install for Claude (~/.claude/skills)
  --codex        only install for Codex (~/.agents/skills)
  --all          install for both (default)
  --uninstall    remove symlinks this script created (never touches real dirs)
  --force        relink a symlink that points elsewhere (real dirs are always
                 left untouched)
  -h, --help     show this help

Idempotent: re-running with a link already in place is a no-op.
USAGE
}

targets=()
want_claude=0
want_codex=0
uninstall=0
force=0
names=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --claude) want_claude=1 ;;
    --codex) want_codex=1 ;;
    --all) want_claude=1; want_codex=1 ;;
    --uninstall) uninstall=1 ;;
    --force) force=1 ;;
    -h|--help) usage; exit 0 ;;
    --*) echo "error: unknown option '$1'" >&2; usage >&2; exit 1 ;;
    *) names+=("$1") ;;
  esac
  shift
done

if [ "$want_claude" -eq 0 ] && [ "$want_codex" -eq 0 ]; then
  want_claude=1
  want_codex=1
fi
[ "$want_claude" -eq 1 ] && targets+=("$claude_dir")
[ "$want_codex" -eq 1 ] && targets+=("$agents_dir")

[ -d "$skills_src" ] || { echo "error: skills directory not found: $skills_src" >&2; exit 1; }

# Selected skill names default to every directory holding a SKILL.md.
if [ "${#names[@]}" -eq 0 ]; then
  for d in "$skills_src"/*/; do
    [ -f "$d/SKILL.md" ] || continue
    names+=("$(basename "$d")")
  done
fi

linked=0
removed=0
skipped=0

for name in "${names[@]}"; do
  src="$skills_src/$name"
  if [ ! -f "$src/SKILL.md" ]; then
    echo "skip   $name (no such skill in $skills_src)" >&2
    skipped=$((skipped + 1))
    continue
  fi
  for target in "${targets[@]}"; do
    dst="$target/$name"
    case "$dst" in
      "$HOME"/*) label="~/${dst#"$HOME"/}" ;;
      *) label="$dst" ;;
    esac

    if [ "$uninstall" -eq 1 ]; then
      if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        rm "$dst"
        echo "remove $label"
        removed=$((removed + 1))
      elif [ -e "$dst" ] || [ -L "$dst" ]; then
        echo "skip   $label (not a link this script created)"
        skipped=$((skipped + 1))
      fi
      continue
    fi

    mkdir -p "$target"

    if [ -L "$dst" ]; then
      current="$(readlink "$dst")"
      if [ "$current" = "$src" ]; then
        echo "ok     $label (already linked)"
        continue
      fi
      if [ "$force" -eq 1 ]; then
        rm "$dst"
        ln -s "$src" "$dst"
        echo "relink $label (was -> $current)"
        linked=$((linked + 1))
      else
        echo "skip   $label (symlink -> $current; use --force to relink)" >&2
        skipped=$((skipped + 1))
      fi
      continue
    fi

    if [ -e "$dst" ]; then
      echo "skip   $label (a real file or directory exists here; remove it yourself to replace)" >&2
      skipped=$((skipped + 1))
      continue
    fi

    ln -s "$src" "$dst"
    echo "link   $label -> $src"
    linked=$((linked + 1))
  done
done

if [ "$uninstall" -eq 1 ]; then
  echo "done: removed $removed, skipped $skipped"
else
  echo "done: linked $linked, skipped $skipped"
fi
