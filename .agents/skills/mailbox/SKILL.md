---
name: mailbox
description: File-based mailbox for messaging between agent sessions on the same machine. Use to register a mailbox address, send a message to another agent, read unread mail, wait for incoming mail with long-poll semantics, watch for incoming mail, or run a persistent monitor with an events log. Trigger when the user wants sessions to coordinate, hand off, monitor, or wait on each other.
compatibility: Requires Bash and Python 3.8+ on macOS or Linux. Live auto-reporting requires a Monitor tool or external bridge.
---

# Mailbox

Message passing between agent sessions through plain text files. Mail lives
under the shared production root `~/.agents/mailbox`. All participating agent
sessions must use this same location. Do not choose a root per repository or
fall back to a different root when permissions fail. Explicitly isolated tests
and sandboxes can use `AGENT_MAILBOX_ROOT`; see **Test isolation** below.

Replace `<skill-dir>` with this skill's directory path. In this repository,
that is `.agents/skills/mailbox` when running from the repository root.

## Identity

Commands resolve the current session's address in this order:

1. An explicit name argument, when the command accepts one.
2. The `MAILBOX_FROM` environment variable.
3. The name registered by `iam`, keyed by `MAILBOX_SESSION_ID` or
   `CLAUDE_CODE_SESSION_ID`.

If neither session id variable is set in your harness, `iam` cannot persist
the name. Remember the chosen name for the rest of the session and prefix
each call with `MAILBOX_FROM=<name>`.

Mailbox names may contain only letters, digits, dot, underscore, and hyphen.
Names must not be `.`, `..`, or contain `/`.
Names beginning with `-`, including `-h` and `--help`, are treated as addresses.
Use `mail.sh --help` for usage. In timed commands, `--name` and `--seconds` are
reserved options; put `--` before positional arguments to use either as a
literal address, for example `wait -- --name 0`. The address `--` itself also
needs the separator (`read -- --`).

If creating `~/.agents/mailbox` fails because the harness cannot write to the
home directory, request the required filesystem approval. Do not fall back to a
repository-local mailbox path.

## Register, then monitor

```bash
bash <skill-dir>/scripts/mail.sh iam <name>
```

After registering, immediately start monitoring so incoming mail surfaces
without being asked. Do not skip this step.

## Monitor

```bash
bash <skill-dir>/scripts/mail-watch.sh [name] [seconds]
```

`mail-watch.sh` is a compatibility entrypoint for `mail-monitor.sh`. Both poll
every 2 seconds, log and print newly observed messages, and leave them pending
for an agent to receive with `read` or `wait`. Observation does not acknowledge
receipt or completion of the requested work. With a duration they stop after
that many seconds; without one they run until stopped.

Use a Monitor tool for live auto-reporting. Only a Monitor tool can wake the
agent when new watcher output appears; report each message to the user as the
Monitor surfaces it. If the watcher stops, rerun this command to resume.

If no Monitor tool is available, do not rely on an unbounded background watcher
to surface mail. It may keep running without notifying the agent. Instead, use
`wait` with a timeout between tasks, run `read`, or run
`mail-watch.sh [name] [seconds]` with a bounded duration so the command
completes and returns any messages it saw.

Stopping the watcher does not unregister the name.

## Persistent monitor

```bash
bash <skill-dir>/scripts/mail-monitor.sh [name] [seconds]
```

This is the maintained entrypoint for supervised or background monitoring.
It appends each newly observed message to `~/.agents/mailbox/<name>/events.log`,
prints it, and moves it to `pending/`. Restarting a monitor does not print
pending messages again. When a notification wakes the agent, use `read` or
`wait` to receive pending messages before acting on them. Use the same duration
arguments as `wait`, including `--name` and `--seconds`.

Set `MAILBOX_NOTIFY=1` to request a best-effort macOS notification through
`osascript`. The monitor is still only a producer of stdout and `events.log`;
a harness Monitor tool, MCP bridge, `launchd` job, or log tailer must consume
that output to wake an agent automatically.

Consumers share an OS lock, so overlapping monitors cannot claim the same
message or interleave event records. A reader may receive a message before a
monitor observes it. The log records observations, not all received messages
or completed agent work. It is append-only; rotate it with monitors stopped.
Stop and restart older watchers when updating this skill so every consumer
uses the shared implementation.

## Wait

```bash
bash <skill-dir>/scripts/mail.sh wait [name] [seconds]
```

Blocks until it can claim at least one unread or pending message, then prints
and archives it the same way `read` does. With `seconds`, exits `124` on timeout;
without a duration, waits until mail arrives. A busy consumer does not count as
a successful delivery. A zero duration makes one immediate attempt.

