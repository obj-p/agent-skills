#!/usr/bin/env bash
set -euo pipefail

root="${1:-.}"
status=0

while IFS= read -r skill_file; do
  skill_dir="$(basename "$(dirname "$skill_file")")"

  if ! sed -n '1p' "$skill_file" | grep -qx -- '---'; then
    echo "missing frontmatter start: $skill_file"
    status=1
    continue
  fi

  name="$(sed -n '2,40p' "$skill_file" | sed -n 's/^name:[[:space:]]*//p' | head -n 1)"
  description="$(sed -n '2,80p' "$skill_file" | sed -n 's/^description:[[:space:]]*//p' | head -n 1)"

  if [[ -z "$name" ]]; then
    echo "missing name: $skill_file"
    status=1
  elif [[ "$name" != "$skill_dir" ]]; then
    echo "name does not match directory: $skill_file ($name != $skill_dir)"
    status=1
  elif [[ ! "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    echo "invalid name format: $skill_file ($name)"
    status=1
  fi

  if [[ -z "$description" ]]; then
    echo "missing description: $skill_file"
    status=1
  fi
done < <(find "$root" -path '*/SKILL.md' -type f | sort)

exit "$status"
