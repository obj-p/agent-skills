---
name: watchdog
description: Mechanical supervisor that monitors a fleet of agent sessions by their self-reported context gauges and heartbeats, and fires threshold events (warn, hard, stale) so a coordinator or worker can be handed off to a successor before it runs out of context or after it dies. Use when running long multi-agent workflows that must survive context exhaustion or a dead agent without stranding the fleet.
---

# Watchdog

## Overview

A watchdog is a small, mechanical supervisor for a fleet of agent sessions
(coordinator, reviewers, workers) exchanging work over the `mailbox` skill.
Each agent self-reports a **context gauge** — used tokens, limit, and a
heartbeat timestamp. The watchdog polls those gauges and fires one event per
agent when a threshold is crossed:

- **WARN** at `WARN_PCT` (default 70%): the agent should write a handoff now
  and keep working.
- **HARD** at `HARD_PCT` (default 85%): the agent is nearly out of context and
  needs a successor.
- **STALE** when the heartbeat is older than `STALE_SECS` (default 120s): the
  agent likely died and needs a successor.

The watchdog is deliberately dumb. It holds no plan and does no reasoning — an
LLM watchdog would suffer the same context exhaustion it exists to cure. All
judgment stays in the agents it supervises; the watchdog only enforces
thresholds and invokes an action.

## Design principles

- **Safety net, not primary path.** A healthy coordinator hands off to its own
  successor gracefully at its hard threshold (see the `handoff` skill). The
  watchdog exists for the cases it *can't* handle: it died, it hung, or it blew
  past the threshold without handing off. Keep the two roles distinct so they
  never both spawn the same successor.
- **Single-owner invariant.** Every mailbox name (`coordinator`, `worker-2`)
  must have at most one live owner. A successor must claim the name atomically
  before draining the inbox, and the watchdog must only act on an agent it has
  observed go STALE or cross HARD. This prevents two sessions both registering
  the same name. (Lease mechanics are being designed; today the watchdog
  de-dupes by only firing when an agent's state escalates.)
- **Mechanical, not agentic.** The watchdog is a shell loop suitable for
  `launchd`/`cron` supervision. It should itself be supervised so the
  supervisor of the fleet does not become a single point of failure.

## Feeding the gauge

Nothing reports context usage automatically. Each agent refreshes its own
gauge on a cadence (for example after each mailbox turn):

```bash
bash <skill-dir>/scripts/watchdog.sh gauge <name> <used> <limit> [role]
```

- `<name>`: the agent's mailbox name (e.g. `coordinator`, `worker-1`).
- `<used>` / `<limit>`: token counts; the script computes percent.
- `[role]`: `coordinator` or `worker` (default `worker`), used to pick a
  handoff strategy.

The refresh doubles as the heartbeat — a gauge that stops updating goes STALE.
On a clean exit an agent should call `watchdog.sh clear <name>` so the watchdog
does not treat the normal shutdown as a death.

Where the token numbers come from is harness-specific. Wire whatever signal the
harness exposes (a hook, a status line, an estimate) to this `gauge` call. This
skill starts with Claude sessions; the same gauge call works for Codex once a
Codex-side source is wired.

## Monitoring

```bash
bash <skill-dir>/scripts/watchdog.sh status        # one-shot table of all gauges
bash <skill-dir>/scripts/watchdog.sh watch [secs]   # poll loop; fire events
```

`status` prints name, role, used/limit, percent, heartbeat age, and state.
`watch` polls every `WATCHDOG_POLL_SECS` (default 5) and fires an event only
when an agent's state escalates, so a stuck-at-HARD agent is not re-notified
every tick. Give `watch` a seconds argument to bound the run, or supervise an
unbounded `watch` with `launchd`.

## Actions

When an event fires the watchdog, by default, logs to
`~/.agents/context/watchdog.log` and, if `WATCHDOG_NOTIFY` is set, mails a
notice to that mailbox. To drive a real handoff or spawn, set `WATCHDOG_ON_EVENT`
to a command; it is invoked as:

```text
<WATCHDOG_ON_EVENT> <state> <name> <role> <percent>
```

The handoff a successor needs already exists as substrate: the coordinator's
pool file plus `MEMORY.md` plus the mailbox inbox for a coordinator, or the
worktree branch plus a next-step note for a worker. A HARD/STALE action should
therefore: refresh/confirm that substrate, register the successor under the
same mailbox name (claiming the single-owner lease), drain the inbox, and stop
the old session.

## Launching a successor

`spawn-agent` launches **one-shot** workers, so it cannot stand up a persistent
successor that keeps servicing a mailbox. A successor needs one of the
persistent Claude Code shapes:

- **Headless streaming (recommended on macOS).** A detached process that reads
  newline-delimited JSON user messages from stdin and stays alive until stdin
  closes:

  ```bash
  claude -p --input-format stream-json --output-format stream-json --verbose \
    --session-id "$UUID" \
    --permission-mode dontAsk --allowedTools "Bash,Read,Edit" < mailbox.fifo
  ```

  Keep a FIFO open as stdin and write one user message per mailbox item.
  Supervise it with `launchd` (or `nohup`) so it survives the parent shell.
- **Agent SDK** (`ClaudeSDKClient` / streaming `query`) if you want to host the
  loop with native message objects and tool-approval callbacks.

Do NOT use plain `claude -p "text"` or an SDK string `query` for the loop —
both are one-shot and exit.

**Human pickup.** Pin `--session-id <uuid>` at launch. A headless/SDK session
does not show in the interactive picker, but a human can still take it over
with `claude --resume <uuid>` run **from the same project directory** (resume is
scoped per-directory and its worktrees).

**Remote control.** To drive a running successor from claude.ai/code or the
mobile app, start it with `claude remote-control` (or `--remote-control`). The
session still runs on this machine; the browser/phone is a window. This is a
research preview (needs a recent Claude Code, claude.ai OAuth login, not API
keys) — verify against `claude --version` before relying on it.

Wire the chosen launcher into `WATCHDOG_ON_EVENT` so a HARD/STALE event starts
the successor and hands off.

## Environment

- `WATCHDOG_WARN_PCT` (default 70), `WATCHDOG_HARD_PCT` (default 85).
- `WATCHDOG_STALE_SECS` (default 120), `WATCHDOG_POLL_SECS` (default 5).
- `WATCHDOG_ON_EVENT`: command run per event (see Actions).
- `WATCHDOG_NOTIFY`: mailbox to send default notices to.
- `MAILBOX_SKILL_DIR`: mailbox skill dir (default sibling skill, then
  `~/.claude/skills/mailbox`).
