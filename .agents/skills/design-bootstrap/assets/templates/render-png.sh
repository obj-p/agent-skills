#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
render_prog="render-png.sh"
source "$script_dir/_render_common.sh"

usage() {
  cat <<'EOF'
Usage: ./render-png.sh <path.html> [options]

Renders a mockup PNG to design/output.

Options:
  --beat N          Add ?beat=N.
  --query QUERY     Add fixed query params, for example theme=dark&tab=activity.
  --theme THEME     Shortcut for --query theme=THEME.
  --size WxH        Browser window size. Default: 1240x880.
  --scale N         Device scale factor. Default: 2.
  --output NAME     Output filename under design/output.
  -h, --help        Show this help.
EOF
}

html=""
beat=""
query=""
size="1240x880"
scale="2"
output_name=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --beat)
      [[ $# -ge 2 ]] || die "--beat requires a value"
      beat="$2"
      shift 2
      ;;
    --query)
      [[ $# -ge 2 ]] || die "--query requires a value"
      addition="${2#\?}"
      query="${query:+$query&}$addition"
      shift 2
      ;;
    --theme)
      [[ $# -ge 2 ]] || die "--theme requires a value"
      query="${query:+$query&}theme=$2"
      shift 2
      ;;
    --size)
      [[ $# -ge 2 ]] || die "--size requires a value"
      size="$2"
      shift 2
      ;;
    --scale)
      [[ $# -ge 2 ]] || die "--scale requires a value"
      scale="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || die "--output requires a value"
      output_name="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      [[ -z "$html" ]] || die "unexpected extra argument: $1"
      html="$1"
      shift
      ;;
  esac
done

[[ -n "$html" ]] || { usage >&2; exit 64; }
[[ "$size" == *x* ]] || die "--size must be WIDTHxHEIGHT"

resolve_html_path "$html"

url_query="${query#\?}"
if [[ -n "$beat" ]]; then
  url_query="${url_query:+$url_query&}beat=$beat"
fi

url="file://$html_path"
if [[ -n "$url_query" ]]; then
  url="$url?$url_query"
fi

mkdir -p "$output_dir"

if [[ -n "$output_name" ]]; then
  out_base="$(basename "$output_name")"
  [[ "$out_base" == *.png ]] || out_base="$out_base.png"
  png="$output_dir/$out_base"
else
  stem="${rel_path%.html}"
  name="$(slugify "$stem")"
  [[ -n "$beat" ]] && name="$name-beat-$beat"
  [[ -n "${query#\?}" ]] && name="$name-$(slugify "${query#\?}")"
  png="$output_dir/$name.png"
fi

chrome_screenshot "$png" "$url"

echo "wrote $png"
