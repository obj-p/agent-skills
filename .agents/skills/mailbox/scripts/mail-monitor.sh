#!/usr/bin/env bash
set -euo pipefail

root="$HOME/.agents/mailbox"
sid="${MAILBOX_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"

if [ -n "${AGENT_MAILBOX_ROOT:-}" ] && [ "$AGENT_MAILBOX_ROOT" != "$root" ]; then
  echo "warning: ignoring AGENT_MAILBOX_ROOT; fixed mailbox root is $root" >&2
fi

usage() {
  echo "usage: mail-monitor.sh [name] [seconds]"
}

validate_addr() {
  # Keep this in sync with mail.sh and mail-watch.sh.
  local name="$1" label="${2:-name}"
  if [ -z "$name" ]; then
    echo "error: $label required" >&2
    exit 1
  fi
  case "$name" in
    .|..|*/*)
      echo "error: invalid $label '$name'; use only letters, digits, dot, underscore, and hyphen" >&2
      exit 1
      ;;
  esac
  if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "error: invalid $label '$name'; use only letters, digits, dot, underscore, and hyphen" >&2
    exit 1
  fi
}

resolve_addr() {
  # Keep this in sync with mail.sh and mail-watch.sh.
  if [ -n "${1:-}" ]; then
    echo "$1"
    return
  fi
  if [ -n "${MAILBOX_FROM:-}" ]; then
    echo "$MAILBOX_FROM"
    return
  fi
  if [ -n "$sid" ] && [ -f "$root/.who/$sid" ]; then
    cat "$root/.who/$sid"
  fi
}

notify() {
  local me="$1" from="$2"
  [ "${MAILBOX_NOTIFY:-}" = "1" ] || return 0
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"New mailbox message from $from\" with title \"Mailbox: $me\"" >/dev/null 2>&1 || true
}

process_mail() {
  local me="$1" box="$2" arch="$3" log="$4"
  local f base nm ts rest from seen
  for f in "$box"/*.txt; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    nm="${base%.txt}"
    ts="${nm%%-*}"
    rest="${nm#*-}"
    from="${rest%-*}"
    seen="$(date -u +%Y%m%dT%H%M%SZ)"

    {
      echo "--- from ${from} at ${ts} seen ${seen} file ${base} ---"
      cat "$f"
      echo
    } >> "$log"

    echo "--- from ${from} at ${ts} ---"
    cat "$f"
    echo

    mv "$f" "$arch/$base"
    notify "$me" "$from"
  done
}

me="${1:-}"
secs="${2:-}"
case "$me" in
  -h|--help)
    usage
    exit 0
    ;;
  ''|*[!0-9]*) ;;
  *) secs="$me"; me="" ;;
esac

if [ -n "$secs" ]; then
  case "$secs" in
    *[!0-9]*) echo "error: seconds must be a non-negative integer" >&2; exit 1 ;;
  esac
  secs=$((10#$secs))
fi

me="$(resolve_addr "$me")"
[ -z "$me" ] && { echo "error: no identity; run mail.sh iam <name> or set MAILBOX_FROM"; exit 1; }
validate_addr "$me" "name"

box="$root/$me/inbox"
arch="$root/$me/read"
log="$root/$me/events.log"
umask 077
if ! mkdir -p "$box" "$arch" 2>/dev/null; then
  echo "error: cannot create fixed mailbox root '$root'" >&2
  echo "grant this session write access to '$HOME/.agents' and retry" >&2
  exit 1
fi
touch "$log"

echo "monitoring mailbox for '$me' at '$root'"
echo "event log: $log"

end=""
[ -n "$secs" ] && end=$((SECONDS + secs))
first=1
while [ "$first" -eq 1 ] || [ -z "$end" ] || [ "$SECONDS" -lt "$end" ]; do
  first=0
  process_mail "$me" "$box" "$arch" "$log"
  if [ -n "$end" ] && [ "$SECONDS" -ge "$end" ]; then
    break
  fi
  sleep 2
done
