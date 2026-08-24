#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
render_prog="render-gif.sh"
source "$script_dir/_render_common.sh"

usage() {
  cat <<'EOF'
Usage: ./render-gif.sh <path.html> [options]

Renders a looping GIF to design/output.

Options:
  --beats LIST       Beat list or range. Examples: 1-4, 1,3,5. Default: 1..TOTAL.
  --query QUERY      Fixed query params, for example theme=dark&tab=activity.
  --theme THEME      Shortcut for --query theme=THEME.
  --fps N            GIF frame rate. Default: 12.
  --step-seconds N   Seconds to hold each beat. Default: 1.7.
  --width PX         Output GIF width. Default: 1000.
  --size WxH         Browser window size. Default: 1240x880.
  --scale N          Chrome device scale factor. Default: 1.
  --output NAME      Output filename under design/output.
  -h, --help         Show this help.
EOF
}

beat_total() {
  sed -n '/const[[:space:]]*TOTAL[[:space:]]*=/{s/.*const[[:space:]]*TOTAL[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p;q;}' "$1"
}

expand_beats() {
  local spec="$1"
  local part start end i
  IFS=',' read -r -a parts <<< "$spec"
  for part in "${parts[@]}"; do
    if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
      start="${BASH_REMATCH[1]}"
      end="${BASH_REMATCH[2]}"
      [[ "$start" -le "$end" ]] || die "beat range must ascend: $part"
      for ((i=start; i<=end; i++)); do
        printf '%s\n' "$i"
      done
    elif [[ "$part" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$part"
    else
      die "invalid beat list: $spec"
    fi
  done
}

html=""
beats=""
query=""
fps="12"
step_seconds="1.7"
width="1000"
size="1240x880"
scale="1"
output_name=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --beats)
      [[ $# -ge 2 ]] || die "--beats requires a value"
      beats="$2"
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
    --fps)
      [[ $# -ge 2 ]] || die "--fps requires a value"
      fps="$2"
      shift 2
      ;;
    --step-seconds)
      [[ $# -ge 2 ]] || die "--step-seconds requires a value"
      step_seconds="$2"
      shift 2
      ;;
    --width)
      [[ $# -ge 2 ]] || die "--width requires a value"
      width="$2"
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
[[ "$fps" =~ ^[0-9]+$ && "$fps" -gt 0 ]] || die "--fps must be a positive integer"
[[ "$step_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--step-seconds must be a positive number"
[[ "$width" =~ ^[0-9]+$ && "$width" -gt 0 ]] || die "--width must be a positive integer"
command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg is required"

resolve_html_path "$html"

if [[ -z "$beats" ]]; then
  total="$(beat_total "$html_path")"
  [[ -n "$total" ]] || die "no beat total found; pass --beats or add const TOTAL"
  beats="1-$total"
fi

beat_values=()
while IFS= read -r beat_value; do
  beat_values+=("$beat_value")
done < <(expand_beats "$beats")
[[ "${#beat_values[@]}" -gt 0 ]] || die "no beats selected"

mkdir -p "$output_dir"

if [[ -n "$output_name" ]]; then
  out_base="$(basename "$output_name")"
  [[ "$out_base" == *.gif ]] || out_base="$out_base.gif"
  gif="$output_dir/$out_base"
else
  stem="${rel_path%.html}"
  name="$(slugify "$stem")"
  [[ -n "$beats" ]] && name="$name-beats-$(slugify "$beats")"
  [[ -n "${query#\?}" ]] && name="$name-$(slugify "${query#\?}")"
  gif="$output_dir/$name.gif"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/design-render-gif.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

frame=0
for beat in "${beat_values[@]}"; do
  n="$(printf '%03d' "$frame")"
  url_query="beat=$beat"
  if [[ -n "${query#\?}" ]]; then
    url_query="$url_query&${query#\?}"
  fi
  chrome_screenshot "$tmp_dir/f$n.png" "file://$html_path?$url_query" >/dev/null 2>&1
  [[ -s "$tmp_dir/f$n.png" ]] || die "beat $beat failed to render"
  frame=$((frame + 1))
done

input_rate="$(awk -v s="$step_seconds" 'BEGIN { printf "%.8f", 1 / s }')"

ffmpeg -y -framerate "$input_rate" -i "$tmp_dir/f%03d.png" \
  -vf "fps=$fps,scale=$width:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3" \
  -loop 0 "$gif" >/dev/null 2>&1

echo "wrote $gif"
