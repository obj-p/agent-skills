---
name: shared-memory
description: Durable Markdown memory shared between Claude, Codex, and other agents working the same repo. Records objectives, decisions, task ownership, and notes under a per-workspace namespace both tools can read. Use to persist collaboration state across sessions and agents, or to check what another agent recorded.
---

# Shared Memory

A durable, plain-text journal that outlives a single session and is readable by
any agent on the machine. It complements two other skills: `mailbox` carries
short-lived messages between agents, and `handoff` writes a full-context
handoff file when a session ends. This skill is for the standing record in
between, decisions, tasks, and notes that both agents should see.

Files are Markdown with frontmatter (`metadata.type: shared-agent-memory`) plus
a `MEMORY.md` index, so Claude-style memory readers and Codex both parse the
same namespace.

## Location

The helper writes under
`${AGENT_MEMORY_ROOT:-$HOME/.agents/memory/shared}/<workspace-key>`, where the
key is derived from the current working directory. Run it from the repository
being coordinated so every agent in that repo resolves the same directory.

## Usage

```bash
bash <skill-dir>/scripts/memory.sh init "short project label"
bash <skill-dir>/scripts/memory.sh note codex "Inspected README and mailbox skill."
bash <skill-dir>/scripts/memory.sh decision claude "Use mailbox for alerts and shared-memory for the standing record."
bash <skill-dir>/scripts/memory.sh task codex doing "Implement mail.sh wait."
bash <skill-dir>/scripts/memory.sh handoff codex claude "Please review the split."
bash <skill-dir>/scripts/memory.sh show all
```

Subcommands:

- `init [label]`: create the namespace and record the workspace path and key.
- `note <agent> <message>`: append a timestamped journal entry.
- `decision <agent> <message>`: record a durable decision and its rationale.
- `task <agent> <open|doing|blocked|done> <message>`: track task ownership.
- `handoff <from> <to> <message>`: note a lightweight in-repo handoff.
- `show [target]`: print `path`, `index`, `workspace`, `journal`, `decisions`,
  `tasks`, `handoffs`, or `all`.

## What to record

- user objective and constraints
- file ownership and active tasks
- decisions and rationale
- blockers and open questions
- verification commands and results

Do not store secrets, credentials, private keys, or sensitive user data here
unless the user explicitly asks for that exact storage.

## Environment

- `AGENT_MEMORY_ROOT`: shared memory root (default `~/.agents/memory/shared`).
  `AGENT_COLLAB_ROOT` is still honored for compatibility.
- `AGENT_MEMORY_WORKSPACE`: override the workspace path used to derive the
  namespace key (default: current directory). `AGENT_COLLAB_WORKSPACE` is still
  honored.
