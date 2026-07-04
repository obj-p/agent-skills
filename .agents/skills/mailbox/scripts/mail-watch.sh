#!/usr/bin/env bash
set -euo pipefail

root="$HOME/.agents/mailbox"
sid="${MAILBOX_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"

if [ -n "${AGENT_MAILBOX_ROOT:-}" ] && [ "$AGENT_MAILBOX_ROOT" != "$root" ]; then
  echo "warning: ignoring AGENT_MAILBOX_ROOT; fixed mailbox root is $root" >&2
fi

me="${1:-}"
secs="${2:-}"
case "$me" in
  ''|*[!0-9]*) ;;
  *) secs="$me"; me="" ;;
esac
if [ -z "$me" ] && [ -n "${MAILBOX_FROM:-}" ]; then
  me="$MAILBOX_FROM"
fi
if [ -z "$me" ] && [ -n "$sid" ] && [ -f "$root/.who/$sid" ]; then
  me="$(cat "$root/.who/$sid")"
fi
[ -z "$me" ] && { echo "error: no identity; run mail.sh iam <name> or set MAILBOX_FROM"; exit 1; }

box="$root/$me/inbox"
arch="$root/$me/read"
if ! mkdir -p "$box" "$arch" 2>/dev/null; then
  echo "error: cannot create fixed mailbox root '$root'" >&2
  echo "grant this session write access to '$HOME/.agents' and retry" >&2
  exit 1
fi

echo "watching mailbox for '$me' at '$root'"
end=""
[ -n "$secs" ] && end=$((SECONDS + secs))
while [ -z "$end" ] || [ "$SECONDS" -lt "$end" ]; do
  for f in "$box"/*.txt; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    nm="${base%.txt}"
    ts="${nm%%-*}"
    rest="${nm#*-}"
    from="${rest%-*}"
    echo "mail from ${from} at ${ts}: $(cat "$f")"
    mv "$f" "$arch/$base"
  done
  sleep 2
done
