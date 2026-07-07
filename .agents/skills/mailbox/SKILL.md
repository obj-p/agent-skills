---
name: mailbox
description: File-based mailbox for messaging between agent sessions on the same machine. Use to register a mailbox address, send a message to another agent, read unread mail, wait for incoming mail with long-poll semantics, or watch for incoming mail. Trigger when the user wants sessions to coordinate, hand off, monitor, or wait on each other.
compatibility: Requires bash. Works in any harness; live auto-reporting requires a Monitor tool.
---

# Mailbox

Message passing between agent sessions through plain text files. Mail lives
under the fixed root `~/.agents/mailbox`. Do not use repo-local mailbox
directories or override the root per repository; every agent session on the
machine must use this same location to exchange messages.

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

The watcher polls every 2 seconds, prints each new message, and archives it.
With a number it stops after that many seconds. With no number it runs until
stopped.

Use a Monitor tool for live auto-reporting. Only a Monitor tool can wake the
agent when new watcher output appears; report each message to the user as the
Monitor surfaces it. If the watcher stops, rerun this command to resume.

If no Monitor tool is available, do not rely on an unbounded background watcher
to surface mail. It may keep running without notifying the agent. Instead, use
`wait` with a timeout between tasks, run `read`, or run
`mail-watch.sh [name] [seconds]` with a bounded duration so the command
completes and returns any messages it saw.

Stopping the watcher does not unregister the name.

## Wait

```bash
bash <skill-dir>/scripts/mail.sh wait [name] [seconds]
```

Blocks until at least one unread message arrives, then prints and archives it
the same way `read` does. With `seconds`, exits `124` on timeout. Without
`seconds`, waits until mail arrives.

Use this as the long-poll primitive for harnesses without a live Monitor tool
and for MCP/plugin bridges that need a request/response wait operation.

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

Prints unread messages and archives them.

## Clean

```bash
bash <skill-dir>/scripts/mail.sh clean [name|all]
```

Deletes this session's mailbox, a named mailbox, or every mailbox with `all`.
Confirm with the user before running `clean all`.
