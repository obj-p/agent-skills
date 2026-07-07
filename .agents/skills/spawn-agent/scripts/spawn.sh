#!/usr/bin/env bash
set -euo pipefail

# spawn.sh <claude|codex> <reply-to> <job...>
#
# Runs a one-shot ("ephemeral") child agent, captures its final output, and
# mails the result to the <reply-to> mailbox as a THREAD/VERB envelope
# (VERB: DONE on success, VERB: BLOCKED on failure). The child never touches
# the mailbox or the protocol; this wrapper owns delivery. The script blocks
# until the child finishes, so the caller decides whether to background it.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
skill_dir="$(cd -- "$script_dir/.." && pwd -P)"

if [ -n "${MAILBOX_SKILL_DIR:-}" ]; then
  mailbox_skill="$MAILBOX_SKILL_DIR"
elif [ -f "$skill_dir/../mailbox/scripts/mail.sh" ]; then
  mailbox_skill="$skill_dir/../mailbox"
else
  mailbox_skill="$HOME/.claude/skills/mailbox"
fi

usage() {
  cat <<'USAGE'
usage: spawn.sh <claude|codex> <reply-to> <job...>

  Spawns a one-shot worker of <tool>, runs <job>, and mails the result to the
  <reply-to> mailbox (VERB: DONE, or VERB: BLOCKED on nonzero exit).

env overrides:
  SPAWN_CLAUDE_FLAGS  claude flags (default: -p --output-format text)
  SPAWN_CODEX_FLAGS   codex exec flags (default: -s read-only)
  MAILBOX_SKILL_DIR   mailbox skill dir (default: sibling skill, then ~/.claude/skills/mailbox)

The defaults run unattended but read-only. For a worker that must edit files,
override the flags to grant write access, e.g.
  SPAWN_CLAUDE_FLAGS="-p --output-format text --dangerously-skip-permissions"
  SPAWN_CODEX_FLAGS="-s workspace-write"
Those flags let the child act without per-action approval. Only enable them
when you trust the job text, which may itself arrive from another agent.
USAGE
}

if [ "${AGENT_SPAWN_DEPTH:-0}" != "0" ]; then
  echo "error: refusing to spawn from inside a spawned worker (AGENT_SPAWN_DEPTH=${AGENT_SPAWN_DEPTH})" >&2
  exit 1
fi

tool="${1:-}"; shift || true
reply_to="${1:-}"; shift || true
job="${*:-}"

[ -z "$tool" ] && { usage >&2; exit 1; }
case "$tool" in
  claude|codex) ;;
  *) echo "error: tool must be 'claude' or 'codex'" >&2; exit 1 ;;
esac
[ -z "$reply_to" ] && { echo "error: <reply-to> mailbox name required" >&2; usage >&2; exit 1; }
case "$reply_to" in
  .|..|*/*|*[!A-Za-z0-9._-]*)
    echo "error: <reply-to> must be a simple mailbox name (letters, digits, . _ -)" >&2
    exit 1 ;;
esac
[ -z "$job" ] && { echo "error: <job> text required" >&2; usage >&2; exit 1; }

mail_send="$mailbox_skill/scripts/mail.sh"
[ -f "$mail_send" ] || { echo "error: mailbox skill not found at '$mailbox_skill'" >&2; exit 1; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
child="${tool}-worker-$$"
thread="spawn-${reply_to}-${stamp}"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
out="$workdir/out.txt"
err="$workdir/err.txt"

# Strip session identity so the child CLI cannot inherit and register as the
# parent, and mark depth so a worker cannot recursively spawn.
run_child() {
  env -u CLAUDE_CODE_SESSION_ID -u MAILBOX_SESSION_ID -u MAILBOX_FROM \
    AGENT_SPAWN_DEPTH=1 "$@"
}

status=0
if [ "$tool" = "claude" ]; then
  # shellcheck disable=SC2086
  run_child claude ${SPAWN_CLAUDE_FLAGS:--p --output-format text} "$job" \
    >"$out" 2>"$err" || status=$?
  result="$(cat "$out")"
else
  msg="$workdir/msg.txt"
  # shellcheck disable=SC2086
  run_child codex exec ${SPAWN_CODEX_FLAGS:--s read-only} -o "$msg" "$job" \
    >"$out" 2>"$err" || status=$?
  if [ -s "$msg" ]; then result="$(cat "$msg")"; else result="$(cat "$out")"; fi
fi

if [ "$status" -eq 0 ]; then
  verb="DONE"
  body="$result"
else
  verb="BLOCKED"
  body="$(printf '%s\n\n--- exit=%s stderr tail ---\n%s' "$result" "$status" "$(tail -n 20 "$err")")"
fi

if ! MAILBOX_FROM="$child" bash "$mail_send" send <<EOF
$reply_to THREAD: $thread
VERB: $verb
FROM: $child
TO: $reply_to

$body
EOF
then
  echo "warning: failed to deliver result to '$reply_to'" >&2
fi

echo "spawned $tool as '$child' -> mailed $verb to '$reply_to' (thread $thread, exit $status)"
exit "$status"
