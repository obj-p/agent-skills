---
name: agent-collaboration
description: Playbook for coordinating work between Claude, Codex, and other coding agents. Ties together the mailbox, shared-memory, handoff, and spawn-agent skills into modes, a coordination message shape, conflict control, and a verification loop. Use when asked to have agents collaborate, split implementation and review, hand off work, or maintain durable collaboration state.
---

# Agent Collaboration

## Overview

This is a playbook, not a new mechanism. It composes four atomic skills into a
way of working. Read each one's `SKILL.md` for the details it owns:

- **`mailbox`**: register an address, send and receive messages, and `wait` or
  `watch` for incoming mail. Owns identity, the mailbox root, and the
  `THREAD`/`VERB` envelope convention.
- **`shared-memory`**: durable Markdown record of objectives, decisions, tasks,
  and notes that both agents can read across sessions.
- **`handoff`**: write a full-context handoff file, or `handoff pickup` to
  resume one, under the tool-neutral `~/.agents/handoffs/<repo>/`.
- **`spawn-agent`**: delegate a self-contained job to a fresh Claude or Codex
  worker that mails its result back.

## Quick Start

1. Register each agent's address with the `mailbox` skill. Common names are
   `codex` and `claude`.
2. From the repository root, initialize the shared record and note the goal:

   ```bash
   bash <shared-memory-skill-dir>/scripts/memory.sh init "short project label"
   bash <shared-memory-skill-dir>/scripts/memory.sh note codex "Objective: ..."
   bash <shared-memory-skill-dir>/scripts/memory.sh task codex open "Claude review: ..."
   ```

3. Send the counterpart a mailbox message using the coordination shape below.
4. While waiting, keep moving on independent work. Read, wait, or watch mailbox
   before finalizing.
5. Record decisions, blockers, and verification results in shared memory.

## Collaboration Modes

- **Lead plus reviewer**: one agent edits; the other reviews assumptions,
  tests, and final diff. Prefer for small or risky changes.
- **Split ownership**: assign file or subsystem owners before editing. Prefer
  for larger tasks with separable modules.
- **Research plus implementation**: one agent investigates APIs, examples, or
  failing behavior; the other patches and verifies.
- **Handoff**: one agent stops and gives the other enough state to resume
  without replaying the whole conversation. Use the `handoff` skill.

Do not coordinate for its own sake. For a small single-agent change, record a
brief note only if another agent is actually involved.

## Coordination Message Shape

The `mailbox` skill defines the `THREAD`/`VERB` envelope and the verbs
(`ASK`, `ACK`, `DONE`, `BLOCKED`, `FYI`). For collaboration, fill the body with
the fields the other agent needs to act:

```text
Objective:
Current state:
Owned files:
Changed files:
Decisions:
Open questions:
Next action:
Verification:
Shared memory:
```

Include exact paths and commands when they matter, and absolute dates for
anything time-sensitive. When waiting on another agent, wait for the matching
`THREAD` and prefer an explicit `ACK`, `DONE`, or `BLOCKED` over assuming
silence means agreement.

## Conflict Control

- Assign one editing owner per file or subsystem before parallel work, and
  announce it (an `FYI` over mailbox, or a `task` entry in shared memory).
- Treat task ownership as advisory, not a lock. Check `git status` and reread
  files before editing.
- Prefer patches that are easy for the other agent to inspect.
- If agents disagree, record the decision and reason with `memory.sh decision`,
  then use the user's newest instruction as the tie breaker.

## Verification Loop

Before finalizing collaborative work:

1. Read or wait for unread mailbox messages.
2. Show shared memory and check for unresolved `open`, `doing`, or `blocked`
   tasks.
3. Run the repository's relevant validation or tests.
4. Record the verification result in shared memory.
5. Write a `handoff` or send a final message if another agent still has pending
   work.

## Memory And Messaging Options

For choosing systems beyond the local mailbox and shared memory, read
`references/memory-and-messaging.md`. Use that reference when the user asks
about architecture, future improvements, multi-machine coordination, semantic
memory, or alternatives to the file-based mailbox.
