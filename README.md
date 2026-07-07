# Agent Skills

Portable Agent Skills for Claude, Codex, and other clients that support the
open Agent Skills format. Skills live in `.agents/skills/`, one directory per
skill with a `SKILL.md` inside.

## Current Skills

- `summarize-cli`: run a command and ask an LM Studio local model to summarize,
  classify, extract, or explain the captured output.
- `mailbox`: file-based message passing between agent sessions, with `iam`
  (register), `send`, `read`, `wait`, and `clean` subcommands, plus a separate
  watch script for monitoring.
- `agent-collaboration`: playbook that ties the mailbox, shared-memory,
  handoff, and spawn-agent skills into collaboration modes, a coordination
  message shape, conflict control, and a verification loop.
- `shared-memory`: durable Markdown record of objectives, decisions, tasks, and
  notes under a per-workspace namespace both Claude and Codex can read.
- `spawn-agent`: spawn a one-shot Claude or Codex worker that runs a job and
  mails its result back to a mailbox address as a `THREAD`/`VERB` envelope.
- `handoff`: write a full-context handoff file, or pick up the latest and
  continue, under the tool-neutral root `~/.agents/handoffs/<repo>/`.

## Using These Skills

Codex discovers `.agents/skills/` automatically, both in this repository and
from `~/.agents/skills` for global use.

Claude Code reads `.claude/skills/` instead. This repository ships a
`.claude/skills` symlink pointing at `.agents/skills`, so both tools work from
a clone with no setup. The symlink requires a symlink-capable checkout, which
excludes default Windows Git settings.

For global use, symlink a skill into each tool's user directory:

```bash
ln -s "$PWD/.agents/skills/summarize-cli" ~/.claude/skills/summarize-cli
ln -s "$PWD/.agents/skills/summarize-cli" ~/.agents/skills/summarize-cli
```

## Validation

```bash
scripts/validate-skills.sh
```

This checks local `SKILL.md` files for the basic fields shared by compatible
Agent Skills clients.
