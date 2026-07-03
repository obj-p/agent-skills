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

The helper checks that the LM Studio server is reachable before running the
command, so an expensive command is never run when no summary is possible.

Everything after `--` is the command to execute. Use `bash -lc` only when shell
features such as pipes, redirects, globbing, or compound commands are needed.
Do not use the helper to obscure commands from the agent runtime's normal
review, approval, or sandboxing process.

## Options

- `--instruction`: Required. Tell the local model exactly what to return.
- `--base-url`: LM Studio server base URL. Defaults to
  `LMSTUDIO_BASE_URL`, then `http://127.0.0.1:1234/v1`.
- `--model`: Model name. Defaults to `LMSTUDIO_MODEL`. If unset, the helper
  queries `/models` and uses the first loaded non-embedding model.
- `--timeout`: Command timeout in seconds. Defaults to `120`.
- `--max-output-chars`: Maximum captured output sent to the model. Defaults to
  `12000`, sized to fit models with an 8k-token context alongside the
  instruction and response. Raise it only when the loaded model has a larger
  context window.
- `--cwd`: Working directory for the command. Defaults to the current directory.
- `--preserve-exit-code`: Exit with the wrapped command's exit code after a
  successful summary. By default, the helper exits `0` when LM Studio returns a
  summary, even if the wrapped command failed.

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