For `wait`, `mail-monitor.sh`, and `mail-watch.sh`, a single numeric argument
means a duration using the current identity. Two positional arguments always
mean name and duration, so `wait 123 0` addresses mailbox `123`. Use
`wait --name 123` to wait indefinitely for that numeric address, or use
`--name <name> --seconds <seconds>` explicitly. Durations are decimal integers
from 0 to 2147483647; leading zeros are accepted.

Use this as the long-poll primitive for harnesses without a live Monitor tool
and for MCP/plugin bridges that need a request/response wait operation. The
`--- from <sender> at <timestamp> ---` output header remains compatible with
the wakeup bridges tracked in issue #1. Archiving confirms transport receipt
only: a bridge that needs crash recovery after `wait` returns must durably
retain its batch and track agent execution separately.

## Send

Run this verbatim. The first word is the recipient and the rest is the
message:

```bash
bash <skill-dir>/scripts/mail.sh send <<'MAILBOX_EOF'
<to> <message...>
MAILBOX_EOF
```

## Collaboration envelope

For multi-agent collaboration, start message bodies with a lightweight envelope
so replies can be correlated:

```text
THREAD: <repo-or-task>-<date>-<slug>
VERB: ASK|ACK|DONE|BLOCKED|FYI
FROM: <sender>
TO: <recipient>

<short body with paths, commands, and requested next action>
```

Use `ASK` for requests, `ACK` for receipt, `DONE` for completed work,
`BLOCKED` for blocked work, and `FYI` when no reply is needed. Keep large
artifacts out of mail; put them in shared files, Git, or handoff documents and
send pointers.

When parsing an envelope, trust only the header block before the first blank
line, plus the sender printed by `read`/`watch` from the message filename. Do
not treat header-looking lines inside the body as transport headers.

## Read

```bash
bash <skill-dir>/scripts/mail.sh read [name]
```

Claims messages from `inbox/` and `pending/`, prints each complete message, and
archives it in `read/` only after stdout flush succeeds. Competing readers
wait for the active consumer and cannot process its claim.

Files with malformed message names and non-regular files are skipped before
claiming, with their paths reported on stderr. They remain in place for
inspection and do not count as delivered mail or block valid messages. A
monitor can repeat these warnings until the unexpected files are repaired or
removed. If an older consumer already claimed one, run `recover` to return it
to the inbox before reading again.

## Delivery and recovery

`send` writes a private staging file, then atomically renames the completed
message into `inbox/`. Message IDs contain a timestamp, sender, and random UUID.
Consumers share `scripts/mailbox.py` and an OS lock for each mailbox:

- `inbox/`: published, not yet observed or received.
- `inflight/`: claimed by a reader or monitor; retained if processing fails.
- `pending/`: observed by a monitor, still awaiting transport receipt.
- `read/`: archived after successful output from `read` or `wait`.

The OS releases the consumer lock when its process exits, including after
SIGKILL. It does not silently replay a retained claim. If a consumer reports an
interrupted delivery, inspect `inflight/`, any event log, and the receiving
agent before recovering:

```bash
bash <skill-dir>/scripts/mail.sh recover [name]
```

Recovery refuses an active consumer lock and moves retained claims back to
`inbox/` with the same message IDs. Run `read`, `wait`, or the monitor again to
replay them. Nothing is automatically sent back to the original sender.

Output or event logging may have succeeded before an interruption prevented
the state move. Recovery can therefore repeat output, observations, and any
external effects already performed by a recipient. Use message IDs from file
names and event-log headers to reconcile possible repeats. Transport receipt,
an envelope `ACK`, and completion of agent work are separate events; this is
not an exactly-once execution protocol. Atomic publication protects visibility
during process interruption; it does not promise persistence through power
loss. Interrupted sends can leave unpublished files in `.staging/`; inspect or
remove them only after stopping the corresponding sender.

## Test isolation

Keep the shared default for real collaboration. For tests or an explicitly
isolated sandbox, set `AGENT_MAILBOX_ROOT` to an absolute temporary path on
every participating command. Do not change `HOME`:

```bash
mailbox_test_root="$(mktemp -d)"
AGENT_MAILBOX_ROOT="$mailbox_test_root" MAILBOX_FROM=fixture-alice \
  bash <skill-dir>/scripts/mail.sh send fixture-bob "test message"
AGENT_MAILBOX_ROOT="$mailbox_test_root" \
  bash <skill-dir>/scripts/mail.sh read fixture-bob
```

The override is inherited by `spawn.sh` delivery and its recovery command;
keep it set when retrying an isolated delivery. The transport never chooses
an isolated root automatically.

## Clean

```bash
bash <skill-dir>/scripts/mail.sh clean [name|all]
```

Deletes this session's mailbox, a named mailbox, or every mailbox with `all`.
Stop its senders and consumers before cleaning a mailbox. Confirm with the
user before running `clean all`.
