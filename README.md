# Agent Skills

Portable Agent Skills for Claude, Codex, and other clients that support the
open Agent Skills format. Skills live in `.agents/skills/`, one directory per
skill with a `SKILL.md` inside.

## Current Skills

- `summarize-cli`: run a command and ask an LM Studio local model to summarize,
  classify, extract, or explain the captured output.
- `mailbox`: file-based message passing between agent sessions, with register,
  send, read, monitor, and clean subcommands.

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
