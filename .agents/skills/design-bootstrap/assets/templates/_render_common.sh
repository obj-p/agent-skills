#!/usr/bin/env bash
# Shared helpers for render-png.sh and render-gif.sh. Source this file from a
# render script after setting script_dir and render_prog.

set -euo pipefail

output_dir="$script_dir/output"

die() {
  echo "$render_prog: $*" >&2
  exit 1
}

slugify() {
  printf '%s' "$1" | tr '/?&= ' '-----' | tr -cs 'A-Za-z0-9._-' '-'
}

find_chrome() {
  if [[ -n "${CHROME_BIN:-}" ]]; then
    printf '%s\n' "$CHROME_BIN"
    return
  fi

  local mac_chrome_real="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  if [[ -x "$mac_chrome_real" ]]; then
    printf '%s\n' "$mac_chrome_real"
    return
  fi
  if command -v google-chrome >/dev/null 2>&1; then
    command -v google-chrome
    return
  fi
  if command -v chromium >/dev/null 2>&1; then
    command -v chromium
    return
  fi
  if command -v chromium-browser >/dev/null 2>&1; then
    command -v chromium-browser
    return
  fi
}

chrome="$(find_chrome || true)"

resolve_html_path() {
  local html="$1"
  if [[ "$html" == /* ]]; then
    html_path="$html"
    rel_path="${html#$script_dir/}"
  else
    rel_path="${html#./}"
    if [[ "$rel_path" == design/* && -f "$script_dir/${rel_path#design/}" ]]; then
      rel_path="${rel_path#design/}"
    fi
    html_path="$script_dir/$rel_path"
  fi
  [[ -f "$html_path" ]] || die "HTML file not found: $html"
}

chrome_screenshot() {
  local out="$1" url="$2"

  [[ -n "$chrome" && -x "$chrome" ]] || die "Chrome not found; set CHROME_BIN"

  if [[ "${CODEX_SANDBOX:-}" == "seatbelt" && "$chrome" == /Applications/*.app/* ]]; then
    die "Chrome aborts under Codex's macOS seatbelt sandbox; run from a normal terminal or approve unsandboxed execution"
  fi

  "$chrome" \
    --headless=new \
    --disable-gpu \
    --hide-scrollbars \
    --force-device-scale-factor="$scale" \
    --window-size="${size/x/,}" \
    --virtual-time-budget=1000 \
    --screenshot="$out" "$url"
}
