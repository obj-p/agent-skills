#!/usr/bin/env bash
set -euo pipefail

root="$HOME/.agents/mailbox"
who_dir="$root/.who"
sid="${MAILBOX_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"

if [ -n "${AGENT_MAILBOX_ROOT:-}" ] && [ "$AGENT_MAILBOX_ROOT" != "$root" ]; then
  echo "warning: ignoring AGENT_MAILBOX_ROOT; fixed mailbox root is $root" >&2
fi

usage() {
  echo "usage: mail.sh {iam <name>|send <to> <message...>|read [name]|clean [name|all]}"
}

ensure_root() {
  if ! mkdir -p "$root" 2>/dev/null; then
    echo "error: cannot create fixed mailbox root '$root'" >&2
    echo "grant this session write access to '$HOME/.agents' and retry" >&2
    exit 1
  fi
}

mkdir_or_die() {
  local dir="$1"
  if ! mkdir -p "$dir" 2>/dev/null; then
    echo "error: cannot create mailbox directory '$dir'" >&2
    echo "fixed mailbox root is '$root'; grant this session write access to '$HOME/.agents' and retry" >&2
    exit 1
  fi
}

resolve_addr() {
  if [ -n "${1:-}" ]; then
    echo "$1"
    return
  fi
  if [ -n "${MAILBOX_FROM:-}" ]; then
    echo "$MAILBOX_FROM"
    return
  fi
  if [ -n "$sid" ] && [ -f "$who_dir/$sid" ]; then
    cat "$who_dir/$sid"
  fi
}

drain() {
  local me="$1" box="$root/$me/inbox" arch="$root/$me/read" f base nm ts rest from
  if [ ! -d "$box" ] || [ -z "$(ls -A "$box" 2>/dev/null)" ]; then
    echo "no new mail for '$me'"
    return
  fi
  mkdir_or_die "$arch"
  for f in "$box"/*.txt; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    nm="${base%.txt}"
    ts="${nm%%-*}"
    rest="${nm#*-}"
    from="${rest%-*}"
    echo "--- from ${from} at ${ts} ---"
    cat "$f"
    echo
    mv "$f" "$arch/$base"
  done
}

cmd="${1:-}"
shift || true

case "$cmd" in
  iam)
    name="${1:-}"
    [ -z "$name" ] && { echo "error: name required"; exit 1; }
    ensure_root
    if [ -z "$sid" ]; then
      mkdir_or_die "$root/$name/inbox"
      mkdir_or_die "$root/$name/read"
      echo "mailbox initialized for '$name' at '$root/$name'"
      echo "no session id available; set MAILBOX_FROM=$name on each mail.sh call instead"
      exit 0
    fi
    mkdir -p "$who_dir"
    printf '%s' "$name" > "$who_dir/$sid"
    echo "registered as '$name' for this session"
    ;;
  send)
    if [ "$#" -gt 0 ]; then
      payload="$*"
    elif [ ! -t 0 ]; then
      payload="$(cat)"
    else
      payload=""
    fi
    if [ -z "${payload//[[:space:]]/}" ]; then
      {
        echo "error: no message provided"
        echo "pass it as arguments:"
        echo "  mail.sh send <to> <message...>"
        echo "or pipe it on stdin:"
        echo "  mail.sh send <<'MAILBOX_EOF'"
        echo "  <to> <message...>"
        echo "  MAILBOX_EOF"
      } >&2
      exit 1
    fi
    to="${payload%%[[:space:]]*}"
    body="${payload#"$to"}"
    body="${body#"${body%%[![:space:]]*}"}"
    from="$(resolve_addr)"
    [ -z "$to" ] && { echo "error: recipient required"; exit 1; }
    [ -z "$from" ] && { echo "error: no identity; run mail.sh iam <name> or set MAILBOX_FROM"; exit 1; }
    [ -z "$body" ] && { echo "error: message required"; exit 1; }
    ensure_root
    box="$root/$to/inbox"
    mkdir_or_die "$box"
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    f="$box/${ts}-${from}-${RANDOM}.txt"
    printf '%s' "$body" > "$f"
    echo "sent to '$to'"
    ;;
  read)
    me="$(resolve_addr "${1:-}")"
    [ -z "$me" ] && { echo "error: no identity; run mail.sh iam <name> or set MAILBOX_FROM"; exit 1; }
    ensure_root
    drain "$me"
    ;;
  clean)
    ensure_root
    if [ "${1:-}" = "all" ]; then
      count=0
      for d in "$root"/*/; do
        [ -e "$d" ] || continue
        rm -rf "$d"
        count=$((count + 1))
      done
      echo "cleaned $count mailbox(es)"
    else
      me="$(resolve_addr "${1:-}")"
      [ -z "$me" ] && { echo "error: no identity; run mail.sh iam <name> or set MAILBOX_FROM"; exit 1; }
      rm -rf "${root:?}/$me"
      echo "cleaned mailbox for '$me'"
    fi
    ;;
  *)
    usage
    exit 1
    ;;
esac
