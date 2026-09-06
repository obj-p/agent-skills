#!/usr/bin/env python3
"""Mailbox regressions with temporary storage; never use the live mailbox.

Run: python3 -B -m unittest discover -s tests -p 'test_mailbox.py'
"""

import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_spawn


SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/mailbox/scripts"
SPEC = importlib.util.spec_from_file_location("mailbox_transport", SCRIPTS / "mailbox.py")
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)
BODY = "THREAD: fixture\nVERB: FYI\n\nUnicode café\n$(touch must-not-exist)\n\nlast line\n\n"
PAUSED_CONSUMER = r'''
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("transport", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ready, release = map(pathlib.Path, sys.argv[2:4])
original = module.Mailbox.output
def output(record):
    original(record)
    ready.touch()
    while not release.exists():
        time.sleep(0.01)
module.Mailbox.output = staticmethod(output)
raise SystemExit(module.main(sys.argv[4:]))
'''


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-skills-mailbox-test-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.root = self.work / "mailboxes 'quoted' $literal"
        self.bin = self.work / "bin"
        self.bin.mkdir()
        # A closed PATH cannot invoke an installed worker CLI or notifier.
        for name in ("bash", "dirname"):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = dict(os.environ)
        for key in ("MAILBOX_FROM", "MAILBOX_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
                    "MAILBOX_NOTIFY", "BASH_ENV", "ENV", "PYTHONPATH", "PYTHONHOME"):
            self.env.pop(key, None)
        self.env.update(PATH=str(self.bin), AGENT_MAILBOX_ROOT=str(self.root),
                        MAILBOX_FROM="fixture-alice", PYTHONDONTWRITEBYTECODE="1")
        self.mailbox = transport.Mailbox(self.root)

    def run_mail(self, *args, script="mail.sh", input=None, env=None):
        return subprocess.run(
            [str(self.bin / "bash"), str(SCRIPTS / script), *args],
            input=input, env=env or self.env, cwd=self.work, text=True,
            capture_output=True, timeout=10,
        )

    def send(self, body=BODY, recipient="fixture-bob"):
        result = self.run_mail("send", input=recipient + " " + body)
        self.assertEqual(result.returncode, 0, result.stderr)
        return next(path for path in (self.root / recipient / "inbox").glob("*.txt")
                    if path.read_text() == body)

    def files(self, state, recipient="fixture-bob"):
        return list((self.root / recipient / state).glob("*.txt"))

    def start(self, argv):
        process = subprocess.Popen(
            argv, env=self.env, cwd=self.work, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        return process

    def paused(self, command="read"):
        ready, release = self.work / "ready", self.work / "release"
        args = [command, "fixture-bob"] + (["0"] if command == "monitor" else [])
        process = self.start([
            sys.executable, "-B", "-c", PAUSED_CONSUMER, str(SCRIPTS / "mailbox.py"),
            str(ready), str(release), *args,
        ])
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "consumer did not reach the output-before-ack barrier")
        return process, release

    def test_roundtrip_preserves_multiline_body_and_legacy_header(self):
        message = self.send()
        result = self.run_mail("read", "fixture-bob")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.splitlines()[0], r"^--- from fixture-alice at [0-9TZ]+ ---$")
        self.assertTrue(result.stdout.endswith(BODY + "\n"))
        self.assertEqual(self.files("inbox"), [])
        self.assertEqual(self.files("read")[0].read_text(), BODY)
        self.assertEqual(self.files("read")[0].name, message.name)
        self.assertIn("no new mail", self.run_mail("read", "fixture-bob").stdout)
        self.assertFalse((self.work / "must-not-exist").exists())
        legacy = self.root / "fixture-bob/inbox/20260905T120000Z-old-sender_1-12345.txt"
        legacy.write_text("legacy message")
        self.assertIn("--- from old-sender_1 at 20260905T120000Z ---", self.run_mail("read", "fixture-bob").stdout)

    def test_review_malformed_filenames_do_not_block_or_count_as_delivery(self):
        for command in ("read", "wait", "monitor", "watch"):
            with self.subTest(command=command):
                recipient = "fixture-" + command
                inbox = self.root / recipient / "inbox"
                inbox.mkdir(parents=True)
                invalid = [inbox / name for name in (
                    "000-note.txt", "note.txt", "20260905T120000Z-..-1.txt",
                    "20260905T120000Z-alice-.txt",
                )]
                for path in invalid:
                    path.write_text("keep this invalid file for inspection")
                directory = inbox / "20260905T120000Z-alice-2.txt"
                directory.mkdir()
                self.send(recipient=recipient)
                if command == "read":
                    result = self.run_mail("read", recipient)
                elif command == "wait":
                    result = self.run_mail("wait", recipient, "0")
                else:
                    result = self.run_mail(recipient, "0", script=f"mail-{command}.sh")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(BODY, result.stdout)
                self.assertNotIn("keep this invalid file", result.stdout)
                self.assertEqual(self.files("inflight", recipient), [])
                for path in invalid:
                    self.assertIn(path.name, result.stderr)
                    self.assertEqual(path.read_text(), "keep this invalid file for inspection")
                self.assertTrue(directory.is_dir())
                if command in ("monitor", "watch"):
                    self.assertEqual(self.run_mail("read", recipient).returncode, 0)
                self.assertEqual(self.run_mail("wait", recipient, "0").returncode, 124)

    def test_review_recovered_malformed_claim_does_not_repeat_the_failure(self):
        self.mailbox.prepare("fixture-bob")
        bad_claim = self.root / "fixture-bob/inflight/note.txt"
        bad_claim.write_text("retain malformed claim")
        self.assertEqual(self.run_mail("recover", "fixture-bob").returncode, 0)
        pending = self.root / "fixture-bob/pending/pending-note.txt"
        pending.write_text("retain malformed pending message")
        for _ in range(2):
            self.send()
            result = self.run_mail("read", "fixture-bob")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(BODY, result.stdout)
            self.assertIn("note.txt", result.stderr)
            self.assertIn("pending-note.txt", result.stderr)
            self.assertEqual(self.files("inflight"), [])
        self.assertEqual((self.root / "fixture-bob/inbox/note.txt").read_text(), "retain malformed claim")
        self.assertEqual(pending.read_text(), "retain malformed pending message")

    def test_review_option_like_names_work_across_mailbox_commands(self):
        self.send(recipient="untouched")
        for name in ("-reviewer", "-h", "--help", "-"):
            with self.subTest(name=name):
                registered = self.run_mail("iam", name)
                self.assertEqual(registered.returncode, 0, registered.stderr)
                self.assertTrue((self.root / name / "inbox").is_dir())
                for script, prefix, suffix in (
                    ("mail.sh", ("read",), ()), ("mail.sh", ("wait",), ("0",)),
                    ("mail-monitor.sh", (), ("0",)), ("mail-watch.sh", (), ("0",)),
                ):
                    self.send(recipient=name)
                    result = self.run_mail(*prefix, name, *suffix, script=script)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(BODY, result.stdout)
                    if not prefix:
                        self.assertEqual(self.run_mail("read", name).returncode, 0)
                message = self.send(recipient=name)
                message.rename(self.root / name / "inflight" / message.name)
                recovered = self.run_mail("recover", name)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertIn(BODY, self.run_mail("read", name).stdout)
                self.assertEqual(self.run_mail("clean", name).returncode, 0)
                self.assertFalse((self.root / name).exists())
                self.assertEqual(len(self.files("inbox", "untouched")), 1)

    def test_review_name_options_accept_hyphens_and_literal_flag_names(self):
        for name in ("-h", "--name", "--seconds", "--"):
            with self.subTest(name=name):
                self.send(recipient=name)
                explicit = self.run_mail("wait", "--name", name, "--seconds", "0")
                self.assertEqual(explicit.returncode, 0, explicit.stderr)
                self.assertIn(BODY, explicit.stdout)
                self.send(recipient=name)
                literal = self.run_mail("wait", "--", name, "0")
                self.assertEqual(literal.returncode, 0, literal.stderr)
                self.assertIn(BODY, literal.stdout)
        help_result = self.run_mail("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("usage:", help_result.stdout)

    def test_review_interactive_send_without_payload_exits_before_eof(self):
        master, slave = os.openpty()
        process = subprocess.Popen(
            [str(self.bin / "bash"), str(SCRIPTS / "mail.sh"), "send"],
            env=self.env, cwd=self.work, stdin=slave,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        os.close(slave)
        try:
            out, err = process.communicate(timeout=2)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            os.close(master)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("recipient and message required", err)
        self.assertNotIn("sent to", out)
        self.assertFalse(self.root.exists())

    def test_slow_publication_is_invisible_until_complete(self):
        original_open = Path.open
        test = self
        class SlowWrite:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, body):
                halfway = len(body) // 2
                self.stream.write(body[:halfway])
                self.stream.flush()
                test.assertEqual(test.files("inbox"), [])
                observed = test.run_mail("read", "fixture-bob")
                test.assertEqual(observed.returncode, 0, observed.stderr)
                test.assertIn("no new mail", observed.stdout)
                self.stream.write(body[halfway:])
        def slow_open(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return SlowWrite(stream) if path.parent.name == ".staging" and mode == "xb" else stream
        with patch.object(Path, "open", slow_open):
            self.mailbox.publish("fixture-alice", "fixture-bob", BODY.encode())
        self.assertIn(BODY, self.run_mail("read", "fixture-bob").stdout)

    def test_failed_publication_never_reports_or_exposes_delivery(self):
        with patch.object(transport.os, "replace", side_effect=OSError("simulated publication failure")):
            with self.assertRaises(OSError):
                self.mailbox.publish("fixture-alice", "fixture-bob", BODY.encode())
        self.assertEqual(self.files("inbox"), [])
        self.assertEqual(list((self.root / "fixture-bob/.staging").iterdir()), [])
        self.root.rename(self.work / "previous-root")
        self.root.write_text("not a directory")
        result = self.run_mail("send", input="fixture-bob " + BODY)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sent to", result.stdout)

    def test_send_argument_body_is_opaque_even_when_it_looks_like_options(self):
        result = self.run_mail("send", "fixture-bob", "--help", "-n", "literal body")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sent to", result.stdout)
        self.assertEqual(self.files("inbox")[0].read_text(), "--help -n literal body")

    def test_message_ids_do_not_collide_under_parallel_senders(self):
        senders = [self.start([str(self.bin / "bash"), str(SCRIPTS / "mail.sh"),
                               "send", "fixture-bob", f"message-{index}"])
                   for index in range(20)]
        for process in senders:
            _out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, err)
        messages = self.files("inbox")
        self.assertEqual(len(messages), 20)
        self.assertEqual({path.read_text() for path in messages}, {f"message-{i}" for i in range(20)})

    def test_competing_readers_deliver_one_copy(self):
        self.send()
        first, release = self.paused()
        second = self.start([str(self.bin / "bash"), str(SCRIPTS / "mail.sh"), "read", "fixture-bob"])
        time.sleep(0.1)
        self.assertIsNone(second.poll(), "second reader should wait for the active claim")
        release.touch()
        outputs = [process.communicate(timeout=5) for process in (first, second)]
        self.assertEqual([first.returncode, second.returncode], [0, 0], outputs)
        self.assertEqual(sum(out.count(BODY) for out, err in outputs), 1)
        self.assertEqual(len(self.files("read")), 1)

    def test_wait_does_not_claim_success_when_another_reader_owns_mail(self):
        self.send()
        first, release = self.paused()
        result = self.run_mail("wait", "fixture-bob", "0")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertNotIn(BODY, result.stdout)
        release.touch()
        first.communicate(timeout=5)

    def test_monitor_observes_without_acknowledging_and_watch_is_compatible(self):
        empty = self.run_mail("fixture-bob", "0", script="mail-monitor.sh")
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertEqual((self.root / "fixture-bob/events.log").read_bytes(), b"")
        self.send()
        first = self.run_mail("fixture-bob", "0", script="mail-watch.sh")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(BODY, first.stdout)
        self.assertEqual(len(self.files("pending")), 1)
        self.assertEqual(self.files("read"), [])
        second = self.run_mail("fixture-bob", "0", script="mail-monitor.sh")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn(BODY, second.stdout)
        log = (self.root / "fixture-bob/events.log").read_text()
        self.assertEqual(log.count(BODY), 1)
        self.assertIn(" file " + self.files("pending")[0].name + " ---", log)
        read = self.run_mail("wait", "fixture-bob", "0")
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn(BODY, read.stdout)
        self.assertEqual(len(self.files("read")), 1)

    def test_competing_monitors_observe_once(self):
        self.send()
        first, release = self.paused("monitor")
        second = self.start([str(self.bin / "bash"), str(SCRIPTS / "mail-monitor.sh"), "fixture-bob", "1"])
        release.touch()
        outputs = [process.communicate(timeout=5) for process in (first, second)]
        self.assertEqual([first.returncode, second.returncode], [0, 0], outputs)
        self.assertEqual(sum(out.count(BODY) for out, err in outputs), 1)
        self.assertEqual((self.root / "fixture-bob/events.log").read_text().count(BODY), 1)

    def test_monitor_does_not_reobserve_mail_claimed_by_a_reader(self):
        self.send()
        reader, release = self.paused()
        monitor = self.start([str(self.bin / "bash"), str(SCRIPTS / "mail-monitor.sh"), "fixture-bob", "1"])
        release.touch()
        read_out, read_err = reader.communicate(timeout=5)
        monitor_out, monitor_err = monitor.communicate(timeout=5)
        self.assertEqual([reader.returncode, monitor.returncode], [0, 0], (read_err, monitor_err))
        self.assertEqual(read_out.count(BODY), 1)
        self.assertNotIn(BODY, monitor_out)
        self.assertEqual(self.files("pending"), [])

    def test_failed_event_logging_does_not_acknowledge(self):
        self.send()
        log = self.root / "fixture-bob/events.log"
        log.mkdir()
        result = self.run_mail("fixture-bob", "0", script="mail-monitor.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(BODY, result.stdout)
        self.assertEqual(self.files("pending"), [])
        self.assertEqual(self.files("read"), [])
        self.assertEqual(self.files("inbox")[0].read_text(), BODY)
        # A failure after initialization also preserves the current claim.
        with self.assertRaises(OSError):
            self.mailbox.consume("fixture-bob", observe=True)
        self.assertEqual(self.files("inflight")[0].read_text(), BODY)
        log.rmdir()
        self.assertEqual(self.run_mail("recover", "fixture-bob").returncode, 0)
        self.assertIn(BODY, self.run_mail("fixture-bob", "0", script="mail-monitor.sh").stdout)

    def test_reader_can_receive_mail_after_a_competing_monitor_observes_it(self):
        self.send()
        monitor, release = self.paused("monitor")
        reader = self.start([str(self.bin / "bash"), str(SCRIPTS / "mail.sh"), "read", "fixture-bob"])
        release.touch()
        observed, monitor_err = monitor.communicate(timeout=5)
        delivered, reader_err = reader.communicate(timeout=5)
        self.assertEqual([monitor.returncode, reader.returncode], [0, 0], (monitor_err, reader_err))
        self.assertEqual(observed.count(BODY), 1)
        self.assertEqual(delivered.count(BODY), 1)
        self.assertEqual(len(self.files("read")), 1)

    def test_crash_after_output_retains_claim_and_explicit_recovery_replays_it(self):
        for mode in ("read", "monitor"):
            with self.subTest(mode=mode):
                message = self.send()
                first, _release = self.paused(mode)
                busy = self.run_mail("recover", "fixture-bob")
                self.assertNotEqual(busy.returncode, 0)
                self.assertIn("busy", busy.stderr)
                os.killpg(first.pid, signal.SIGKILL)
                first.communicate(timeout=5)
                claim, = self.files("inflight")
                self.assertEqual(claim.read_text(), BODY)
                self.assertEqual(claim.name, message.name)
                blocked = self.run_mail("read", "fixture-bob")
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("recover", blocked.stderr)
                recovered = self.run_mail("recover", "fixture-bob")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertIn("recovered 1", recovered.stdout)
                self.assertEqual(self.files("inflight"), [])
                self.assertIn(BODY, self.run_mail("read", "fixture-bob").stdout)
                self.assertIn("no new mail", self.run_mail("read", "fixture-bob").stdout)
                (self.work / "ready").unlink()

    def test_broken_output_does_not_acknowledge(self):
        body = "large-message-" * 100000
        self.send(body)
        process = self.start([str(self.bin / "bash"), str(SCRIPTS / "mail.sh"), "read", "fixture-bob"])
        process.stdout.close()
        process.stdout = None
        _out, err = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("recover", err)
        self.assertEqual(self.files("read"), [])
        self.assertEqual(self.files("inflight")[0].read_text(), body)

    def test_numeric_addresses_and_timeout_shorthand_share_one_parser(self):
        for script, prefix in (("mail.sh", ("wait",)), ("mail-monitor.sh", ()), ("mail-watch.sh", ())):
            for options in (("123", "0"), ("--name", "123", "--seconds", "0")):
                with self.subTest(script=script, options=options):
                    self.send(recipient="123")
                    result = self.run_mail(*prefix, *options, script=script)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(BODY, result.stdout)
                    self.run_mail("read", "123")
            env = {**self.env, "MAILBOX_FROM": "fixture-bob"}
            result = self.run_mail(*prefix, "00", script=script, env=env)
            self.assertEqual(result.returncode, 124 if prefix else 0, result.stderr)
            for invalid in ("-1", "1.5", "bogus", "99999999999999999999"):
                result = self.run_mail(*prefix, "fixture-bob", invalid, script=script)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(transport.parse_args(["wait", "bob", "008"]).seconds, 8)
        self.assertEqual(transport.parse_args(["monitor", "123", "--seconds", "0"]).name, "123")

    def test_wait_timeout_and_arrival(self):
        start = time.monotonic()
        result = self.run_mail("wait", "fixture-bob", "1")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertLess(time.monotonic() - start, 2)
        waiter = self.start([str(self.bin / "bash"), str(SCRIPTS / "mail.sh"), "wait", "fixture-bob", "3"])
        self.send()
        out, err = waiter.communicate(timeout=5)
        self.assertEqual(waiter.returncode, 0, err)
        self.assertIn(BODY, out)

    def test_identity_precedence_and_root_isolation(self):
        env = dict(self.env, MAILBOX_SESSION_ID="session-fixture")
        env.pop("MAILBOX_FROM")
        self.assertEqual(self.run_mail("iam", "registered", env=env).returncode, 0)
        self.send(recipient="registered")
        self.assertIn(BODY, self.run_mail("read", env=env).stdout)
        env["MAILBOX_FROM"] = "environment"
        self.send(recipient="environment")
        self.assertIn(BODY, self.run_mail("read", env=env).stdout)
        self.send()
        self.assertIn(BODY, self.run_mail("read", "fixture-bob", env=env).stdout)
        for name in ("../escape", ".", "..", "invalid/name"):
            self.assertNotEqual(self.run_mail("read", name).returncode, 0)
        result = self.run_mail("read", "fixture-bob", env={**self.env, "AGENT_MAILBOX_ROOT": "relative-root"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "relative-root").exists())
        with patch.dict(os.environ):
            os.environ.pop("AGENT_MAILBOX_ROOT", None)
            self.assertEqual(transport.Mailbox().root, Path.home() / ".agents/mailbox")

    def test_spawn_recovery_with_real_mailbox_transport(self):
        for tool in ("claude", "codex"):
            for worker_exit in (0, 7):
                with self.subTest(tool=tool, worker_exit=worker_exit):
                    fixture = test_spawn.SpawnTests.fixture(self, tool, worker_exit)
                    (fixture.bin / "python3").symlink_to(sys.executable)
                    mailbox_root = fixture.root / "isolated-mail"
                    mailbox_root.write_text("force transport failure")
                    fixture.env.update(AGENT_MAILBOX_ROOT=str(mailbox_root), MAILBOX_SKILL_DIR=str(SCRIPTS.parent))
                    result = test_spawn.SpawnTests.run_spawn(self, fixture)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("mailed", result.stdout)
                    capture, = fixture.artifacts.iterdir()
                    evidence = {path.name: path.read_bytes() for path in capture.iterdir()}
                    mailbox_root.unlink()
                    retry = subprocess.run(
                        [str(fixture.bin / "bash"), str(capture / "retry.sh")], env=fixture.env,
                        cwd=self.work, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(retry.returncode, 0, retry.stderr)
                    messages = list((mailbox_root / "fixture-parent/inbox").glob("*.txt"))
                    self.assertEqual(len(messages), 1)
                    self.assertIn(test_spawn.ANSWER.rstrip("\n"), messages[0].read_text())
                    self.assertIn("VERB: " + ("BLOCKED" if worker_exit else "DONE"), messages[0].read_text())
                    self.assertEqual((fixture.root / "workers.txt").read_text(), "run\n")
                    self.assertEqual({p.name: p.read_bytes() for p in capture.iterdir()}, evidence)


if __name__ == "__main__":
    unittest.main()
