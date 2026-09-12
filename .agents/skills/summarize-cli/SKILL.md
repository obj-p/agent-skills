---
name: summarize-cli
description: Run a command but keep its raw output out of agent context. A free local model condenses the output into a short summary, extraction, or classification, trading thousands of tokens of logs, tests, or diffs for a few hundred. Use whenever command output would be long, noisy, or repetitive.
compatibility: Requires Python 3 and an LM Studio local server compatible with the OpenAI chat completions API, usually at http://127.0.0.1:1234/v1.
---

# Summarize CLI

Use this skill to offload interpretation of command output to a local model
served by LM Studio. The agent still chooses the command. The local model only
receives the captured command output and the summarization instruction.

## When To Use

Use this for commands that produce large, noisy, or repetitive output, such as:

- logs
- test output
- `git diff`, `git log`, or blame output
- `find`, `rg`, package-manager, or linter output
- generated reports that need extraction or clustering

Do not use this when the command output is short enough for the agent to inspect
directly, or when the output contains secrets that should not be sent to the
local LM Studio server.

## Command

Run:

```bash
python3 <skill-dir>/scripts/summarize_cli.py \
  --instruction "Summarize the failures and list the most actionable next steps." \
  -- bash -lc "pytest -q"
```

Replace `<skill-dir>` with this skill's directory path. In this repository, that
is `.agents/skills/summarize-cli` when running from the repository root.

The helper checks the server's model list before running the command and rejects
an explicit model ID that is absent from that list. Listing does not prove a
model is loaded or ready for inference: [LM Studio's models endpoint](https://lmstudio.ai/docs/developer/openai-compat/models)
can include downloaded models when Just-In-Time loading is enabled. Inference
can still fail after the command runs; the raw output remains available.

Everything after `--` is the command to execute. Use `bash -lc` only when shell
features such as pipes, redirects, globbing, or compound commands are needed.
Do not use the helper to obscure commands from the agent runtime's normal
review, approval, or sandboxing process.

## Options

- `--instruction`: Required. Tell the local model exactly what to return.
- `--base-url`: LM Studio server base URL. Defaults to
  `LMSTUDIO_BASE_URL`, then `http://127.0.0.1:1234/v1`.
- `--model`: Model name. Defaults to `LMSTUDIO_MODEL`. If unset, the helper
  queries `/models` and uses the first listed ID without `embed` in its name.
  This name heuristic does not prove chat compatibility; use an explicit ID
  when needed.
- `--timeout`: Command timeout in seconds. Defaults to `120`.
- `--max-output-chars`: Maximum excerpt characters sent to the model, including
  stream/byte-range labels. Defaults to `12000`. This is a character budget,
  not a token or context-window guarantee; allow room for the instruction and
  response in the chosen model.
- `--cwd`: Working directory for the command. Defaults to the current directory.
- `--artifact-root`: Root for unique retained run directories. Defaults to
  `SUMMARIZE_ARTIFACT_ROOT`, then `~/.agents/summarize-cli`. Use a temporary root
  for tests. Each run directory is private to its owner.
- `--preserve-exit-code`: Accepted for compatibility; this is now the default.
- `--ignore-command-exit-code`: Opt into the old behavior: exit `0` after a
  successful summary even when the command failed. Errors in the summarizer
  still return `2`. Mutually exclusive with `--preserve-exit-code`.

## Status and Verification

The summary is written to stdout. Stderr emits `summarize-cli: <JSON>` records
before execution, before inference, and at completion. The final record contains
`command_exit`, `timed_out`, `model`, `truncated`, `summary_status`, `wrapper_exit`,
and `artifact_dir`. A null command exit means the command did not run or no
outcome was captured. Status facts are produced by the helper independently of
the model's prose. `truncated` describes the model input, not the raw logs.

By default, a nonzero command status takes precedence even if summarization
also fails. Timeouts return `124`; signal termination uses `128 + signal`.
A successful command followed by a summarizer/setup/capture error returns `2`.
Use the JSON fields to distinguish a command exiting `2` from a helper error.
A command interrupted through the wrapper returns `130`.

The reported run directory retains:

- `stdout.log` and `stderr.log`: original bytes streamed directly to disk, with
  no capture-size limit in RAM. The streams are separate; cross-stream ordering
  is not reconstructed. Run foreground commands; detached background writers
  can continue changing their inherited log files after the command exits.
- `model-input.txt`: the exact selected excerpts sent as captured output.
- `metadata.json`: the latest status, byte counts, selected ranges, and coverage.
- `summary.txt`: the successful summary, when available.

Inspect an artifact directly instead of rerunning an expensive or state-changing
command, for example `rg -n -a 'ERROR|FAILED' /path/to/run/stdout.log`. Artifacts
remain after success and failure; remove a run directory once it is no longer
needed. Disk usage grows with command output and retained runs.

Large outputs share the excerpt budget between nonempty stdout and stderr.
The selector scans the full files in bounded chunks for diagnostic keywords
(such as error, failed, exception, warning, and timeout), keeps up to eight
first/last distinct context windows per stream, and samples the beginning,
middle, and end. Byte-range labels identify the source of each excerpt.
Non-UTF-8 bytes or split Unicode boundaries are replaced only in model input;
the raw files preserve every captured byte.

Selection is heuristic: unfamiliar diagnostics, dense failures, long messages,
and small budgets can leave important evidence out. `coverage` reports selected
versus total bytes and selected diagnostic-window counts (match counts are
null when a full-output fast path avoids scanning); partial coverage must not be
interpreted as proof that no other failures exist. Inspect the raw artifacts
when the summary affects a decision. Model fidelity and net savings are assessed
separately in issue #15.

## Good Instructions

Prefer specific output contracts:

```text
Return only:
1. failing test names
2. likely root cause
3. smallest next command to run
```

```text
Group repeated errors by cause. Include one representative line for each group.
Ignore progress bars and successful checks.
```

```text
Extract filenames that need edits. Return a markdown table with file, issue,
and confidence.
```

## Safety

- Review the command before running it, just as with any other Bash command.
- The wrapped command must still satisfy the active agent runtime's command
  review, approval, and sandbox policy. Do not hide destructive commands or
  shell composition inside the helper invocation.
- Do not send secrets, credentials, private keys, or sensitive customer data to
  the local model.
- If command output may include secrets, run a narrower command or redact output
  before using this skill.
- Treat the local model summary as a helper result, not as ground truth. When
  the result affects code changes or destructive actions, verify the relevant
  lines directly.
