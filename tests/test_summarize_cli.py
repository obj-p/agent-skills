#!/usr/bin/env python3
"""Deterministic summarizer regressions; model/server boundaries are mocked.

Run: python3 -B -m unittest discover -s tests -p 'test_summarize_cli.py'
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import Mock, patch
import urllib.error


SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/summarize-cli/scripts/summarize_cli.py'
SPEC = importlib.util.spec_from_file_location('summarize_cli', SCRIPT)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-skills-summarize-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifacts = self.root / 'artifacts'
        self.requests = []

    def response(self, url, payload=None, timeout=120):
        if payload is None:
            return {'data': [{'id': 'stub-embedding'}, {'id': 'stub-chat'}]}
        self.requests.append(payload)
        return {'choices': [{'message': {'content': 'A concise summary.'}, 'finish_reason': 'stop'}]}

    def invoke(self, code='pass', options=(), response=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        args = [
            '--instruction', 'Summarize failures', '--artifact-root', str(self.artifacts),
            '--cwd', str(self.root), '--model', 'stub-chat', *options,
            '--', sys.executable, '-B', '-c', code,
        ]
        with patch.object(helper, 'request_json', side_effect=response or self.response), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = helper.main(args)
        records = [json.loads(line.removeprefix('summarize-cli: '))
                   for line in stderr.getvalue().splitlines() if line.startswith('summarize-cli: ')]
        self.assertTrue(records, stderr.getvalue())
        final = records[-1]
        self.assertEqual(final['wrapper_exit'], status)
        if final['artifact_dir'] and final.get('metadata_status') == 'saved':
            directory = Path(final['artifact_dir'])
            self.assertEqual(json.loads((directory / 'metadata.json').read_text()), final)
        return status, stdout.getvalue(), final

    def prompt_facts(self):
        prompt = self.requests[-1]['messages'][1]['content']
        return json.loads(prompt.split('Command facts from the wrapper:\n', 1)[1].split('\n\n', 1)[0])

    def test_nonzero_status_is_preserved_and_facts_are_not_model_prose(self):
        status, summary, facts = self.invoke('print("worker log"); raise SystemExit(7)')
        self.assertEqual(status, 7)
        self.assertEqual(summary, 'A concise summary.\n')
        self.assertEqual(facts['command_exit'], 7)
        self.assertEqual(facts['model'], 'stub-chat')
        self.assertFalse(facts['timed_out'])
        self.assertFalse(facts['truncated'])
        self.assertEqual(facts['summary_status'], 'ok')
        self.assertEqual(self.prompt_facts()['command_exit'], 7)

    def test_exit_code_compatibility_flags(self):
        for flags, expected in [((), 7), (('--preserve-exit-code',), 7), (('--ignore-command-exit-code',), 0)]:
            with self.subTest(flags=flags):
                status, _, facts = self.invoke('raise SystemExit(7)', flags)
                self.assertEqual(status, expected)
                self.assertEqual(facts['command_exit'], 7)

    def test_summary_failure_preserves_command_outcome_and_raw_evidence(self):
        def response(url, payload=None, timeout=120):
            if payload is not None:
                raise RuntimeError('Inference unavailable')
            return self.response(url, payload, timeout)
        for command_status, flags, expected in [(0, (), 2), (7, (), 7), (7, ('--ignore-command-exit-code',), 2)]:
            with self.subTest(command_status=command_status, flags=flags):
                status, summary, facts = self.invoke(
                    f'print("saved evidence"); raise SystemExit({command_status})', flags, response,
                )
                self.assertEqual(status, expected)
                self.assertEqual(summary, '')
                self.assertEqual(facts['command_exit'], command_status)
                self.assertEqual(facts['summary_status'], 'error')
                directory = Path(facts['artifact_dir'])
                self.assertEqual((directory / 'stdout.log').read_bytes(), b'saved evidence\n')
                self.assertIn('saved evidence', (directory / 'model-input.txt').read_text())
                self.assertFalse((directory / 'summary.txt').exists())

    def test_server_failure_does_not_run_command(self):
        def unreachable(*args, **kwargs):
            raise RuntimeError('Server unavailable')
        status, _, facts = self.invoke('from pathlib import Path; Path("ran").touch()', response=unreachable)
        self.assertEqual(status, 2)
        self.assertIsNone(facts['command_exit'])
        self.assertFalse((self.root / 'ran').exists())
        self.assertFalse(self.artifacts.exists())

    def test_unlisted_explicit_model_does_not_run_command(self):
        status, _, facts = self.invoke('from pathlib import Path; Path("ran").touch()', ['--model', 'not-listed'])
        self.assertEqual(status, 2)
        self.assertIn('not listed', facts['error'])
        self.assertFalse((self.root / 'ran').exists())

    def test_timeout_preserves_output_even_if_inference_fails(self):
        def response(url, payload=None, timeout=120):
            if payload is not None:
                raise RuntimeError('Inference unavailable')
            return self.response(url, payload, timeout)
        status, _, facts = self.invoke('import time; print("before timeout", flush=True); time.sleep(60)', ['--timeout', '1'], response)
        self.assertEqual(status, 124)
        self.assertEqual(facts['command_exit'], 124)
        self.assertTrue(facts['timed_out'])
        self.assertEqual((Path(facts['artifact_dir']) / 'stdout.log').read_bytes(), b'before timeout\n')

    @unittest.skipUnless(os.name == 'posix', 'POSIX process groups')
    def test_timeout_kills_term_ignoring_descendant_after_parent_exits(self):
        descendant = 'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(os.getpid(), flush=True)\nfor _ in range(80):\n os.write(1, b"heartbeat\\n"); time.sleep(0.1)'
        code = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {descendant!r}]); time.sleep(60)'
        status, _, facts = self.invoke(code, ['--timeout', '1'])
        self.assertEqual(status, 124)
        log = Path(facts['artifact_dir']) / 'stdout.log'
        self.assertGreater(int(log.read_text().splitlines()[0]), 0)
        # The fixture has a bounded lifetime; never signal a potentially
        # recycled grandchild PID from test teardown.
        captured = log.read_bytes()
        self.assertIn(b'heartbeat', captured)
        time.sleep(0.3)
        self.assertEqual(log.read_bytes(), captured, 'descendant is still writing after timeout')

    @unittest.skipUnless(os.name == 'posix', 'POSIX signals')
    def test_signal_exit_is_normalized(self):
        status, _, facts = self.invoke('import os, signal; os.kill(os.getpid(), signal.SIGTERM)')
        self.assertEqual(status, 128 + signal.SIGTERM)
        self.assertEqual(facts['command_exit'], status)

    def test_unicode_and_invalid_bytes_preserved_in_raw_files(self):
        out = 'café 日本語 😀\n'.encode() + b'\xff\x00\n'
        err = 'error: naïve résumé\n'.encode()
        status, _, facts = self.invoke(f'import os; os.write(1, {out!r}); os.write(2, {err!r})')
        self.assertEqual(status, 0)
        directory = Path(facts['artifact_dir'])
        self.assertEqual((directory / 'stdout.log').read_bytes(), out)
        self.assertEqual((directory / 'stderr.log').read_bytes(), err)
        evidence = (directory / 'model-input.txt').read_text()
        self.assertIn('café 日本語 😀', evidence)
        self.assertIn('error: naïve résumé', evidence)
        self.assertIn('\ufffd', evidence)
        self.assertFalse(facts['truncated'])

    def test_large_mixed_streams_keep_start_middle_end_diagnostics(self):
        markers = []
        for name in ('stdout', 'stderr'):
            content = []
            for position in ('start', 'middle', 'end'):
                marker = f'ERROR {name}_{position}_unique'
                markers.append(marker)
                content.extend([marker + '\n', 'ordinary progress café\n' * 16000])
            (self.root / f'{name}.fixture').write_text(''.join(content), encoding='utf-8')
        code = 'import shutil, sys; shutil.copyfileobj(open("stdout.fixture", "rb"), sys.stdout.buffer); shutil.copyfileobj(open("stderr.fixture", "rb"), sys.stderr.buffer)'
        status, _, facts = self.invoke(code)
        self.assertEqual(status, 0)
        self.assertTrue(facts['truncated'])
        directory = Path(facts['artifact_dir'])
        evidence = (directory / 'model-input.txt').read_text()
        self.assertLessEqual(len(evidence), 12000)
        for marker in markers:
            self.assertIn(marker, evidence)
        for name in ('stdout', 'stderr'):
            self.assertEqual((directory / f'{name}.log').read_bytes(), (self.root / f'{name}.fixture').read_bytes())
            coverage = facts['coverage'][name]
            self.assertLess(coverage['selected_bytes'], coverage['total_bytes'])
            self.assertGreaterEqual(coverage['diagnostic_matches'], 3)
            self.assertEqual(coverage['selected_bytes'], helper.covered_bytes(coverage['ranges']))
        self.assertIn(evidence, self.requests[0]['messages'][1]['content'])

    def test_bounded_input_for_tiny_budgets_and_dense_single_line_output(self):
        for budget in (1, 40, 200, 1024):
            with self.subTest(budget=budget):
                _, _, facts = self.invoke('import os; os.write(1, b"error dense " * 10000); os.write(2, b"warning dense " * 10000)', ['--max-output-chars', str(budget)])
                self.assertLessEqual(facts['model_input_chars'], budget)
                self.assertTrue(facts['truncated'])
                for coverage in facts['coverage'].values():
                    self.assertLessEqual(coverage['diagnostic_windows'], 8)

    def test_large_capture_has_bounded_python_memory(self):
        code = 'import os; chunk = b"ordinary output\\n" * 4096\nfor _ in range(256): os.write(1, chunk)'
        tracemalloc.start()
        try:
            _, _, facts = self.invoke(code)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        directory = Path(facts['artifact_dir'])
        self.assertEqual((directory / 'stdout.log').stat().st_size, 16 * 4096 * 256)
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertLessEqual(facts['model_input_chars'], 12000)

    def test_diagnostic_crossing_scan_chunk_boundary_is_retained(self):
        fixture = self.root / 'boundary.fixture'
        fixture.write_bytes(b'x' * 8187 + b'\nERROR boundary_failure\n' + b'x' * 50000)
        _, _, facts = self.invoke('import shutil, sys; shutil.copyfileobj(open("boundary.fixture", "rb"), sys.stdout.buffer)', ['--max-output-chars', '1000'])
        evidence = (Path(facts['artifact_dir']) / 'model-input.txt').read_text()
        self.assertIn('ERROR boundary_failure', evidence)

    def test_nonkeyword_middle_evidence_is_sampled(self):
        marker = 'UNIQUE_DIAGNOSTIC_WITHOUT_KEYWORDS'
        code = f'import sys; sys.stdout.write("ordinary\\n" * 20000 + {marker!r} + "ordinary\\n" * 20000)'
        _, _, facts = self.invoke(code)
        evidence = (Path(facts['artifact_dir']) / 'model-input.txt').read_text()
        self.assertIn(marker, evidence)
        self.assertTrue(facts['truncated'])

    def test_artifact_runs_are_unique_private_and_retained(self):
        directories = []
        for _ in range(2):
            _, _, facts = self.invoke()
            directory = Path(facts['artifact_dir'])
            directories.append(directory)
            self.assertEqual((directory / 'summary.txt').read_text(), 'A concise summary.\n')
            if os.name == 'posix':
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertNotEqual(*directories)
        self.assertTrue(all(path.is_dir() for path in directories))

    def test_unwritable_artifact_root_stops_before_command(self):
        self.artifacts.write_text('regular file')
        status, _, facts = self.invoke('from pathlib import Path; Path("ran").touch()')
        self.assertEqual(status, 2)
        self.assertIsNone(facts['command_exit'])
        self.assertFalse((self.root / 'ran').exists())

    def test_command_launch_failure_retains_metadata(self):
        with patch.object(helper.subprocess, 'Popen', side_effect=FileNotFoundError('missing executable')):
            status, _, facts = self.invoke()
        self.assertEqual(status, 2)
        self.assertIsNone(facts['command_exit'])
        self.assertIn('Could not start command', facts['error'])
        self.assertTrue((Path(facts['artifact_dir']) / 'stdout.log').exists())

    def test_model_selection_and_malformed_responses(self):
        with patch.object(helper, 'request_json', side_effect=self.response):
            self.assertEqual(helper.resolve_model('http://stub/v1', None), 'stub-chat')
        for result in ({'data': []}, {'data': [{'id': 'only-embedding'}]}, {}, {'data': None}, {'data': ['bad']}, []):
            with self.subTest(result=result), patch.object(helper, 'request_json', return_value=result):
                with self.assertRaises(RuntimeError):
                    helper.resolve_model('http://stub/v1', None)

    def test_empty_and_malformed_summary_are_errors(self):
        for result in ({}, {'choices': []}, {'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}]}, {'choices': [{'message': {'content': ['invalid']}}]}, {'choices': [{'message': []}]}, {'choices': [{'message': {'content': '', 'reasoning_content': []}}]}):
            def response(url, payload=None, timeout=120):
                return result if payload is not None else self.response(url, payload, timeout)
            with self.subTest(result=result):
                status, summary, facts = self.invoke(response=response)
                self.assertEqual(status, 2)
                self.assertEqual(summary, '')
                self.assertEqual(facts['command_exit'], 0)
                self.assertEqual(facts['summary_status'], 'error')

    def test_network_and_json_errors_have_explicit_failures(self):
        for failure in (urllib.error.URLError('offline'), TimeoutError('timed out')):
            with self.subTest(failure=failure), patch.object(helper.urllib.request, 'urlopen', side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, 'Could not reach'):
                    helper.request_json('http://stub/v1/models')
        with patch.object(helper.urllib.request, 'urlopen', return_value=io.BytesIO(b'not json')):
            with self.assertRaisesRegex(RuntimeError, 'invalid JSON'):
                helper.request_json('http://stub/v1/models')

    def test_review_metadata_failure_keeps_summary_and_original_error(self):
        replace = Path.replace
        def fail_final(path, target):
            if path.name == 'metadata.tmp' and json.loads(path.read_text())['phase'] == 'finished':
                raise OSError('injected metadata failure')
            return replace(path, target)
        for command_exit, inference_fails in ((0, False), (7, False), (0, True), (7, True)):
            def response(url, payload=None, timeout=120):
                if payload is not None and inference_fails:
                    raise RuntimeError('original inference error')
                return self.response(url, payload, timeout)
            with self.subTest(command_exit=command_exit, inference_fails=inference_fails), patch.object(Path, 'replace', fail_final):
                status, summary, facts = self.invoke(f'raise SystemExit({command_exit})', response=response)
                self.assertEqual(status, command_exit or (2 if inference_fails else 0))
                self.assertEqual(summary, '' if inference_fails else 'A concise summary.\n')
                self.assertEqual(facts['summary_status'], 'error' if inference_fails else 'ok')
                self.assertEqual(facts['metadata_status'], 'stale_or_missing')
                self.assertIn('injected metadata failure', facts['metadata_errors'][-1]['error'])
                if inference_fails:
                    self.assertEqual(facts['error'], 'original inference error')
                directory = Path(facts['artifact_dir'])
                self.assertEqual(json.loads((directory / 'metadata.json').read_text())['phase'], 'summarizing')
                if not inference_fails:
                    self.assertEqual((directory / 'summary.txt').read_text(), summary)

    def test_review_early_metadata_failure_does_not_prevent_capture(self):
        replace = Path.replace
        def fail_first(path, target):
            if path.name == 'metadata.tmp' and json.loads(path.read_text())['phase'] == 'running':
                raise OSError('initial metadata failure')
            return replace(path, target)
        with patch.object(Path, 'replace', fail_first):
            status, _, facts = self.invoke('print("captured evidence")')
        self.assertEqual(status, 0)
        self.assertEqual(facts['metadata_status'], 'saved')
        self.assertEqual(len(facts['metadata_errors']), 1)
        self.assertEqual((Path(facts['artifact_dir']) / 'stdout.log').read_text(), 'captured evidence\n')

    def test_review_failed_summary_save_does_not_suppress_stdout(self):
        write = Path.write_text
        def fail_summary(path, *args, **kwargs):
            if path.name == 'summary.txt':
                raise OSError('summary artifact write failed')
            return write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', fail_summary):
            status, summary, facts = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(summary, 'A concise summary.\n')
        self.assertEqual(facts['summary_status'], 'ok')
        self.assertIn('write failed', facts['summary_artifact_error'])

    def test_review_timeout_facts_differ_from_command_exit_124(self):
        status, _, facts = self.invoke('import time; print("PASSED", flush=True); time.sleep(60)', ['--timeout', '1'])
        self.assertEqual(status, 124)
        self.assertTrue(self.prompt_facts()['timed_out'])
        self.assertEqual(self.prompt_facts()['timeout_seconds'], 1)
        self.assertFalse(self.prompt_facts()['interrupted'])
        self.assertFalse(facts['truncated'])
        status, _, _ = self.invoke('raise SystemExit(124)')
        self.assertEqual(status, 124)
        self.assertFalse(self.prompt_facts()['timed_out'])

    def test_review_interrupt_skips_inference_and_preserves_raw_output(self):
        def interrupt(process, timeout):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                logs = list(self.artifacts.glob('run-*/stdout.log'))
                if logs and b'READY' in logs[-1].read_bytes():
                    raise KeyboardInterrupt
                time.sleep(0.01)
            self.fail('command did not become ready')
        with patch.object(helper, 'wait_for_command', side_effect=interrupt):
            status, summary, facts = self.invoke('import time; print("READY", flush=True); time.sleep(60)', ['--ignore-command-exit-code'])
        self.assertEqual(status, 130)
        self.assertEqual(summary, '')
        self.assertTrue(facts['interrupted'])
        self.assertEqual(facts['command_exit'], 130)
        self.assertEqual(facts['summary_status'], 'interrupted')
        self.assertEqual(self.requests, [])
        directory = Path(facts['artifact_dir'])
        self.assertIn(b'READY', (directory / 'stdout.log').read_bytes())
        self.assertFalse((directory / 'summary.txt').exists())
        status, _, facts = self.invoke('raise SystemExit(130)')
        self.assertEqual(status, 130)
        self.assertFalse(facts['interrupted'])
        self.assertEqual(facts['summary_status'], 'ok')
        self.assertEqual(len(self.requests), 1)

    def test_review_interrupt_during_setup_or_inference_keeps_command_facts(self):
        for phase in ('setup', 'inference'):
            def response(url, payload=None, timeout=120):
                if (payload is None) == (phase == 'setup'):
                    raise KeyboardInterrupt
                return self.response(url, payload, timeout)
            with self.subTest(phase=phase):
                status, summary, facts = self.invoke(response=response)
                self.assertEqual(status, 130)
                self.assertEqual(summary, '')
                self.assertTrue(facts['interrupted'])
                self.assertEqual(facts['command_exit'], None if phase == 'setup' else 0)

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group ownership')
    def test_review_group_leader_remains_waitable_until_last_signal(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        killpg, sleep = os.killpg, time.sleep
        events = []
        def observe(pid, sig):
            self.assertIsNone(process.returncode)
            # Raises ECHILD if this PID has already been reaped, even if recycled.
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            events.append((sig, info is not None))
            return killpg(pid, sig)
        try:
            with patch.object(helper.os, 'killpg', side_effect=observe), patch.object(helper.time, 'sleep', side_effect=lambda _: sleep(0.1)):
                helper.terminate_process(process)
            self.assertEqual([sig for sig, _ in events], [signal.SIGTERM, signal.SIGKILL])
            self.assertTrue(events[-1][1], 'leader should have exited but remain unreaped')
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        finally:
            process.kill()  # Popen guards its own, already-reaped PID.
            process.wait()

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group ownership')
    def test_review_reaped_or_unowned_child_never_receives_group_signal(self):
        with patch.object(helper.os, 'killpg') as send:
            helper.terminate_process(Mock(returncode=0))
            with patch.object(helper.os, 'waitid', side_effect=ChildProcessError):
                errors = helper.terminate_process(Mock(returncode=None, pid=12345))
            send.assert_not_called()
            self.assertTrue(errors)

    def test_review_scan_boundaries_match_whole_text(self):
        cases = [
            b'z' * 8184 + b' warnings ok\n' + b'y' * 90000,
            b'z' * 8186 + b' errorx' + b'y' * 200,
            b'prefix\n' + b'x' * 8176 + b' critical failure\n' + b'y' * 9000,
            ('日本語' * 910 + '\nERROR unicode boundary\n').encode(),
            b'z' * 8186 + b' error',
        ]
        path = self.root / 'boundary.log'
        for raw in cases:
            with self.subTest(length=len(raw)):
                path.write_bytes(raw)
                positions, count = helper.diagnostic_positions(path)
                expected = [match.start() for match in helper.DIAGNOSTIC.finditer(raw.decode('utf-8', 'surrogateescape'))]
                self.assertEqual(count, len(expected))
                self.assertEqual(positions, expected)

    def selection_fixture(self, stdout, stderr=b'', budget=12000):
        (self.root / 'stdout.log').write_bytes(stdout)
        (self.root / 'stderr.log').write_bytes(stderr)
        return helper.build_captured_output(self.root, budget)

    def test_review_overlapping_windows_emit_unique_source_bytes(self):
        first = b''.join(f'ERROR start-{i}: failed step\n'.encode() for i in range(5))
        last = b''.join(f'ERROR end-{i}: failed step\n'.encode() for i in range(5))
        text, coverage = self.selection_fixture(first + b'ordinary progress\n' * 100000 + last)
        facts = coverage['stdout']
        self.assertLessEqual(len(text), 12000)
        self.assertGreater(facts['selected_bytes'], 11000)
        ranges = facts['ranges']
        self.assertEqual(sum(end - start for start, end in ranges), facts['selected_bytes'])
        self.assertTrue(all(left[1] < right[0] for left, right in zip(ranges, ranges[1:])))
        self.assertLessEqual(facts['diagnostic_windows'], len(ranges))

    def test_review_quiet_stream_returns_unused_budget(self):
        stdout = b'ordinary progress\n' * 40000
        _, empty = self.selection_fixture(stdout)
        text, quiet = self.selection_fixture(stdout, b'\n')
        self.assertGreater(quiet['stdout']['selected_bytes'], empty['stdout']['selected_bytes'] * 0.98)
        self.assertGreater(len(text), 11800)
        self.assertLessEqual(len(text), 12000)
        self.assertFalse(quiet['stderr']['truncated'])
        for out, err in ((b'x' * 8000, b'\n'), (b'\n', b'x' * 8000)):
            _, coverage = self.selection_fixture(out, err)
            self.assertFalse(any(facts['truncated'] for facts in coverage.values()))

    def test_review_unicode_that_fits_is_retained_whole(self):
        for text in ('start\n' + '日本語' * 1666 + '\n', 'e\u0301' * 3050, '😀' * 5000):
            with self.subTest(characters=len(text)):
                raw = text.encode()
                evidence, coverage = self.selection_fixture(raw)
                self.assertIn(text, evidence)
                self.assertNotIn('\ufffd', evidence)
                self.assertFalse(coverage['stdout']['truncated'])
                self.assertEqual(coverage['stdout']['ranges'], [[0, len(raw)]])
                self.assertEqual(coverage['stdout']['total_chars'], len(text))

    def test_review_partial_unicode_ranges_end_at_codepoint_boundaries(self):
        for prefix in ('a', 'aa', 'aaa', 'start\n'):
            raw = (prefix + ('日本語😀e\u0301\n' * 10000)).encode()
            evidence, coverage = self.selection_fixture(raw)
            self.assertNotIn('\ufffd', evidence)
            self.assertLessEqual(len(evidence), 12000)
            self.assertGreater(coverage['stdout']['selected_chars'], 11000)
            self.assertTrue(coverage['stdout']['truncated'])
            for start, end in coverage['stdout']['ranges']:
                raw[start:end].decode('utf-8', errors='strict')



if __name__ == '__main__':
    unittest.main()
