#!/usr/bin/env bash
set -euo pipefail

root="$HOME/.agents/mailbox"
who_dir="$root/.who"
sid="${MAILBOX_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"

if [ -n "${AGENT_MAILBOX_ROOT:-}" ] && [ "$AGENT_MAILBOX_ROOT" != "$root" ]; then
  echo "warning: ignoring AGENT_MAILBOX_ROOT; fixed mailbox root is $root" >&2
fi

usage() {
  echo "usage: mail.sh {iam <name>|send <to> <message...>|read [name]|wait [name] [seconds]|clean [name|all]}"
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

validate_addr() {
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

mail_ready() {
  local me="$1" box="$root/$me/inbox" f
  [ -d "$box" ] || return 1
  for f in "$box"/*.txt; do
    [ -e "$f" ] && return 0
  done
  return 1
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
    validate_addr "$name" "name"
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
    validate_addr "$to" "recipient"
    validate_addr "$from" "sender"
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
    validate_addr "$me" "name"
    ensure_root
    drain "$me"
    ;;
  wait)
    name="${1:-}"
    secs="${2:-}"
    case "$name" in
      ''|*[!0-9]*) ;;
      *)
        if [ -z "$secs" ]; then
          secs="$name"
          name=""
        fi
        ;;
    esac
    if [ -n "$secs" ]; then
      case "$secs" in
        *[!0-9]*) echo "error: seconds must be a non-negative integer" >&2; exit 1 ;;
      esac
      secs=$((10#$secs))
    fi
    me="$(resolve_addr "$name")"
    [ -z "$me" ] && { echo "error: no identity; run mail.sh iam <name> or set MAILBOX_FROM"; exit 1; }
    validate_addr "$me" "name"
    ensure_root
    mkdir_or_die "$root/$me/inbox"
    mkdir_or_die "$root/$me/read"
    end=""
    [ -n "$secs" ] && end=$((SECONDS + secs))
    while true; do
      if mail_ready "$me"; then
        drain "$me"
        exit 0
      fi
      if [ -n "$end" ] && [ "$SECONDS" -ge "$end" ]; then
        echo "timed out waiting for mail for '$me'"
        exit 124
      fi
      sleep 2
    done
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
      validate_addr "$me" "name"
      rm -rf "${root:?}/$me"
      echo "cleaned mailbox for '$me'"
    fi
    ;;
  *)
    usage
    exit 1
    ;;
esac
