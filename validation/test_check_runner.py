"""Preserve complete check coverage and receipts when a real child times out."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation import run_checks
from validation.lifecycle import test_lifecycle
from validation.test_support import temporary_directory


def module_case_ids():
    def flatten(suite):
        for case in suite:
            if isinstance(case, unittest.TestSuite):
                yield from flatten(case)
            else:
                yield case.id()
    return list(flatten(unittest.defaultTestLoader.loadTestsFromModule(test_lifecycle)))


class CheckRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_shards_cover_actual_loader_once_and_include_new_cases(self):
        shards = run_checks.lifecycle_shards(ROOT / 'validation/lifecycle/test_lifecycle.py')
        actual = module_case_ids()
        self.assertEqual(len(shards), 2)
        self.assertTrue(all(shards))
        self.assertFalse(set(shards[0]) & set(shards[1]))
        self.assertEqual(sorted(shards[0] + shards[1]), sorted(actual))
        self.assertEqual(len(shards[0] + shards[1]), len(actual))
        self.assertEqual(shards, run_checks.lifecycle_shards(ROOT / 'validation/lifecycle/test_lifecycle.py'))
        source = self.root / 'cases.py'
        source.write_text('class Lifecycle(unittest.TestCase):\n'
                          '    def test_b(self): pass\n'
                          '    def test_a(self): pass\n'
                          '    def test_new(self): pass\n', encoding='utf-8')
        selected = run_checks.lifecycle_shards(source)
        self.assertEqual(sorted(selected[0] + selected[1]),
                         ['validation.lifecycle.test_lifecycle.Lifecycle.' + name
                          for name in ['test_a', 'test_b', 'test_new']])

    def test_unsupported_discovery_shapes_fail_closed(self):
        normal = ('class Lifecycle(unittest.TestCase):\n'
                  '    def test_a(self): pass\n'
                  '    def test_b(self): pass\n')
        unsupported = [normal.replace('unittest.TestCase', 'OtherBase'),
                       normal + 'class Other(unittest.TestCase): pass\n',
                       normal + 'def load_tests(loader, tests, pattern):\n    return loader.loadTestsFromName("other_module.Other")\n',
                       normal + 'load_tests = lambda loader, tests, pattern: tests\n',
                       normal + 'Lifecycle.test_dynamic = lambda self: None\n',
                       normal.replace('def test_b', 'def test_a'),
                       normal.replace('    def test_b', '    @decorator\n    def test_b'),
                       normal.replace('def test_b', 'async def test_b'),
                       normal + '    test_alias = test_a\n']
        source = self.root / 'unsupported.py'
        for body in unsupported:
            with self.subTest(source=body):
                source.write_text(body, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Lifecycle'):
                    run_checks.lifecycle_shards(source)

    def test_both_modes_build_complete_shards_without_direct_suite_duplicate(self):
        commands = run_checks.build_commands(ROOT)
        expected = set(module_case_ids())
        for optimized in [False, True]:
            mode = [command for command in commands if ('-O' in command) == optimized]
            shards = [command for command in mode if 'unittest' in command]
            self.assertEqual(len(shards), 2)
            cases = [arg for command in shards for arg in command if arg.startswith('validation.lifecycle.')]
            self.assertEqual(set(cases), expected)
            self.assertEqual(len(cases), len(expected))
            self.assertTrue(all('-B' in command and '-v' in command for command in shards))
            self.assertFalse(any('validation/lifecycle/test_lifecycle.py' in command for command in mode))
            self.assertEqual(sum('validation/test_check_runner.py' in command for command in mode), 1)

    def test_normal_failure_logs_streams_and_continues_checks(self):
        commands = [[sys.executable, '-u', '-c', code] for code in [
            "import sys; print('normal stdout'); print('normal stderr', file=sys.stderr)",
            "import sys; print('failure output', file=sys.stderr); sys.exit(7)",
            "print('remaining check ran')",
        ]]
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = run_checks.run_commands(commands, self.root, dict(os.environ))
        self.assertEqual(code, 1)
        summary = json.loads((self.root / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual([item['status'] for item in summary], ['PASS', 'FAIL', 'PASS'])
        self.assertEqual([item['exit_code'] for item in summary], [0, 7, 0])
        self.assertTrue(all(item['duration_seconds'] >= 0 for item in summary))
        self.assertIn('normal stdout', (self.root / 'check-00.log').read_text(encoding='utf-8'))
        self.assertIn('normal stderr', (self.root / 'check-00.log').read_text(encoding='utf-8'))
        self.assertIn('failure output', (self.root / 'check-01.log').read_text(encoding='utf-8'))
        self.assertIn('remaining check ran', (self.root / 'check-02.log').read_text(encoding='utf-8'))
        lines = stdout.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])['receipt_directory'], str(self.root))
        self.assertEqual(json.loads(lines[1])['event'], 'START')
        self.assertIn('failure output', stdout.getvalue())

    def test_running_summary_is_checkpointed_before_child_with_default_180_bound(self):
        shards = run_checks.lifecycle_shards(ROOT / 'validation/lifecycle/test_lifecycle.py')
        commands = [[sys.executable, '-B', '-m', 'unittest', '-v', *cases] for cases in shards]
        calls = []
        def completed(command, **kwargs):
            snapshot = json.loads((self.root / 'summary.json').read_text(encoding='utf-8'))
            number = len(calls)
            self.assertEqual(snapshot[number]['status'], 'RUNNING')
            self.assertEqual(snapshot[number]['test_cases'], shards[number])
            if number == 0:
                self.assertEqual(snapshot[1]['status'], 'NOT_RUN')
            self.assertEqual(kwargs['timeout'], 180)
            self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
            self.assertNotIn('capture_output', kwargs)
            kwargs['stdout'].write(b'checkpoint output\n')
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(run_checks.subprocess, 'run', side_effect=completed):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_checks.run_commands(commands, self.root, dict(os.environ)), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual([row['status'] for row in json.loads((self.root / 'summary.json').read_text())], ['PASS', 'PASS'])

    def test_real_timeout_preserves_partial_log_and_marks_remaining_not_run(self):
        marker = self.root / 'must-not-run'
        commands = [
            [sys.executable, '-u', '-c',
             "import sys,time; print('partial stdout',flush=True); print('partial stderr',file=sys.stderr,flush=True); time.sleep(30)"],
            [sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').write_text("ran")'],
        ]
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = run_checks.run_commands(commands, self.root, dict(os.environ), timeout=2)
        self.assertEqual(code, 1)
        summary = json.loads((self.root / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual([row['status'] for row in summary], ['TIMEOUT', 'NOT_RUN'])
        self.assertEqual(summary[0]['exit_code'], 124)
        self.assertEqual(summary[0]['descendant_cleanup'], 'UNKNOWN')
        self.assertGreater(summary[0]['duration_seconds'], 0)
        self.assertIsNone(summary[1]['exit_code'])
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / 'check-01.log').exists())
        partial = (self.root / 'check-00.log').read_text(encoding='utf-8')
        self.assertIn('partial stdout', partial)
        self.assertIn('partial stderr', partial)
        self.assertEqual(json.loads(stdout.getvalue().splitlines()[0])['receipt_directory'], str(self.root))
        self.assertEqual(json.loads(stdout.getvalue().splitlines()[-1])['result'], 'FAIL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
