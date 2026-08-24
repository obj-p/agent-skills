#!/usr/bin/env bash
set -euo pipefail

# watchdog.sh — mechanical supervisor for a fleet of agent sessions.
#
# Agents self-report a context gauge (used/limit -> percent) plus a heartbeat.
# The watchdog polls those gauges and fires one of three events per agent:
#   WARN  (percent >= WARN_PCT)  — nudge the agent to write a handoff now
#   HARD  (percent >= HARD_PCT)  — agent is nearly out of context; needs successor
#   STALE (heartbeat older than STALE_SECS) — agent likely died; needs successor
#
# The watchdog is deliberately dumb. All judgment lives in the agents it
# supervises. Its only job is to enforce thresholds and invoke an action hook.
# Starting scope is Claude sessions; the action hook can be extended to Codex.

root="$HOME/.agents/context"
log="$root/watchdog.log"

WARN_PCT="${WATCHDOG_WARN_PCT:-70}"
HARD_PCT="${WATCHDOG_HARD_PCT:-85}"
STALE_SECS="${WATCHDOG_STALE_SECS:-120}"
POLL_SECS="${WATCHDOG_POLL_SECS:-5}"

usage() {
  cat <<'USAGE'
usage: watchdog.sh <command> [args]

  gauge <name> <used> <limit> [role]   write/refresh this agent's gauge + heartbeat
  clear <name>                         remove a gauge (call on graceful exit)
  status                               print a table of all known gauges
  watch [seconds]                      poll gauges and fire WARN/HARD/STALE events

env:
  WATCHDOG_WARN_PCT   warn threshold percent (default 70)
  WATCHDOG_HARD_PCT   hard threshold percent (default 85)
  WATCHDOG_STALE_SECS heartbeat staleness seconds (default 120)
  WATCHDOG_POLL_SECS  watch poll interval seconds (default 5)
  WATCHDOG_ON_EVENT   command run per event: "<cmd> <state> <name> <role> <percent>"
  WATCHDOG_NOTIFY     mailbox name to send default WARN/HARD/STALE notices to
  MAILBOX_SKILL_DIR   mailbox skill dir (default: sibling skill, then ~/.claude/skills/mailbox)
USAGE
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
skill_dir="$(cd -- "$script_dir/.." && pwd -P)"
if [ -n "${MAILBOX_SKILL_DIR:-}" ]; then
  mailbox_skill="$MAILBOX_SKILL_DIR"
elif [ -f "$skill_dir/../mailbox/scripts/mail.sh" ]; then
  mailbox_skill="$skill_dir/../mailbox"
else
  mailbox_skill="$HOME/.claude/skills/mailbox"
fi
mail_send="$mailbox_skill/scripts/mail.sh"

ensure_root() {
  if ! mkdir -p "$root" 2>/dev/null; then
    echo "error: cannot create context root '$root'" >&2
    exit 1
  fi
}

validate_name() {
  local name="$1"
  case "$name" in
    ""|.|..|*/*|*[!A-Za-z0-9._-]*)
      echo "error: invalid name '$name'; use letters, digits, dot, underscore, hyphen" >&2
      exit 1 ;;
  esac
}

# read a numeric field from a gauge file: field <file> <key>
field() {
  sed -n "s/.*\"$2\":\([0-9]*\).*/\1/p" "$1" | head -n 1
}

# read the role string from a gauge file
role_of() {
  sed -n 's/.*"role":"\([^"]*\)".*/\1/p' "$1" | head -n 1
}

cmd_gauge() {
  local name="${1:-}" used="${2:-}" limit="${3:-}" role="${4:-worker}"
  validate_name "$name"
  case "$used$limit" in
    *[!0-9]*|"") echo "error: <used> and <limit> must be integers" >&2; exit 1 ;;
  esac
  [ "$limit" -gt 0 ] || { echo "error: <limit> must be > 0" >&2; exit 1; }
  ensure_root
  local pct=$(( used * 100 / limit ))
  local ts; ts="$(date +%s)"
  local iso; iso="$(date -u +%Y%m%dT%H%M%SZ)"
  printf '{"name":"%s","role":"%s","used":%s,"limit":%s,"percent":%s,"ts":%s,"updatedAt":"%s","pid":%s}\n' \
    "$name" "$role" "$used" "$limit" "$pct" "$ts" "$iso" "${PPID:-0}" > "$root/$name.json"
  echo "gauge $name: $used/$limit = ${pct}% (role $role)"
}

cmd_clear() {
  local name="${1:-}"
  validate_name "$name"
  rm -f "$root/$name.json" "$root/$name.state"
  echo "cleared gauge $name"
}

