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
from unittest.mock import patch
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
        if final['artifact_dir']:
            directory = Path(final['artifact_dir'])
            self.assertEqual(json.loads((directory / 'metadata.json').read_text()), final)
        return status, stdout.getvalue(), final

    def test_nonzero_status_is_preserved_and_facts_are_not_model_prose(self):
        status, summary, facts = self.invoke('print("worker log"); raise SystemExit(7)')
        self.assertEqual(status, 7)
        self.assertEqual(summary, 'A concise summary.\n')
        self.assertEqual(facts['command_exit'], 7)
        self.assertEqual(facts['model'], 'stub-chat')
        self.assertFalse(facts['timed_out'])
        self.assertFalse(facts['truncated'])
        self.assertEqual(facts['summary_status'], 'ok')
        self.assertIn('Exit code: 7', self.requests[0]['messages'][1]['content'])

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
        descendant = 'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(os.getpid(), flush=True)\nfor _ in range(600):\n os.write(1, b"heartbeat\\n"); time.sleep(0.1)'
        code = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {descendant!r}]); time.sleep(60)'
        status, _, facts = self.invoke(code, ['--timeout', '1'])
        self.assertEqual(status, 124)
        log = Path(facts['artifact_dir']) / 'stdout.log'
        pid = int(log.read_text().splitlines()[0])
        def cleanup():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(cleanup)
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


if __name__ == '__main__':
    unittest.main()
