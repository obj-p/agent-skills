# Memory And Messaging Systems

Use the simplest system that preserves the coordination state the agents need.
The local file mailbox plus Claude-compatible shared Markdown memory is the
default for same-machine work.

## Recommended Baseline

- **Mailbox**: short-lived alerts, requests, reviews, and handoffs. Use an
  envelope with `THREAD`, `VERB`, `FROM`, and `TO` for correlated replies.
- **Shared Markdown memory**: durable objective, decisions, task ownership,
  blockers, and verification results under `~/.agents/memory/shared`.
- **Git**: source-of-truth diff, review surface, and recovery mechanism.
- **Claude handoff files**: cross-tool pause/resume files under
  `~/.claude/handoffs/<repo>/`.

This baseline is easy to inspect, works offline, and does not require a server.
Its main limitation is that it is single-machine unless the files are synced.

## Systems Worth Exploring

- **SQLite queue and memory store**: good next step for multi-agent local work.
  It can store messages, tasks, decisions, acknowledgements, and file ownership
  with transactions and useful queries. Use WAL mode and short transactions.
- **MCP broker**: useful when Claude, Codex, and other clients should share the
  same tools for send/read/search/acknowledge. This is the cleanest path toward
  portable, policy-controlled collaboration across clients.
- **Git-native handoffs**: use branches, commits, notes, and PR comments for
  durable async collaboration. Good when humans also need review history.
- **Issue tracker or project board**: use GitHub Issues, Linear, or similar
  when tasks span machines, people, or days. Treat it as task memory, not chat.
- **Vector or RAG memory**: useful for finding old decisions and related prior
  work. Do not make it the source of truth; store canonical decisions in a
  structured log first, then index them.
- **CRDT/shared document state**: consider only for simultaneous editing of the
  same document or canvas. It is usually too much machinery for code-agent
  handoffs.
- **Notification layer**: terminal monitors, OS notifications, or webhooks can
  improve responsiveness, but should not be the only record of state.

Avoid sockets, Redis, or hosted queues for the default workflow. They add
runtime dependencies and reduce inspectability. Add them only when the task
requires cross-machine real-time coordination.

## Selection Heuristics

- Same machine and short task: mailbox plus shared Markdown memory.
- Same repo and human review needed: shared journal plus Git branch or PR.
- Multiple machines or long-running work: issue tracker or MCP broker.
- Many agents or frequent queries over state: SQLite-backed broker.
- Need semantic lookup over prior work: structured log plus vector index.

## Minimum Data Model For A Broker

Any future broker should store:

- agents: address, client, capabilities, last seen
- messages: id, from, to, body, created_at, read_at, thread_id
- tasks: id, owner, status, title, files, updated_at
- decisions: id, author, text, rationale, created_at
- handoffs: id, from, to, objective, next_action, verification
- artifacts: path, owner, status, notes

Add acknowledgements before adding complex routing. Agents need to know whether
a counterpart saw a request before they need channels, priorities, or search.