# classify a gauge file -> STALE|HARD|WARN|OK, echoing "state percent age role"
classify() {
  local f="$1" now="$2"
  local pct ts age role
  pct="$(field "$f" percent)"; ts="$(field "$f" ts)"; role="$(role_of "$f")"
  [ -n "$pct" ] || pct=0
  [ -n "$ts" ] || ts="$now"
  [ -n "$role" ] || role="worker"
  age=$(( now - ts ))
  local state=OK
  if [ "$age" -gt "$STALE_SECS" ]; then state=STALE
  elif [ "$pct" -ge "$HARD_PCT" ]; then state=HARD
  elif [ "$pct" -ge "$WARN_PCT" ]; then state=WARN
  fi
  echo "$state $pct $age $role"
}

cmd_status() {
  ensure_root
  local now; now="$(date +%s)"
  printf '%-16s %-12s %-13s %-5s %-7s %s\n' NAME ROLE USED/LIMIT PCT AGE STATE
  local f
  for f in "$root"/*.json; do
    [ -e "$f" ] || { echo "(no gauges)"; return; }
    local name used limit info state pct age role
    name="$(basename "$f" .json)"
    used="$(field "$f" used)"; limit="$(field "$f" limit)"
    info="$(classify "$f" "$now")"
    state="${info%% *}"; info="${info#* }"; pct="${info%% *}"
    info="${info#* }"; age="${info%% *}"; role="${info#* }"
    printf '%-16s %-12s %-13s %-5s %-7s %s\n' \
      "$name" "$role" "${used}/${limit}" "${pct}%" "${age}s" "$state"
  done
}

notify() {
  local state="$1" name="$2" role="$3" pct="$4"
  printf '%s %s name=%s role=%s pct=%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" "$state" "$name" "$role" "$pct" >> "$log"
  if [ -n "${WATCHDOG_ON_EVENT:-}" ]; then
    # shellcheck disable=SC2086
    ${WATCHDOG_ON_EVENT} "$state" "$name" "$role" "$pct" || \
      echo "warning: WATCHDOG_ON_EVENT failed for $name" >&2
    return
  fi
  if [ -n "${WATCHDOG_NOTIFY:-}" ] && [ -f "$mail_send" ]; then
    MAILBOX_FROM=watchdog bash "$mail_send" send <<EOF
$WATCHDOG_NOTIFY THREAD: watchdog-$(date -u +%Y%m%d)
VERB: FYI
FROM: watchdog
TO: $WATCHDOG_NOTIFY

context $state: $name (role $role) at ${pct}%. $( [ "$state" = WARN ] && echo "write a handoff now, keep working." || echo "needs a successor; owner likely gone or exhausted." )
EOF
  fi
}

# fire an event only when the state escalates for a given agent
maybe_fire() {
  local name="$1" state="$2" pct="$3" role="$4"
  local sf="$root/$name.state" last=""
  [ -f "$sf" ] && last="$(cat "$sf")"
  [ "$state" = "$last" ] && return
  echo "$state" > "$sf"
  [ "$state" = OK ] && return
  notify "$state" "$name" "$role" "$pct"
}

cmd_watch() {
  ensure_root
  local seconds="${1:-}" deadline=0 now
  if [ -n "$seconds" ]; then
    case "$seconds" in *[!0-9]*) echo "error: seconds must be an integer" >&2; exit 1 ;; esac
    deadline=$(( $(date +%s) + seconds ))
  fi
  echo "watchdog watching $root (warn ${WARN_PCT}% hard ${HARD_PCT}% stale ${STALE_SECS}s)"
  while :; do
    now="$(date +%s)"
    local f
    for f in "$root"/*.json; do
      [ -e "$f" ] || break
      local name info state pct role
      name="$(basename "$f" .json)"
      info="$(classify "$f" "$now")"
      state="${info%% *}"; info="${info#* }"; pct="${info%% *}"
      info="${info#* }"; role="${info#* }"
      maybe_fire "$name" "$state" "$pct" "$role"
    done
    [ "$deadline" -ne 0 ] && [ "$(date +%s)" -ge "$deadline" ] && break
    sleep "$POLL_SECS"
  done
}

cmd="${1:-}"; shift || true
case "$cmd" in
  gauge)  cmd_gauge "$@" ;;
  clear)  cmd_clear "$@" ;;
  status) cmd_status "$@" ;;
  watch)  cmd_watch "$@" ;;
  ""|-h|--help|help) usage ;;
  *) echo "error: unknown command '$cmd'" >&2; usage >&2; exit 1 ;;
esac
