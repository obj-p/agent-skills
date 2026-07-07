---
name: handoff
description: Write a handoff file so a fresh session can resume work, or pick up the latest handoff and continue it. Use when ending or pausing a work session and wanting the next session (Claude or Codex) to continue without replaying context, or when starting a session that should resume handed-off work. Run `handoff pickup` to resume; run with no argument (or a note) to write one.
---

# Handoff

Hand work between sessions through a full-context handoff file. Handoffs live
under a tool-neutral root, `~/.agents/handoffs/<repo>/<date>-<slug>.md`, so a
handoff written by Claude can be picked up by Codex and the reverse. The
`<repo>` name is derived from the Git common directory, so all worktrees of a
repo share one directory.

The helper script does the deterministic file operations. The judgment, the
memory pass, filling the sections, and verifying against the repo, is yours.

```bash
bash <skill-dir>/scripts/handoff.sh <repo|dir|new|latest|list|archive>
```

## Write a handoff

Run this flow when `$ARGUMENTS` is empty or is just a note to fold in.

1. **Memory pass.** Scan the conversation for durable facts worth keeping
   beyond this task: corrections the user gave on how to work, project
   decisions or constraints not derivable from the code or git history, and
   learned preferences. Save these to your own persistent memory. Skip anything
   the repo, git history, or CLAUDE.md already records, and anything that only
   mattered to this conversation.

2. **Create the file.** Pick a short kebab-case slug for the task:

   ```bash
   bash <skill-dir>/scripts/handoff.sh new <slug> "the original ask in one line"
   ```

   This writes a template with these sections. Fill each one before finishing:

   - **Goal**: the original ask, in one or two sentences
   - **Done**: what is complete, and how each item was verified
   - **Outstanding**: what remains, as a checklist
   - **Next step**: the exact first action the next session should take
   - **Key files**: paths with line numbers for the code that matters
   - **Gotchas**: failed approaches and why, surprising behavior, tooling
     quirks discovered along the way

   Be specific: exact paths, exact commands, exact error messages. The next
   session has none of your context. Gotchas are the most valuable section
   because they are the most expensive to rediscover.

3. **Tell the user** the file path and that they can run `handoff pickup` in a
   fresh session (Claude or Codex) to continue.

## Pick up a handoff

Run this flow when `$ARGUMENTS` starts with `pickup`.

1. **Find it.** If an explicit file path follows `pickup`, use it. Otherwise:

   ```bash
   bash <skill-dir>/scripts/handoff.sh latest
   ```

   This prints the most recently modified active handoff for this repo, or
   nothing if there is none. If there is none, tell the user and stop.

2. **Verify it against reality.** The repo may have changed since the handoff
   was written. Before trusting it, check `git status`, the current branch, and
   `git log` since around the handoff date; read the key files it names and
   confirm the referenced code is still there (line numbers may have drifted);
   and spot-check items listed as done. Tell the user about any drift and how
   you will adjust.

3. **Archive it.**

   ```bash
   bash <skill-dir>/scripts/handoff.sh archive <file>
   ```

4. **Continue.** Summarize the goal, what is done, and what is outstanding in a
   few lines, then start on the next step, honoring the gotchas.

## Environment

- `AGENT_HANDOFF_ROOT`: handoff root (default `~/.agents/handoffs`).
