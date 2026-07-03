---
name: mailbox
description: File-based mailbox for messaging between agent sessions on the same machine. Use to register a mailbox address, send a message to another agent, read unread mail, or watch for incoming mail. Trigger when the user wants sessions or agents to coordinate, hand off, or wait on each other.
compatibility: Requires bash. Works in any harness; live monitoring needs a background-command or monitor capability.
---

# Mailbox

Message passing between agent sessions through plain text files. Mail lives
under `~/.agents/mailbox` by default. Set `AGENT_MAILBOX_ROOT` to move it.

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
stopped. Run it with a Monitor tool if one is available, otherwise as a
background command. If the harness can only run blocking commands, do not
start it. Fall back to `read` between tasks instead.

Report each message to the user as it appears. If the watcher stops, rerun
this command to resume. Stopping the watcher does not unregister the name.

## Send

Run this verbatim. The first word is the recipient and the rest is the
message:

```bash
bash <skill-dir>/scripts/mail.sh send <<'MAILBOX_EOF'
<to> <message...>
MAILBOX_EOF
```

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
