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
- `design-bootstrap`: bootstrap a project-local design language system,
  feature HTML mockups, beat navigation, and PNG/GIF render scripts for
  agent-created design workflows.

## Using These Skills

Codex discovers `.agents/skills/` automatically, both in this repository and
from `~/.agents/skills` for global use.

Claude Code reads `.claude/skills/` instead. This repository ships a
`.claude/skills` symlink pointing at `.agents/skills`, so both tools work from
a clone with no setup. The symlink requires a symlink-capable checkout, which
excludes default Windows Git settings.

For global use, symlink the skills into each tool's user directory
(`~/.claude/skills` for Claude, `~/.agents/skills` for Codex) with the install
script:

```bash
scripts/install-skills.sh            # all skills, both tools
scripts/install-skills.sh --codex    # only ~/.agents/skills
scripts/install-skills.sh mailbox    # just one skill
scripts/install-skills.sh --uninstall
```

The links point back at this repo, so edits and `git pull` take effect
immediately. It is idempotent and never overwrites a real directory. It needs a
symlink-capable checkout, which excludes default Windows Git settings.

## Validation

```bash
scripts/validate-skills.sh
```

This checks local `SKILL.md` files for the basic fields shared by compatible
Agent Skills clients.
