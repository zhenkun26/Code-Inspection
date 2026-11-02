"""Preserve complete check coverage and receipts when a real child times out."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation import run_checks
from validation import ci_receipts, mutation_check
from validation.lifecycle import test_lifecycle
from validation.test_support import temporary_directory
from validation.test_windows_temporary_qualification import protocol_fixture


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


class CiReceipts(unittest.TestCase):
    """Synthetic protocol tests never represent a hosted or native CI run."""
    def setUp(self):
        retained = patch.dict(os.environ, {'CODE_INSPECTION_KEEP_TEST_ARTIFACTS': '1'})
        retained.start()
        self.addCleanup(retained.stop)
        self.root = Path(temporary_directory().name)
        self.c = ci_receipts
        self.context = dict(sha='1' * 40, tree='2' * 40,
                            workflow_ref='test-owner/test-repo/.github/workflows/ci.yml@refs/heads/test',
                            workflow_sha256='3' * 64, run_id='123', run_attempt='2',
                            platform=self.c.PLATFORM)
        self.maintained = self.c.receipt_json('maintained', self.context,
            [dict(id=name, status='PASS', exit_code=0) for name in self.c.maintained_ids()])

    def raw(self, kind):
        if kind == 'maintained':
            return self.maintained
        rows = ([dict(id=case[0], result='KILLED', baseline_exit_code=0, exit_code=1)
                 for case in mutation_check.platform_cases('nt')]
                if kind == 'mutations' else protocol_fixture()['observations'])
        return self.c.receipt_json(kind, self.context, rows)

    def test_original_ordered_command_and_mutation_inventories_are_unchanged(self):
        # Independently captured before factoring platform selection; these bind
        # every source replacement, copied control, command, and their order.
        inventories = [('maintained', [row[1:] for row in run_checks.build_commands(ROOT)], 64,
                        '94e497c758a21e815ec5903d7de6afc7c825b21d42095c3730724890ed8fc6d2'),
                       ('nt', mutation_check.platform_cases('nt'), 196,
                        '56e1dbb0e32f81407d3253f7e7d7bb7c3f4505984d35841a707816dc41c0b550'),
                       ('posix', mutation_check.platform_cases('posix'), 221,
                        'f9d8d796be749047d17106489af725918a8a255f5d23a8a5fc84434da1b8194f')]
        for name, rows, count, expected in inventories:
            with self.subTest(platform=name):
                self.assertEqual(len(rows), count)
                self.assertEqual(hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest(), expected)
        self.assertEqual(len(self.c.maintained_ids()), 66)
        self.assertEqual(len(set(self.c.maintained_ids())), 66)
        mutation_check.platform_cases('posix').clear()
        self.assertEqual(len(mutation_check.CASES), 221)
        with self.assertRaises(ValueError): mutation_check.platform_cases('other')

    def test_portable_output_caps_fit_windows_environment_and_fixed_key(self):
        self.assertLessEqual(self.c.LIMIT, 24 * 1024)
        for kind in ('maintained', 'mutations', 'observe', 'optimized'):
            raw = self.raw(kind)
            with self.subTest(kind=kind):
                self.assertLess(len(raw), self.c.LIMIT)
                self.assertLess(len(('CI_MUTATION_RECEIPT=' + raw + '\0').encode('utf-16-le')) // 2, 32767)
                self.assertNotIn(str(self.root), raw)
                self.assertNotIn('"command"', raw)
                self.assertNotIn('"log"', raw)
        with self.assertRaises(ValueError): self.c.strict_json('"' + 'x' * (24 * 1024) + '"')
        output = self.root / 'github-output'
        output.write_bytes(b'')
        with contextlib.redirect_stdout(io.StringIO()):
            self.c.emit(self.maintained, {'GITHUB_OUTPUT': str(output)})
        self.assertEqual(output.read_text(), 'receipt=' + self.maintained + '\n')
        with self.assertRaises(ValueError): self.c.emit(self.maintained + '\nreceipt=forged', {'GITHUB_OUTPUT': str(output)})
        self.assertEqual(output.read_text(), 'receipt=' + self.maintained + '\n')

    def test_strict_json_and_schema_reject_ambiguous_or_extended_inputs(self):
        original = json.loads(self.maintained)
        bad = ['', self.maintained[:-1], '[]', 'null', '{"version":1,"version":1}',
               self.maintained.replace('"version":1', '"version":NaN'),
               self.maintained.replace('"version":1', '"version":true'), ' ' * (self.c.LIMIT + 1)]
        for value in (None, True, 2, '1'):
            item = copy.deepcopy(original); item['version'] = value; bad.append(json.dumps(item))
        item = copy.deepcopy(original); item['raw_path'] = str(self.root); bad.append(json.dumps(item))
        item = copy.deepcopy(original); item['records'][0]['exit_code'] = False; bad.append(json.dumps(item))
        item = copy.deepcopy(original); item['records'][0]['status'] = 'RUNNING'; bad.append(json.dumps(item))
        for raw in bad:
            with self.subTest(raw=raw[:80]), self.assertRaises(ValueError):
                self.c.validate_receipt(raw, 'maintained', self.context)
        with self.assertRaises(ValueError):
            self.c.strict_json('{"nested":{"x":1,"x":1}}')
        with self.assertRaises(ValueError): self.c.strict_json('{"x":Infinity}')

    def test_exact_sha_tree_workflow_platform_and_attempt_binding(self):
        for key, wrong in [('sha', '4' * 40), ('tree', '5' * 40), ('workflow_sha256', '6' * 64),
                           ('workflow_ref', self.context['workflow_ref'] + '-different'),
                           ('run_id', '124'), ('run_attempt', '3'), ('platform', 'ubuntu-24.04')]:
            item = json.loads(self.maintained); item['context'][key] = wrong
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.c.validate_receipt(json.dumps(item), 'maintained', self.context)
        for key, wrong in [('run_id', True), ('run_attempt', 2), ('workflow_ref', 'private\npath')]:
            item = json.loads(self.maintained); item['context'][key] = wrong
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.c.validate_receipt(json.dumps(item), 'maintained', self.context)

    def test_actual_windows_runtime_and_clean_exact_head_are_required(self):
        env = dict(GITHUB_ACTIONS='true', RUNNER_OS='Windows', GITHUB_SHA=self.context['sha'],
                   GITHUB_WORKFLOW_REF=self.context['workflow_ref'], GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2')
        facts = dict(os_name='nt', implementation='CPython', python=(3, 13), machine='amd64',
                     pointer_bits=64, windows=(10, 0, 26100, 3))
        with patch.object(self.c.qualification, 'runtime_facts', return_value={'os_name': 'posix'}), \
             patch.object(self.c, 'git_value') as git:
            with self.assertRaises(ValueError): self.c.current_context(env)
            git.assert_not_called()
        with patch.dict(os.environ, env), \
             patch.object(self.c.qualification, 'runtime_facts', return_value={'os_name': 'posix'}), \
             patch.object(self.c, 'retained_output') as fixture, \
             patch.object(self.c.qualification, 'main') as native, patch.object(self.c, 'emit') as emit, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.c.main(['maintained']), 1)
        fixture.assert_not_called(); native.assert_not_called(); emit.assert_not_called()
        with patch.object(self.c.qualification, 'runtime_facts', return_value=facts), \
             patch.object(self.c, 'git_value', side_effect=[self.context['sha'], self.context['tree']]), \
             patch.object(self.c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            actual = self.c.current_context(env)
        expected = dict(self.context, workflow_sha256=hashlib.sha256((ROOT / '.github/workflows/ci.yml').read_bytes()).hexdigest())
        self.assertEqual(actual, expected)
        with patch.object(self.c.qualification, 'runtime_facts', return_value=facts), \
             patch.object(self.c, 'git_value', return_value=self.context['sha']), \
             patch.object(self.c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            for change in ({}, {'GITHUB_SHA': '0' * 40}, {'GITHUB_ACTIONS': 'false'}, {'RUNNER_OS': 'Linux'}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    self.c.current_context(dict(env, **change))

    def test_missing_duplicate_unknown_reordered_failed_records_never_pass(self):
        for kind in ('maintained', 'mutations'):
            original = json.loads(self.raw(kind))
            for rows in ([], original['records'][:-1], original['records'] * 2,
                         original['records'][::-1], [original['records'][0]] * len(original['records'])):
                item = dict(original, records=rows)
                with self.subTest(kind=kind, length=len(rows)), self.assertRaises(ValueError):
                    self.c.validate_receipt(json.dumps(item), kind, self.context)
            item = copy.deepcopy(original); item['records'][0]['id'] = 'unknown'
            with self.assertRaises(ValueError): self.c.validate_receipt(json.dumps(item), kind, self.context)
        for field, wrong in [('baseline_exit_code', 1), ('baseline_exit_code', False),
                             ('exit_code', 0), ('exit_code', True), ('result', 'SURVIVED')]:
            item = json.loads(self.raw('mutations')); item['records'][0][field] = wrong
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.c.validate_receipt(json.dumps(item), 'mutations', self.context)

    def test_no_expensive_phase_runs_on_invalid_or_unsuccessful_upstream(self):
        original = json.loads(self.maintained)
        stale = copy.deepcopy(original); stale['context']['run_attempt'] = '1'
        variants = [dict(), dict(CI_MAINTAINED_RESULT='failure', CI_MAINTAINED_RECEIPT=self.maintained),
                    dict(CI_MAINTAINED_RESULT='success', CI_MAINTAINED_RECEIPT=''),
                    dict(CI_MAINTAINED_RESULT='success', CI_MAINTAINED_RECEIPT=self.maintained[:-1]),
                    dict(CI_MAINTAINED_RESULT='success', CI_MAINTAINED_RECEIPT=json.dumps(stale))]
        for phase in ('mutations', 'observe', 'aggregate'):
            for env in variants:
                with self.subTest(phase=phase, env=env.get('CI_MAINTAINED_RESULT')), \
                     patch.dict(os.environ, env, clear=True), \
                     patch.object(self.c, 'current_context', return_value=self.context), \
                     patch.object(self.c, 'run_mutations') as mutations, \
                     patch.object(self.c, 'run_observation') as observation, \
                     patch.object(self.c, 'aggregate') as aggregate, patch.object(self.c, 'emit') as emit, \
                     patch.object(self.c, 'retained_output') as fixture, \
                     contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(self.c.main([phase]), 1)
                    self.assertEqual(json.loads(error.getvalue())['result'], 'FAIL')
                    mutations.assert_not_called(); observation.assert_not_called()
                    aggregate.assert_not_called(); fixture.assert_not_called(); emit.assert_not_called()

    def test_main_requires_complete_output_and_sanitizes_failures(self):
        env = dict(CI_MAINTAINED_RESULT='success', CI_MAINTAINED_RECEIPT=self.maintained)
        with patch.dict(os.environ, env), patch.object(self.c, 'current_context', return_value=self.context), \
             patch.object(self.c, 'run_mutations', side_effect=ValueError('private-secret-' + str(self.root))), \
             patch.object(self.c, 'emit') as emit, contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(self.c.main(['mutations']), 1)
        emit.assert_not_called()
        self.assertNotIn('private-secret', error.getvalue()); self.assertNotIn(str(self.root), error.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.c.main(['mutations', '--receipt-path', str(self.root)])

    def test_partial_or_mismatched_runner_summary_is_not_success(self):
        commands = [[sys.executable, '-B', '-c', 'print("retained control")']]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_checks.run_commands(commands, self.root, dict(os.environ)), 0)
        self.c.successful_checks(self.root, commands)
        summary = json.loads((self.root / 'summary.json').read_text())
        for field, wrong in [('status', 'RUNNING'), ('exit_code', False), ('command', ['other']),
                             ('duration_seconds', float('nan')), ('log', '../other')]:
            invalid = copy.deepcopy(summary); invalid[0][field] = wrong
            (self.root / 'summary.json').write_text(json.dumps(invalid))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.c.successful_checks(self.root, commands)
        (self.root / 'summary.json').write_text('[]')
        with self.assertRaises(ValueError): self.c.successful_checks(self.root, commands)

    def test_mutation_requires_real_positive_control_and_failure_without_error(self):
        case = next(case for case in mutation_check.platform_cases(os.name) if case[0] == 'runner-load-tests-coverage')
        with contextlib.redirect_stdout(io.StringIO()):
            code = mutation_check.run_cases([case], self.root, dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(code, 0)
        self.assertEqual(self.c.successful_mutations(self.root, [case]),
                         [dict(id=case[0], result='KILLED', baseline_exit_code=0, exit_code=1)])
        self.assertTrue((self.root / case[0] / case[1]).is_file())
        original = json.loads((self.root / 'summary.json').read_text())
        for field, wrong in [('baseline_exit_code', 1), ('exit_code', 2), ('result', 'NOT_VERIFIED')]:
            invalid = copy.deepcopy(original); invalid[0][field] = wrong
            (self.root / 'summary.json').write_text(json.dumps(invalid))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.c.successful_mutations(self.root, [case])
        (self.root / 'summary.json').write_text(json.dumps(original))
        self.assertTrue(mutation_check.killed_result(1, 'FAIL: intended'))
        self.assertFalse(mutation_check.killed_result(1, 'FAIL: intended\nERROR: import'))
        self.assertFalse(mutation_check.killed_result(2, 'FAIL: intended'))

    def test_real_runner_refuses_stdout_fail_stderr_error_and_failed_control(self):
        case = next(case for case in mutation_check.platform_cases(os.name) if case[0] == 'runner-load-tests-coverage')
        for name, results in [
                ('runtime-error', [subprocess.CompletedProcess([], 0, '', 'OK\n'),
                                   subprocess.CompletedProcess([], 1, 'FAIL: stdout only\n', 'ERROR: runtime\n')]),
                ('control-error', [subprocess.CompletedProcess([], 1, '', 'FAIL: positive control\n')])]:
            output = self.root / name; output.mkdir()
            with self.subTest(case=name), patch.object(mutation_check.subprocess, 'run', side_effect=results) as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mutation_check.run_cases([case], output, dict(os.environ)), 1)
                self.assertEqual(run.call_count, len(results))
                self.assertEqual(json.loads((output / 'summary.json').read_text())[0]['result'], 'NOT_VERIFIED')
                with self.assertRaises(ValueError): self.c.successful_mutations(output, [case])
        with patch.object(self.c, 'retained_output', return_value=self.root), \
             patch.object(mutation_check, 'run_cases', return_value=1), \
             patch.object(self.c, 'successful_mutations') as summary:
            with self.assertRaises(ValueError): self.c.run_mutations(self.context)
        summary.assert_not_called()

    def test_observations_require_complete_order_and_infer_interpreter_mode(self):
        original = protocol_fixture()
        for mode in (0, 1):
            def observed(_):
                print(json.dumps(original))
                return 0
            with patch.object(self.c.sys, 'flags', SimpleNamespace(optimize=mode)), \
                 patch.object(self.c.qualification, 'main', side_effect=observed) as native:
                raw = self.c.run_observation(self.context)
            self.assertEqual(json.loads(raw)['kind'], 'optimized' if mode else 'observe')
            native.assert_called_once_with(['--authorize-owned-fixture'])
        for change in ('missing', 'reordered', 'incomplete', 'truncated', 'bad-exit'):
            report = copy.deepcopy(original)
            if change == 'missing': report['observations'].pop()
            if change == 'reordered': report['observations'].reverse()
            if change == 'incomplete': report = self.c.qualification.envelope('NATIVE_PROBE_FAILED', started=True)
            def failed(_):
                text = json.dumps(report)
                print(text[:-1] if change == 'truncated' else text)
                return 2 if change == 'bad-exit' else 0
            with self.subTest(change=change), patch.object(self.c.qualification, 'main', side_effect=failed), \
                 self.assertRaises(ValueError):
                self.c.run_observation(self.context)

    def test_aggregate_requires_all_same_attempt_successes_and_all_controls(self):
        env = {}
        for kind, prefix in [('mutations', 'CI_MUTATION'), ('observe', 'CI_OBSERVE'), ('optimized', 'CI_OPTIMIZED')]:
            env[prefix + '_RESULT'] = 'success'; env[prefix + '_RECEIPT'] = self.raw(kind)
        maintained = self.c.validate_receipt(self.maintained, 'maintained', self.context)
        aggregate = self.c.aggregate(env, self.context, maintained)
        self.assertEqual([row['id'] for row in json.loads(aggregate)['records']],
                         ['maintained', 'mutations', 'observe', 'optimized'])
        for prefix in ('CI_MUTATION', 'CI_OBSERVE', 'CI_OPTIMIZED'):
            for wrong in ('failure', 'skipped', 'cancelled', ''):
                with self.subTest(prefix=prefix, result=wrong), self.assertRaises(ValueError):
                    self.c.aggregate(dict(env, **{prefix + '_RESULT': wrong}), self.context, maintained)
            for wrong in ('', env[prefix + '_RECEIPT'][:-1]):
                with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                    self.c.aggregate(dict(env, **{prefix + '_RECEIPT': wrong}), self.context, maintained)
        stale = json.loads(env['CI_OPTIMIZED_RECEIPT']); stale['context']['run_attempt'] = '1'
        with self.assertRaises(ValueError):
            self.c.aggregate(dict(env, CI_OPTIMIZED_RECEIPT=json.dumps(stale)), self.context, maintained)
        with self.assertRaises(ValueError):
            self.c.aggregate(dict(env, CI_OPTIMIZED_RECEIPT=env['CI_OBSERVE_RECEIPT']), self.context, maintained)

    def diagnostic_run(self, fault=None):
        """Synthetic producer: never execute product checks or claim hosted CI."""
        commands = run_checks.build_commands(ROOT)
        original_read = self.c.read_owned
        counts = dict(allocate=0, run=0, discover=0)

        def allocate():
            counts['allocate'] += 1
            if fault == 'allocate' and counts['allocate'] == 2:
                raise OSError('private-value-' + str(self.root))
            output = self.root / ('phase-%d' % counts['allocate'])
            output.mkdir()
            return output

        def discover(_):
            counts['discover'] += 1
            if fault == 'discover':
                raise ValueError('private-value-' + str(self.root))
            return commands

        def runner(selected, output, env):
            counts['run'] += 1
            rows = []
            for number, command in enumerate(selected):
                row = dict(command=command[1:], status='PASS', exit_code=0,
                           duration_seconds=0.125, log='check-%02d.log' % number)
                cases = [x for x in command if x.startswith('validation.lifecycle.test_lifecycle.Lifecycle.')]
                if cases:
                    row['test_cases'] = cases
                rows.append(row)
                (output / row['log']).write_bytes(b'synthetic control')
            raw = json.dumps(rows).encode('utf-8')
            if counts['run'] == 1:
                if fault == 'json': raw = b'{'
                if fault == 'decode': raw = b'\xff'
                if fault == 'schema': raw = b'[]'
                if fault == 'log': (output / 'check-00.log').unlink()
            (output / 'summary.json').write_bytes(raw)
            return 0

        def read(path, *args):
            if path.parent.name == 'phase-1':
                if fault == 'read': raise OSError('private-value-' + str(self.root))
                if fault == 'metadata':
                    fields = list(path.lstat()); fields[6] += 1
                    with patch.object(self.c.os, 'fstat', return_value=os.stat_result(fields)):
                        return original_read(path, *args)
            return original_read(path, *args)

        with patch.object(self.c, 'current_context', return_value=self.context), \
             patch.object(self.c, 'retained_output', side_effect=allocate), \
             patch.object(run_checks, 'build_commands', side_effect=discover), \
             patch.object(run_checks, 'run_commands', side_effect=runner), \
             patch.object(self.c, 'read_owned', side_effect=read), \
             patch.object(self.c, 'emit') as emit, contextlib.redirect_stderr(io.StringIO()) as errors:
            code = self.c.main(['maintained'])
        return code, json.loads(errors.getvalue()) if errors.getvalue() else None, counts, emit

    def test_diagnostic_success_control_preserves_single_execution_and_receipt(self):
        code, report, counts, emit = self.diagnostic_run()
        self.assertEqual(code, 0)
        self.assertIsNone(report)
        self.assertEqual(counts['run'], 2)
        self.assertEqual(counts['allocate'], 2)
        emit.assert_called_once()
        self.c.validate_receipt(emit.call_args.args[0], 'maintained', self.context)

    def test_diagnostic_distinguishes_summary_read_decode_and_schema(self):
        for fault, stage, category in [('read', 'native_summary_read', 'filesystem'),
                                       ('decode', 'native_summary_decode', 'decode'),
                                       ('json', 'native_summary_decode', 'json'),
                                       ('schema', 'native_summary_validate', 'guard'),
                                       ('log', 'native_log_metadata', 'filesystem')]:
            with self.subTest(fault=fault):
                # Each case owns a different retained directory, even on failure.
                previous = self.root; self.root = previous / fault; self.root.mkdir()
                code, report, counts, emit = self.diagnostic_run(fault)
                self.root = previous
                self.assertEqual(code, 1)
                self.assertEqual(report.get('stage'), stage)
                self.assertEqual(report.get('category'), category)
                self.assertNotIn('private-value', json.dumps(report))
                self.assertEqual(counts['run'], 1)
                self.assertEqual(counts['allocate'], 1)
                emit.assert_not_called()

    def test_diagnostic_discovery_and_allocation_do_not_repeat_prechecks(self):
        for fault, stage, category in [('discover', 'maintained_discovery', 'guard'),
                                       ('allocate', 'maintained_allocate', 'filesystem')]:
            with self.subTest(fault=fault):
                previous = self.root; self.root = previous / fault; self.root.mkdir()
                code, report, counts, emit = self.diagnostic_run(fault)
                self.root = previous
                self.assertEqual(code, 1)
                self.assertEqual(report.get('stage'), stage)
                self.assertEqual(report.get('category'), category)
                self.assertEqual(counts['run'], 1)
                self.assertEqual(counts['discover'], 1)
                self.assertNotIn('private-value', json.dumps(report))
                emit.assert_not_called()

    def test_diagnostic_metadata_mismatch_keeps_guard_and_reports_only_field_names(self):
        code, report, counts, emit = self.diagnostic_run('metadata')
        self.assertEqual(code, 1)
        self.assertEqual(report.get('stage'), 'native_summary_read')
        self.assertEqual(report.get('category'), 'guard')
        self.assertEqual(report.get('reason'), 'metadata_mismatch')
        self.assertEqual(report.get('fields'), ['st_size'])
        self.assertEqual(counts['run'], 1)
        self.assertNotIn(str(self.root), json.dumps(report))
        emit.assert_not_called()

    def test_diagnostic_exception_categories_are_fixed_and_never_raw_messages(self):
        for exc, category in [(KeyboardInterrupt('private-value'), 'interrupted'),
                              (RuntimeError('private-value'), 'unexpected'),
                              (OSError('private-value'), 'filesystem')]:
            with self.subTest(category=category), \
                 patch.object(self.c, 'current_context', side_effect=exc), \
                 patch.object(self.c, 'emit') as emit, contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(self.c.main(['maintained']), 1)
            report = json.loads(errors.getvalue())
            self.assertEqual(report.get('stage'), 'context')
            self.assertEqual(report.get('category'), category)
            self.assertNotIn('private-value', errors.getvalue())
            self.assertLess(len(errors.getvalue()), 512)
            emit.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
