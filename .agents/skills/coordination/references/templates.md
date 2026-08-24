# Coordination templates

Verbatim message and state shapes for the coordinator/reviewer/worker loop.
All messages use the `mailbox` skill envelope (`THREAD`/`VERB`/`FROM`/`TO`).

## BRIEF — coordinator assigns work to a worker

```text
<worker> THREAD: <repo>-<date>-<slug>
VERB: ASK
FROM: coordinator
TO: <worker>

Assignment: #<N> — <one-line>. <lane: sim/no-sim + why chosen (avoids conflict X)>.
Worktree: `git worktree add .worktrees/<slug> -b <slug> origin/main`.
Scope: <what changes, key files>.
Verify FIRST (write the check before the fix): <repro / failing test>.
Traps: (1) <trap>; (2) <shared-file flag>.
Gates: /simplify + /code-review <tier>, full local suite, stage explicit paths
  (no `git add -A`); on green report READY (coordinator merges). Proposed TIER = <low|med|high>.
Sync at start + major boundaries; flag conflicts. ACK to take it.
```

## READY — worker reports a finished PR

```text
coordinator THREAD: <repo>-<date>-<slug>
VERB: DONE
FROM: <worker>
TO: coordinator

Issue #<N> done. PR #<P>, head SHA <sha>. Gates green (build/test/lint,
/code-review <tier>, /simplify). Proposed TIER <t>. Worktree <path>.
```

## REVIEW verdict — reviewer replies on a PR diff

```text
coordinator THREAD: <repo>-<date>-<slug>
VERB: DONE
FROM: reviewer
TO: coordinator

PR #<P>: APPROVE
  — or —
PR #<P>: CHANGES-REQUESTED
  <file>:<line> BLOCKING — <what and why>
  <file>:<line> NIT — <optional>
```

## POOL / STATE file — coordinator scratch (a Markdown file)

```text
# Coordinator — work pool (<date>)
Mailbox: `coordinator`. Agents ASK for work; assign from the pool below.

## MERGE: CI required gate (ruleset <id>, `bazel` check, non-strict).
## Merge = green CI + gates clean + reviewer APPROVE. Some sessions self-merge;
##   others report READY -> coordinator merges.
## MERGED this session: #<N>(<sha> <slug>) ...
## STALE-BRANCH REBASE (pre-<fix-sha> branches reflake): #<N> ...
## IN CI QUEUE: #<N> ...  | HELD (build-and-hold, runner saturated): #<N> ...
## SCOPING (plan-only, no CI): <worker> #<N> ...

## PENDING OWNER SIGNAL
- <thing>: <what fires it> -> <action>

## Active streams (all in own worktrees off origin/main <sha>)
- <worker>  -> #<N> <one-line>. wt <path>. <status>. TIER <t>.

## Decisions logged
- <decision + why>
```

## HANDOFF FYI — coordinator tells the fleet it is being replaced

```text
<agent> THREAD: <repo>-<date>-handoff
VERB: FYI
FROM: coordinator
TO: <agent>

Handing off coordinator role to <successor> (context <state>). Continue there.
State is in the pool file + MEMORY.md; inbox persists. This session is stopping.
```
