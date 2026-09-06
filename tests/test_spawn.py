#!/usr/bin/env python3
"""Spawn delivery regressions using CLI/transport stubs and temporary storage.

Run: python3 -B -m unittest discover -s tests -p 'test_spawn.py' -v
SPAWN_TEST_SCRIPT optionally selects a candidate spawn.sh for local validation.
"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest


SPAWN = Path(os.environ.get(
    "SPAWN_TEST_SCRIPT",
    Path(__file__).resolve().parents[1] / ".agents/skills/spawn-agent/scripts/spawn.sh",
)).resolve()
ANSWER = 'worker answer: café\n$(touch "$SPAWN_TEST_SENTINEL")\n`touch "$SPAWN_TEST_SENTINEL"`\n\n'
STDERR = "".join(f"worker diagnostic {i}\n" for i in range(25))
WORKER = r'''#!/bin/bash
set -eu
printf 'run\n' >> "$SPAWN_TEST_WORKER_LOG"
printf '%s\n' "depth=${AGENT_SPAWN_DEPTH-unset}" "from=${MAILBOX_FROM-unset}" \
  "sid=${MAILBOX_SESSION_ID-unset}" "claude_sid=${CLAUDE_CODE_SESSION_ID-unset}" \
  > "$SPAWN_TEST_IDENTITY"
case "$0" in
  */codex)
    while [ "$#" -gt 0 ]; do
      if [ "$1" = '-o' ]; then
        shift
        if [ "${SPAWN_TEST_NO_MSG:-0}" != 1 ]; then
          printf '%s' "$SPAWN_TEST_ANSWER" > "$1"
        fi
      fi
      shift
    done
    if [ "${SPAWN_TEST_NO_MSG:-0}" = 1 ]; then
      printf '%s' "$SPAWN_TEST_ANSWER"
    else
      printf 'worker progress\n'
    fi
    ;;
  */claude) printf '%s' "$SPAWN_TEST_ANSWER" ;;
esac
printf '%s' "$SPAWN_TEST_STDERR" >&2
exit "$SPAWN_TEST_WORKER_EXIT"
'''
TRANSPORT = r'''#!/bin/bash
set -eu
[ "$#" -eq 1 ] && [ "$1" = send ]
printf '%s\n' "$MAILBOX_FROM" >> "$SPAWN_TEST_DELIVERY_LOG"
cat > "$SPAWN_TEST_REQUEST"
printf 'transport stdout\n'
printf 'transport stderr\n' >&2
if [ "$SPAWN_TEST_DELIVERY_EXIT" -eq 0 ]; then
  cat "$SPAWN_TEST_REQUEST" > "$SPAWN_TEST_DELIVERED"
fi
exit "$SPAWN_TEST_DELIVERY_EXIT"
'''


class SpawnTests(unittest.TestCase):
    def fixture(self, tool="codex", worker_exit=0, delivery_exit=0):
        temp = tempfile.TemporaryDirectory(prefix="agent-skills-spawn-test-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        fixture = SimpleNamespace(
            root=root, tool=tool, bin=root / "bin",
            mailbox=root / "mailbox 'quoted' $literal",
            artifacts=root / "captures 'quoted' $literal",
        )
        fixture.bin.mkdir()
        # A closed PATH prevents any test from falling through to a real CLI.
        for name in ("bash", "env", "dirname", "mktemp", "cat", "tail", "mkdir", "rm", "date"):
            command = shutil.which(name)
            self.assertIsNotNone(command, f"required fixture utility: {name}")
            (fixture.bin / name).symlink_to(command)
        (fixture.bin / tool).write_text(WORKER)
        (fixture.bin / tool).chmod(0o700)
        fixture.mail_send = fixture.mailbox / "scripts/mail.sh"
        fixture.mail_send.parent.mkdir(parents=True)
        fixture.mail_send.write_text(TRANSPORT)
        fixture.env = dict(os.environ)
        for key in ("SPAWN_CLAUDE_FLAGS", "SPAWN_CODEX_FLAGS", "BASH_ENV", "ENV"):
            fixture.env.pop(key, None)
        fixture.env.update({
            "PATH": str(fixture.bin), "MAILBOX_SKILL_DIR": str(fixture.mailbox),
            "SPAWN_ARTIFACT_ROOT": str(fixture.artifacts), "AGENT_SPAWN_DEPTH": "0",
            "CLAUDE_CODE_SESSION_ID": "parent-claude", "MAILBOX_SESSION_ID": "parent-mail",
            "MAILBOX_FROM": "parent-identity", "SPAWN_TEST_ANSWER": ANSWER,
            "SPAWN_TEST_STDERR": STDERR, "SPAWN_TEST_WORKER_EXIT": str(worker_exit),
            "SPAWN_TEST_DELIVERY_EXIT": str(delivery_exit),
            "SPAWN_TEST_NO_MSG": "0",
        })
        for key, name in (
            ("WORKER_LOG", "workers.txt"), ("IDENTITY", "identity.txt"),
            ("DELIVERY_LOG", "deliveries.txt"), ("REQUEST", "request.txt"),
            ("DELIVERED", "delivered.txt"), ("SENTINEL", "must-not-exist"),
        ):
            fixture.env[f"SPAWN_TEST_{key}"] = str(root / name)
        return fixture

    def run_spawn(self, fixture):
        return subprocess.run(
            [str(fixture.bin / "bash"), str(SPAWN), fixture.tool, "fixture-parent", "fixture job"],
            cwd=fixture.root, env=fixture.env, text=True, capture_output=True, timeout=10,
        )

    def captures(self, fixture):
        return list(fixture.artifacts.iterdir()) if fixture.artifacts.is_dir() else []

    def check_outcome(self, tool, worker_exit, delivery_exit):
        fixture = self.fixture(tool, worker_exit, delivery_exit)
        result = self.run_spawn(fixture)
        self.assertEqual(result.returncode, delivery_exit or worker_exit, result.stderr)
        self.assertEqual((fixture.root / "workers.txt").read_text(), "run\n")
        self.assertEqual((fixture.root / "identity.txt").read_text(),
                         "depth=1\nfrom=unset\nsid=unset\nclaude_sid=unset\n")
        self.assertFalse((fixture.root / "must-not-exist").exists())
        senders = (fixture.root / "deliveries.txt").read_text().splitlines()
        self.assertEqual(len(senders), 1)
        self.assertTrue(senders[0].startswith(f"{tool}-worker-"))
        envelope = (fixture.root / "request.txt").read_text()
        verb = "BLOCKED" if worker_exit else "DONE"
        self.assertTrue(envelope.startswith("fixture-parent THREAD: spawn-fixture-parent-"))
        self.assertIn(f"\nVERB: {verb}\nFROM: {senders[0]}\nTO: fixture-parent\n", envelope)
        self.assertIn(ANSWER.rstrip("\n"), envelope)
        if worker_exit:
            self.assertIn(f"--- exit={worker_exit} stderr tail ---", envelope)
            self.assertIn("worker diagnostic 24\n", envelope)
            self.assertNotIn("worker diagnostic 0\n", envelope)
        if delivery_exit:
            self.assertNotIn("mailed", result.stdout + result.stderr)
            self.assertFalse((fixture.root / "delivered.txt").exists())
            captures = self.captures(fixture)
            self.assertEqual(len(captures), 1)
            capture = captures[0]
            self.assertEqual(capture.stat().st_mode & 0o777, 0o700)
            self.assertIn(str(capture), result.stderr)
            self.assertEqual((capture / "envelope.txt").read_text(), envelope)
            answer_file = "msg.txt" if tool == "codex" else "out.txt"
            self.assertEqual((capture / answer_file).read_text(), ANSWER)
            self.assertEqual((capture / "err.txt").read_text(), STDERR)
            self.assertEqual((capture / "delivery-out.txt").read_text(), "transport stdout\n")
            self.assertEqual((capture / "delivery-err.txt").read_text(), "transport stderr\n")
            self.assertEqual((capture / "status.txt").read_text(),
                             f"worker_exit={worker_exit}\ndelivery_exit={delivery_exit}\n")
            self.assertTrue((capture / "retry.sh").is_file())
        else:
            self.assertIn(f"mailed {verb}", result.stdout)
            self.assertEqual((fixture.root / "delivered.txt").read_text(), envelope)
            self.assertEqual(self.captures(fixture), [])
        return fixture, result

    def test_worker_success(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                self.check_outcome(tool, 0, 0)

    def test_worker_failure(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                self.check_outcome(tool, 7, 0)

    def test_delivery_failure_after_worker_success(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                self.check_outcome(tool, 0, 73)

    def test_delivery_failure_after_worker_failure(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                self.check_outcome(tool, 7, 73)

    def test_recovery_retries_only_delivery_and_preserves_evidence(self):
        for tool in ("claude", "codex"):
            for worker_exit in (0, 7):
                with self.subTest(tool=tool, worker_exit=worker_exit):
                    fixture = self.fixture(tool, worker_exit, 73)
                    # Relative configuration still produces recovery usable elsewhere.
                    fixture.env["MAILBOX_SKILL_DIR"] = fixture.mailbox.name
                    fixture.env["SPAWN_ARTIFACT_ROOT"] = fixture.artifacts.name
                    result = self.run_spawn(fixture)
                    self.assertEqual(result.returncode, 73, result.stderr)
                    capture, = self.captures(fixture)
                    evidence = {p.name: p.read_bytes() for p in capture.iterdir()}
                    command = result.stderr.splitlines()[-1].strip()
                    self.assertEqual(shlex.split(command), ["bash", str(capture / "retry.sh")])
                    recovery_cwd = fixture.root / "elsewhere"
                    recovery_cwd.mkdir()
                    for expected_attempts, delivery_exit in ((2, 73), (3, 0)):
                        fixture.env["SPAWN_TEST_DELIVERY_EXIT"] = str(delivery_exit)
                        recovered = subprocess.run(
                            [str(fixture.bin / "bash"), "-c", command], env=fixture.env,
                            cwd=recovery_cwd, capture_output=True, text=True, timeout=10,
                        )
                        self.assertEqual(recovered.returncode, delivery_exit, recovered.stderr)
                        self.assertEqual((fixture.root / "workers.txt").read_text(), "run\n")
                        senders = (fixture.root / "deliveries.txt").read_text().splitlines()
                        self.assertEqual(len(senders), expected_attempts)
                        self.assertEqual(len(set(senders)), 1)
                        self.assertEqual({p.name: p.read_bytes() for p in capture.iterdir()}, evidence)
                    self.assertEqual((fixture.root / "delivered.txt").read_bytes(), evidence["envelope.txt"])
                    self.assertFalse((fixture.root / "must-not-exist").exists())

    def test_missing_cli_stops_before_transport_or_capture(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                fixture = self.fixture(tool)
                (fixture.bin / tool).unlink()
                result = self.run_spawn(fixture)
                self.assertEqual(result.returncode, 127, result.stderr)
                self.assertFalse((fixture.root / "workers.txt").exists())
                self.assertFalse((fixture.root / "deliveries.txt").exists())
                self.assertEqual(self.captures(fixture), [])

    def test_invalid_transport_stops_before_worker(self):
        for failure in ("missing", "directory", "syntax"):
            with self.subTest(failure=failure):
                fixture = self.fixture()
                fixture.mail_send.unlink()
                if failure == "directory":
                    fixture.mail_send.mkdir()
                elif failure == "syntax":
                    fixture.mail_send.write_text("if then\n")
                result = self.run_spawn(fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((fixture.root / "workers.txt").exists())
                self.assertFalse((fixture.root / "deliveries.txt").exists())
                self.assertEqual(self.captures(fixture), [])

    def test_unavailable_capture_root_stops_before_worker(self):
        fixture = self.fixture()
        fixture.artifacts.write_text("a file cannot hold captures")
        result = self.run_spawn(fixture)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((fixture.root / "workers.txt").exists())
        self.assertFalse((fixture.root / "deliveries.txt").exists())

    def test_codex_stdout_fallback(self):
        fixture = self.fixture(delivery_exit=73)
        fixture.env["SPAWN_TEST_NO_MSG"] = "1"
        result = self.run_spawn(fixture)
        self.assertEqual(result.returncode, 73, result.stderr)
        capture, = self.captures(fixture)
        self.assertEqual((capture / "out.txt").read_text(), ANSWER)
        self.assertIn(ANSWER.rstrip("\n"), (capture / "envelope.txt").read_text())


if __name__ == "__main__":
    unittest.main()
