#!/usr/bin/env bash
set -euo pipefail

root="${AGENT_MAILBOX_ROOT:-$HOME/.agents/mailbox}"
sid="${MAILBOX_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"

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
mkdir -p "$box" "$arch"

echo "watching mailbox for '$me'"
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
