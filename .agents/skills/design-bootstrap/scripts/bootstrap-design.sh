#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bootstrap-design.sh <target-project-dir> <feature-slug> [feature-title]

Creates a project-local design workspace:
  design/AGENTS.md
  design/dls.html
  design/<feature-slug>.html
  design/_render_common.sh
  design/render-png.sh
  design/render-gif.sh
  design/output/.gitignore

The script refuses to overwrite existing files.
EOF
}

die() {
  echo "bootstrap-design.sh: $*" >&2
  exit 1
}

titleize() {
  local raw="${1//-/ }"
  printf '%s' "$raw" | awk '{
    for (i = 1; i <= NF; i++) {
      $i = toupper(substr($i, 1, 1)) substr($i, 2)
    }
    print
  }'
}

escape_sed() {
  printf '%s' "$1" | sed 's/[\/&]/\\&/g'
}

render_template() {
  local src="$1" dest="$2" feature_slug="$3" feature_title="$4"
  local escaped_slug escaped_title
  escaped_slug="$(escape_sed "$feature_slug")"
  escaped_title="$(escape_sed "$feature_title")"
  sed \
    -e "s/__FEATURE_SLUG__/$escaped_slug/g" \
    -e "s/__FEATURE_TITLE__/$escaped_title/g" \
    "$src" > "$dest"
}

copy_new() {
  local src="$1" dest="$2"
  [[ ! -e "$dest" ]] || die "refusing to overwrite existing file: $dest"
  cp "$src" "$dest"
}

[[ $# -ge 2 ]] || { usage >&2; exit 64; }

target_dir="$1"
feature_slug="$2"
feature_title="${3:-$(titleize "$feature_slug")}"

[[ -d "$target_dir" ]] || die "target project directory does not exist: $target_dir"
[[ "$feature_slug" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "feature slug must be lowercase kebab-case"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_dir="$(cd "$script_dir/.." && pwd)"
template_dir="$skill_dir/assets/templates"
design_dir="$target_dir/design"
feature_file="$design_dir/$feature_slug.html"

for dest in \
  "$design_dir/AGENTS.md" \
  "$design_dir/dls.html" \
  "$feature_file" \
  "$design_dir/_render_common.sh" \
  "$design_dir/render-png.sh" \
  "$design_dir/render-gif.sh"; do
  [[ ! -e "$dest" ]] || die "refusing to overwrite existing file: $dest"
done

mkdir -p "$design_dir/output"

copy_new "$template_dir/design-AGENTS.md" "$design_dir/AGENTS.md"
copy_new "$template_dir/dls.html" "$design_dir/dls.html"
render_template "$template_dir/feature.html" "$feature_file" "$feature_slug" "$feature_title"
copy_new "$template_dir/_render_common.sh" "$design_dir/_render_common.sh"
copy_new "$template_dir/render-png.sh" "$design_dir/render-png.sh"
copy_new "$template_dir/render-gif.sh" "$design_dir/render-gif.sh"

if [[ ! -e "$design_dir/output/.gitignore" ]]; then
  printf '*\n!.gitignore\n' > "$design_dir/output/.gitignore"
fi

chmod +x "$design_dir/render-png.sh" "$design_dir/render-gif.sh"

cat <<EOF
Created design workspace:
  $design_dir/AGENTS.md
  $design_dir/dls.html
  $feature_file
  $design_dir/render-png.sh
  $design_dir/render-gif.sh

Next:
  1. Open design/dls.html and settle the project theme/components.
  2. Edit design/$feature_slug.html into the first feature mockup.
  3. Render beats with design/render-png.sh and design/render-gif.sh.
EOF
