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
  SPAWN_ARTIFACT_ROOT capture directory (default: ~/.agents/spawn)

Exit status is the delivery exit code when sending fails; otherwise it is the
worker exit code. Failed delivery retains the answer, envelope, and diagnostics
under SPAWN_ARTIFACT_ROOT and prints a command to retry delivery once without
rerunning the worker. Confirmed delivery removes the captures.

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
[ -f "$mail_send" ] && [ -r "$mail_send" ] || {
  echo "error: mailbox transport is not a readable file: '$mail_send'" >&2
  exit 1
}
if ! bash -n "$mail_send"; then
  echo "error: mailbox transport failed Bash syntax validation: '$mail_send'" >&2
  exit 1
fi
if ! command -v "$tool" >/dev/null 2>&1; then
  echo "error: worker CLI '$tool' not found in PATH" >&2
  exit 127
fi
# Recovery commands must also work from a different directory.
mail_send="$(cd -- "$mailbox_skill/scripts" && pwd -P)/mail.sh"

artifact_root="${SPAWN_ARTIFACT_ROOT:-$HOME/.agents/spawn}"
mkdir -p -- "$artifact_root"
artifact_root="$(cd -- "$artifact_root" && pwd -P)"
workdir="$(mktemp -d "$artifact_root/run.XXXXXXXX")"
delivered=0
cleanup() {
  if [ "$delivered" -eq 1 ]; then
    rm -rf -- "$workdir"
  else
    printf 'Spawn artifacts retained at: %s\n' "$workdir" >&2
    if [ -f "$workdir/retry.sh" ]; then
      printf 'Retry delivery once without rerunning the worker:\n  bash %q\n' "$workdir/retry.sh" >&2
    fi
  fi
}
trap cleanup EXIT

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
child="${tool}-worker-$$"
thread="spawn-${reply_to}-${stamp}"

out="$workdir/out.txt"
err="$workdir/err.txt"

# Strip session identity so the child CLI cannot inherit and register as the
# parent, and mark depth so a worker cannot recursively spawn.
run_child() {
  env -u CLAUDE_CODE_SESSION_ID -u MAILBOX_SESSION_ID -u MAILBOX_FROM \
    AGENT_SPAWN_DEPTH=1 "$@"
}

worker_status=0
if [ "$tool" = "claude" ]; then
  # shellcheck disable=SC2086
  run_child claude ${SPAWN_CLAUDE_FLAGS:--p --output-format text} "$job" \
    >"$out" 2>"$err" || worker_status=$?
  result="$(cat "$out")"
else
  msg="$workdir/msg.txt"
  # shellcheck disable=SC2086
  run_child codex exec ${SPAWN_CODEX_FLAGS:--s read-only} -o "$msg" "$job" \
    >"$out" 2>"$err" || worker_status=$?
  if [ -s "$msg" ]; then result="$(cat "$msg")"; else result="$(cat "$out")"; fi
fi

printf 'worker_exit=%s\ndelivery_exit=pending\n' "$worker_status" > "$workdir/status.txt"

if [ "$worker_status" -eq 0 ]; then
  verb="DONE"
  body="$result"
else
  verb="BLOCKED"
  body="$(printf '%s\n\n--- exit=%s stderr tail ---\n%s' "$result" "$worker_status" "$(tail -n 20 "$err")")"
fi

cat > "$workdir/envelope.txt" <<EOF
$reply_to THREAD: $thread
VERB: $verb
FROM: $child
TO: $reply_to

$body
EOF

# Shell-quote only wrapper metadata and paths, never the worker's answer. This
# performs one transport attempt and leaves the saved evidence for inspection.
{
  printf '#!/usr/bin/env bash\n'
  printf 'MAILBOX_FROM=%q bash %q send < %q\n' "$child" "$mail_send" "$workdir/envelope.txt"
} > "$workdir/retry.sh"

delivery_status=0
MAILBOX_FROM="$child" bash "$mail_send" send < "$workdir/envelope.txt" \
  > "$workdir/delivery-out.txt" 2> "$workdir/delivery-err.txt" || delivery_status=$?
if [ "$delivery_status" -eq 0 ]; then
  delivered=1
fi
printf 'worker_exit=%s\ndelivery_exit=%s\n' "$worker_status" "$delivery_status" > "$workdir/status.txt"
cat "$workdir/delivery-out.txt"
cat "$workdir/delivery-err.txt" >&2

if [ "$delivery_status" -ne 0 ]; then
  echo "error: failed to deliver $verb to '$reply_to' (thread $thread, worker exit $worker_status, delivery exit $delivery_status)" >&2
  exit "$delivery_status"
fi

echo "spawned $tool as '$child' -> mailed $verb to '$reply_to' (thread $thread, worker exit $worker_status, delivery exit $delivery_status)"
exit "$worker_status"
