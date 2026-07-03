# Agent Skills

Portable Agent Skills for Claude, Codex, and other clients that support the
open Agent Skills format. Skills live in `.agents/skills/`, one directory per
skill with a `SKILL.md` inside.

## Current Skills

- `summarize-cli`: run a command and ask an LM Studio local model to summarize,
  classify, extract, or explain the captured output.

## Validation

```bash
scripts/validate-skills.sh
```

This checks local `SKILL.md` files for the basic fields shared by compatible
Agent Skills clients.
