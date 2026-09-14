---
name: handoff
description: Write a handoff file so a fresh session can resume work, or pick up the latest handoff and continue it. Use when ending or pausing a work session and wanting the next session (Claude or Codex) to continue without replaying context, or when starting a session that should resume handed-off work. Run `handoff pickup` to resume; run with no argument (or a note) to write one.
---

# Handoff

Hand work between sessions through a full-context handoff file. Handoffs live
under a tool-neutral root, `~/.agents/handoffs/<repo-key>/`, so a handoff written
by Claude can be picked up by Codex and the reverse. The readable `<repo-key>`
includes a hash of the canonical Git common-directory path: unrelated repos
with the same folder name stay separate, while nested directories, symlinked
paths, and all worktrees of one repo select the same storage. Outside Git,
identity uses the canonical current directory; different directories stay
separate. Moving the Git common directory changes the key.

The helper uses Bash and Python 3.9+ (standard library only) on macOS/Linux.
The judgment, memory pass, filling the sections, and verifying against the repo
are yours.

```bash
bash <skill-dir>/scripts/handoff.sh <repo|dir|new|latest|list|archive|legacy-dir|import>
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

   The template also records **Source state** as JSON: repository key, canonical
   Git common directory, source worktree, branch/commit when available, and
   dirty state with porcelain status entries. Preserve this capture when filling
   the task sections. `null` means unavailable (for example, a detached branch
   or non-Git status), not clean. This records filenames/status, not file contents
   or a backup of uncommitted changes.

3. **Tell the user** the file path and that they can run `handoff pickup` in a
   fresh session (Claude or Codex) to continue.

## Pick up a handoff

Run this flow when `$ARGUMENTS` starts with `pickup`.

1. **Find it.** If an explicit file path follows `pickup`, use it. Otherwise:

   ```bash
   bash <skill-dir>/scripts/handoff.sh latest
   ```

   This prints the most recently modified active handoff for this repo, or
   nothing with exit status 0 if there is none, including an archive-only
   directory. If there is none, tell the user and stop. Legacy basename storage
   is not searched automatically; see **Recover legacy handoffs** when needed.

2. **Verify it against reality.** The repo may have changed since the handoff
   was written. Compare the saved Source state with the current repository and
   worktree; an explicitly supplied file may belong to another repo. Before
   trusting it, check `git status`, the current branch, and
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

## Storage and recovery

`repo` prints the repository key; `dir` prints its directory. `list` and `latest`
read only active regular `.md` files and do not initialize storage. `latest`
uses modification time, with the filename as a deterministic tie-breaker.

`new` starts with `<date>-<slug>.md`; name collisions receive a unique suffix.
`archive <file>` prints the actual destination and never overwrites an earlier
archive. Always use the returned path. Creation stages a complete template
before publishing it; archiving publishes the destination before removing the
source. Helper writers coordinate through process-lifetime locks. Use local
storage with working file locks and hard links. Editing an existing handoff
still belongs to its owning session; these locks do not serialize editor writes.

An interrupted operation may leave an ignored `.handoff-*.tmp` staging file or
both an active and archived copy. The lock is released when the process exits;
do not delete `.handoff.lock`. Inspect retained files before manually removing
redundant copies or staging files after the writer has stopped.

## Recover legacy handoffs

Old `~/.agents/handoffs/<basename>/` directories remain untouched and are never
selected by `latest`. To locate the previous directory:

```bash
bash <skill-dir>/scripts/handoff.sh legacy-dir
```

Inspect candidate files and verify their repository, branch, and task against
the current checkout. Basenames alone do not establish identity, and old files
may lack Source state. Once a particular file is verified, copy it into the
current namespace:

```bash
bash <skill-dir>/scripts/handoff.sh import /path/to/verified-handoff.md
bash <skill-dir>/scripts/handoff.sh import /path/to/verified-history.md --archived
```

Imports preserve the original bytes and source file, allocate a new name on
collision, and do not invent source metadata. `--archived` keeps history out of
active pickup. Import only the files that belong to this repo; a legacy directory
can contain mixed histories. The same commands can recover selected records
after moving a repository. An explicit `handoff pickup /path/to/file.md` remains
available and requires the same verification before resuming.

## Environment

- `AGENT_HANDOFF_ROOT`: handoff root (default `~/.agents/handoffs`).
