---
name: coordination
description: Playbook for running a merge-gated coordinator/reviewer/worker fleet over the file mailbox, where a coordinator assigns briefed work, workers implement in isolated worktrees, a reviewer does an independent quality pass, and the coordinator merges only on green CI plus approval. Includes context survival, the coordinator spins up a watchdog and hands off to a fresh successor before it exhausts context or dies. Use when orchestrating several agents on one repo through many issues/PRs and the run outlives a single session's context.
---

# Coordination

## Overview

A fleet of agent sessions drives a repo through issues and PRs, coordinating
over the `mailbox` skill. Three roles, one merge-gated loop:

- **Coordinator** — triages the issue backlog into a ready pool, assigns
  briefed work, routes finished PRs to review and CI, and merges. Owns the
  mailbox name `coordinator` and the pool/state file.
- **Worker** — takes one assignment, works in its own git worktree, runs the
  local gates, opens a PR, and reports READY.
- **Reviewer** — an independent quality pass on the PR diff (best practices,
  style, comment discipline), returning APPROVE or CHANGES-REQUESTED.

The coordinator also spins up the `watchdog` skill so the whole fleet survives
context exhaustion or a dead agent — see **Context survival** below.

Message and state shapes are in `references/templates.md`. Read the `mailbox`
skill first for the envelope; this skill reuses it.

## The cycle and its triggers

1. Coordinator triages `gh issue list` into a ready pool (parallelizable,
   non-file-conflicting) in the pool file. A worker mails `ASK`; coordinator
   replies with a **BRIEF** (issue #, scope, traps, verification, gates, review
   tier).
2. Worker makes its OWN worktree
   (`git worktree add .worktrees/<slug> -b <slug> origin/main`), implements,
   runs local gates (build/test, lint, `/code-review <tier>`, `/simplify` —
   skip on LOW), opens a PR, mails **READY** with PR #, head SHA, proposed tier.
3. **Trigger — PR opened:** coordinator routes the PR to the reviewer, parallel
   to CI.
4. Reviewer does an independent quality pass on the diff (NOT a `/code-review`
   re-run) and mails APPROVE or CHANGES-REQUESTED (file:line, BLOCKING vs NIT).
5. **Trigger — CI green AND reviewer APPROVE:** coordinator squash-merges and
   confirms the SHA. CHANGES-REQUESTED routes back to the worker, then
   re-review.

## State and resume

- **Mailbox** root `~/.agents/mailbox`; the coordinator is `coordinator`.
- **Pool/state file** (a scratch Markdown file): ready pool, active streams
  (worker → issue → worktree → PR → tier → status), merge queue, held PRs,
  decisions log. Template in `references/templates.md`.
- **Durable decisions** go to memory files + `MEMORY.md` (see `shared-memory`).
- **GitHub is truth** for PR/CI state
  (`gh pr view --json mergeStateStatus,headRefOid`, check-runs on the head SHA).
- A **fresh coordinator resumes** from: pool file + `MEMORY.md` +
  `mail.sh read coordinator` + `git worktree list` + `gh pr list`.

## Fixed rules

- Verify green-on-HEAD before merge (`mergeState CLEAN` + check-run success on
  `headRefOid`).
- One worktree per worker; stage explicit paths, never `git add -A`.
- Merge = squash, ONLY on CI-green AND reviewer APPROVE.
- Tiering: `/code-review` takes low|med|high; the author proposes, the reviewer
  escalates, never silently downgrades. Skip `/simplify` on LOW.
- Stale base: a PR branched before a merged fix reruns its old tree — rebase or
  merge in main rather than re-running; force-push may be blocked, so merge-in
  (it collapses under squash).
- Assign with a brief; recommend when asking the owner.
- CI capacity: a single serial runner means holding low-priority PRs and
  staggering opens; match active work to runner capacity.

## Context survival (watchdog + successor)

A long coordinator run has no built-in guardrail against context exhaustion.
The coordinator spins up the `watchdog` skill and every agent refreshes a
context gauge each turn (`watchdog.sh gauge <name> <used> <limit> <role>`). The
watchdog fires:

- **WARN (~70%)** — the agent refreshes the pool file and writes a `handoff`
  note now, and keeps working.
- **HARD (~85%)** — the agent needs a successor.
- **STALE (heartbeat gone)** — the agent likely died; it needs a successor.

**Coordinator handoff (auto at HARD/STALE):** refresh pool file → write a
`handoff` note → start a successor session → the successor claims the
`coordinator` name (single-owner lease), drains the inbox
(`mail.sh read coordinator`), reads the pool file + `MEMORY.md` → the old
coordinator FYIs active agents "continue with <successor>" then stops. Only one
session ever owns `coordinator`; the inbox persists across the swap so nothing
is lost.

**Worker handoff (lighter):** commit WIP on the worktree branch + a note of
issue/PR/tier/next-step; a successor checks out the branch.

The successor is launched as a persistent headless `claude` session (streaming
stdin, or the Agent SDK), pinned with `--session-id` so a human can later
`claude --resume <uuid>` from the project directory, or driven remotely with
`claude remote-control`. See **Launching a successor** in the `watchdog` skill
for the exact command wired into its action hook.

## Failure modes to guard

- **Crossed messages** — a worker mails READY while a coordinator hold is in
  flight (a near-miss merged an unversioned PR). Reconcile idempotently before
  acting on any READY.
- **Session-varying harness caps** — some sessions can self-merge protected
  main or force-push; others are classifier-denied. Support both, with an
  operator-merge fallback.
- **Non-strict ruleset** — a merged tree can differ from any PR-tested tree;
  keep the main-push CI run as the safety net.
- **Serial runner saturation** — fan-out beyond merge throughput builds a held
  backlog; do not manufacture filler work.
- **Owner directives reaching a worker directly** — cause drift; the worker
  should immediately flag the coordinator.

## Related skills

`mailbox` (transport + envelope), `watchdog` (context survival), `handoff`
(successor bootstrap), `shared-memory` (durable decisions), `spawn-agent`
(one-shot delegation), `agent-collaboration` (general collaboration playbook).
