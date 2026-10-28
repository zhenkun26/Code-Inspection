"""Budget observations preserve lifecycle charging and historical recovery."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from interfaces.lifecycle import cli, core, merge
from validation.lifecycle import test_lifecycle as fixture_tests
from validation.lifecycle.fixture import write


class BudgetAccounting(unittest.TestCase):
    setUp = fixture_tests.Lifecycle.setUp
    run_cli = fixture_tests.Lifecycle.run_cli
    initialize = fixture_tests.Lifecycle.initialize
    edit_policy = fixture_tests.Lifecycle.edit_policy
    batch = fixture_tests.Lifecycle.batch
    approval = fixture_tests.Lifecycle.approval
    ready = fixture_tests.Lifecycle.ready
    prepare = fixture_tests.Lifecycle.prepare
    merge_ready = fixture_tests.Lifecycle.merge_ready
    merged = fixture_tests.Lifecycle.merged
    action = fixture_tests.Lifecycle.action

    def state(self):
        return json.loads((self.host / 'state.json').read_text(encoding='utf-8'))

    def assert_report(self, out):
        self.assertIn('budget_report', out, 'CLI must report existing budget and observed invocation work')
        report = out['budget_report']
        self.assertEqual(report['schema'], 'lifecycle-budget-report/v1')
        self.assertFalse(report['authorizes_action'])
        self.assertFalse(out['authorizes_action'])
        self.assertEqual(report['observed_work']['scope'], 'current_cli_invocation')
        self.assertGreaterEqual(report['observed_work']['wall_seconds'], 0)
        for name in ['git_subprocess_attempts', 'repository_scan_attempts',
                     'repository_entries_scanned', 'merge_tree_write_attempts']:
            self.assertIs(type(report['observed_work'][name]), int)
            self.assertGreaterEqual(report['observed_work'][name], 0)
        for name in ['cost', 'runtime_seconds']:
            self.assertEqual(report['external'][name], {'status': 'unknown', 'value': None})
        return report

    def test_inherited_usage_and_observed_zero_are_distinct_from_unknown_external_work(self):
        out = self.run_cli('init', 'handoff.json')
        self.assertEqual(out['status'], 'NOT_RUN')
        report = self.assert_report(out)
        ledger = report['ledger']
        self.assertEqual((ledger['status'], ledger['basis'], ledger['durability']),
                         ('known', 'persisted_ledger', 'persisted'))
        self.assertEqual(ledger['operation_attempts'],
                         {'unit': 'started_lifecycle_operation', 'used': 2, 'limit': 60,
                          'remaining': 58, 'exhausted': False})
        self.assertEqual(ledger['review_rounds'],
                         {'unit': 'review_round', 'used': 1, 'limit': 3,
                          'remaining': 2, 'exhausted': False})
        self.assertEqual(report['observed_work']['git_subprocess_attempts'], 0)
        self.assertEqual(report['observed_work']['repository_scan_attempts'], 0)
        self.assertEqual(ledger['capacity'], {'next_operation': True, 'next_review_round': True})

    def test_ready_verdict_does_not_hide_round_exhaustion_or_write_status_telemetry(self):
        self.edit_policy(lambda p: p['budget'].update(max_rounds=2))
        self.ready()
        before = (self.host / 'state.json').read_bytes()
        out = self.run_cli('pr-status')
        self.assertEqual(out['recommendation'], 'READY_FOR_NEXT')
        report = self.assert_report(out)
        self.assertEqual(report['ledger']['capacity'],
                         {'next_operation': True, 'next_review_round': False})
        self.assertTrue(report['ledger']['review_rounds']['exhausted'])
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_standalone_and_embedded_preflight_observe_real_work_without_double_charge(self):
        self.merge_ready()
        before = (self.host / 'state.json').read_bytes()
        old_usage = self.state()['budget'].copy()
        preflight = self.run_cli('merge-preflight')
        self.assertEqual(preflight['recommendation'], 'READY_FOR_NEXT')
        self.assertEqual((self.host / 'state.json').read_bytes(), before)
        observed = self.assert_report(preflight)['observed_work']
        self.assertEqual(observed['repository_scan_attempts'], 1)
        self.assertGreater(observed['repository_entries_scanned'], 0)
        self.assertGreater(observed['git_subprocess_attempts'], 0)
        self.assertEqual(observed['merge_tree_write_attempts'], 1)
        executed = self.run_cli('merge-execute')
        self.assertEqual(executed['result'], 'confirmed')
        report = self.assert_report(executed)
        self.assertEqual(report['ledger']['operation_attempts']['used'], old_usage['operations'] + 1)
        self.assertEqual(report['ledger']['review_rounds']['used'], old_usage['rounds'])
        self.assertEqual(report['observed_work']['repository_scan_attempts'], 1)
        self.assertEqual(report['observed_work']['merge_tree_write_attempts'], 1)

    def test_exhausted_merge_query_and_idempotent_recovery_keep_ledger_bytes(self):
        plan = self.merged()
        state = self.state()
        state['budget'] = {'operations': self.policy['budget']['max_operations'],
                           'rounds': self.policy['budget']['max_rounds']}
        write(self.host, 'state.json', state)
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        before = (self.host / 'state.json').read_bytes()
        for command in ['merge-query', 'merge-execute']:
            with self.subTest(command=command):
                out = self.run_cli(command)
                self.assertEqual(out['result'], 'confirmed')
                self.assertEqual(out['actual_target'], plan['candidate'])
                report = self.assert_report(out)
                self.assertEqual(report['ledger']['capacity'],
                                 {'next_operation': False, 'next_review_round': False})
                self.assertGreater(report['observed_work']['git_subprocess_attempts'], 0)
                self.assertEqual(report['observed_work']['merge_tree_write_attempts'], 0)
                self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_exhausted_delivery_query_and_idempotent_recovery_keep_ledger_bytes(self):
        self.merged()
        deployed = self.action('deploy', {'artifact': 'artifact-combined'})
        operation = deployed['result']['operation']
        state = self.state()
        state['budget'] = {'operations': self.policy['budget']['max_operations'],
                           'rounds': self.policy['budget']['max_rounds']}
        write(self.host, 'state.json', state)
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        before = (self.host / 'state.json').read_bytes()
        for command in ['delivery-query', 'delivery-execute']:
            with self.subTest(command=command):
                out = self.run_cli(command, operation=operation)
                self.assertEqual(out['result']['result'], 'confirmed')
                report = self.assert_report(out)
                self.assertEqual(report['ledger']['capacity']['next_operation'], False)
                self.assertGreater(report['observed_work']['repository_scan_attempts'], 0)
                self.assertGreater(report['observed_work']['git_subprocess_attempts'], 0)
                self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_pin_and_state_binding_failures_never_report_usage_as_zero_or_trusted(self):
        self.initialize()
        correct_pin = self.pin
        self.pin = 'bad-pin'
        out = self.run_cli('pr-status', code=2)
        self.assertEqual(self.assert_report(out)['ledger']['status'], 'unknown')
        self.pin = correct_pin
        state = self.state()
        for field, value in [('task_id', 'another-task'), ('policy_sha', 'another-pin')]:
            with self.subTest(field=field):
                write(self.host, 'state.json', dict(state, **{field: value}))
                ledger = self.assert_report(self.run_cli('pr-status', code=2))['ledger']
                self.assertEqual(ledger['status'], 'unknown')
                self.assertIsNone(ledger['operation_attempts']['used'])
                self.assertIsNone(ledger['capacity']['next_operation'])

    def test_missing_and_malformed_usage_are_unknown_but_over_limit_usage_is_preserved(self):
        missing = self.assert_report(self.run_cli('pr-status', code=2))['ledger']
        self.assertEqual(missing['status'], 'unknown')
        self.initialize()
        original = self.state()
        for value in [True, -1, '2', None]:
            with self.subTest(value=value):
                state = dict(original, budget={'operations': value, 'rounds': 1})
                write(self.host, 'state.json', state)
                # Report failures are independent of a command's own validation path.
                args = ['lifecycle', '--host-root', str(self.host), '--policy-sha', self.pin, 'export']
                with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()) as stdout:
                    cli.main()
                ledger = self.assert_report(json.loads(stdout.getvalue()))['ledger']
                self.assertEqual(ledger['status'], 'unknown')
                self.assertIsNone(ledger['operation_attempts']['used'])
        state = dict(original, budget={'operations': 65, 'rounds': 4})
        write(self.host, 'state.json', state)
        ledger = self.assert_report(self.run_cli('pr-status'))['ledger']
        self.assertEqual(ledger['operation_attempts']['used'], 65)
        self.assertEqual(ledger['operation_attempts']['remaining'], 0)
        self.assertEqual(ledger['capacity'], {'next_operation': False, 'next_review_round': False})

    def test_failed_charge_save_reports_snapshot_with_unknown_durability(self):
        self.initialize()
        before = (self.host / 'state.json').read_bytes()
        old_usage = self.state()['budget'].copy()
        args = ['lifecycle', '--host-root', str(self.host), '--policy-sha', self.pin, 'begin-review']
        with patch.object(sys, 'argv', args), patch.object(core, 'save', side_effect=OSError('fixture save failure')):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = cli.main()
        self.assertEqual(code, 2)
        out = json.loads(stdout.getvalue())
        self.assertEqual(out['status'], 'UNKNOWN')
        ledger = self.assert_report(out)['ledger']
        self.assertEqual((ledger['basis'], ledger['durability']), ('session_snapshot', 'unknown'))
        self.assertEqual(ledger['operation_attempts']['used'], old_usage['operations'] + 1)
        self.assertEqual(ledger['review_rounds']['used'], old_usage['rounds'] + 1)
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_malformed_usage_cannot_start_or_persist_a_charge(self):
        self.initialize()
        original = self.state()
        cases = []
        for name in ['operations', 'rounds']:
            for value in [True, False, -1, 0.0, 1.5, '1', None, [], {}]:
                cases.append((name + '=' + repr(value), dict(original['budget'], **{name: value})))
            cases.append(('missing ' + name, {key: value for key, value in original['budget'].items() if key != name}))
        cases += [('missing budget', None), ('nonmapping budget', []), ('null budget', None)]
        for label, usage in cases:
            with self.subTest(usage=label):
                state = copy.deepcopy(original)
                if label == 'missing budget':
                    del state['budget']
                else:
                    state['budget'] = usage
                write(self.host, 'state.json', state)
                before = (self.host / 'state.json').read_bytes()
                for rounds in [0, 1]:
                    with self.subTest(charge_rounds=rounds), core.Session(self.host, self.pin).locked() as session:
                        snapshot = copy.deepcopy(session.state)
                        with patch.object(session, 'persist') as persist:
                            with self.assertRaises(ValueError):
                                session.charge(rounds=rounds)
                            persist.assert_not_called()
                        self.assertEqual(session.state, snapshot)
                self.assertEqual((self.host / 'state.json').read_bytes(), before)
                out = self.run_cli('begin-review', code=2)
                ledger = self.assert_report(out)['ledger']
                self.assertEqual(ledger['status'], 'unknown')
                self.assertIsNone(ledger['capacity']['next_operation'])
                self.assertIsNone(ledger['capacity']['next_review_round'])
                self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_unknown_budget_does_not_block_exact_merge_and_delivery_recovery(self):
        self.merged()
        operation = self.action('deploy', {'artifact': 'artifact-combined'})['result']['operation']
        original = self.state()
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        cases = [{'operations': True, 'rounds': 1}, {'operations': 2, 'rounds': True},
                 {'operations': -1, 'rounds': 1}, {'operations': 2, 'rounds': -1},
                 {'rounds': 1}, {'operations': 2}, None]
        for usage in cases:
            with self.subTest(usage=usage):
                state = copy.deepcopy(original)
                if usage is None:
                    del state['budget']
                else:
                    state['budget'] = usage
                write(self.host, 'state.json', state)
                before = (self.host / 'state.json').read_bytes()
                for command in ['merge-query', 'merge-execute', 'delivery-query', 'delivery-execute']:
                    with self.subTest(command=command):
                        out = self.run_cli(command, operation=operation if command.startswith('delivery-') else None)
                        result = out['result'] if command.startswith('delivery-') else out
                        self.assertEqual(result['result'], 'confirmed')
                        ledger = self.assert_report(out)['ledger']
                        self.assertEqual(ledger['status'], 'unknown')
                        self.assertIsNone(ledger['operation_attempts']['used'])
                        self.assertIsNone(ledger['review_rounds']['used'])
                        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_git_attempts_include_nonzero_timeout_and_oserror(self):
        from interfaces.lifecycle.accounting import InvocationMetrics
        metrics = InvocationMetrics()
        g = merge.LocalGit(self.policy['merge'], metrics=metrics)
        with patch.object(merge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 129, b'', b'failed')):
            self.assertEqual(g.run('cat-file', '-t', 'fixture').returncode, 129)
        for error in [subprocess.TimeoutExpired(['git'], 10), OSError('fixture spawn failure')]:
            with self.subTest(error=type(error).__name__):
                with patch.object(merge.subprocess, 'run', side_effect=error), self.assertRaises(type(error)):
                    g.run('merge-tree', '--write-tree', 'base', 'head')
        observed = metrics.finish()
        self.assertEqual(observed['git_subprocess_attempts'], 3)
        self.assertEqual(observed['merge_tree_write_attempts'], 2)
        self.assertEqual(observed['repository_scan_attempts'], 1)
        self.assertGreater(observed['repository_entries_scanned'], 0)

    def test_cli_git_timeout_reports_attempt_before_error_without_ledger_write(self):
        self.merge_ready()
        before = (self.host / 'state.json').read_bytes()
        args = ['lifecycle', '--host-root', str(self.host), '--policy-sha', self.pin, 'merge-preflight']
        with patch.object(sys, 'argv', args):
            with patch.object(merge.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['git'], 10)):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    code = cli.main()
        self.assertEqual(code, 2)
        report = self.assert_report(json.loads(stdout.getvalue()))
        self.assertEqual(report['ledger']['basis'], 'persisted_ledger')
        self.assertEqual(report['observed_work']['git_subprocess_attempts'], 1)
        self.assertEqual(report['observed_work']['repository_scan_attempts'], 1)
        self.assertGreater(report['observed_work']['repository_entries_scanned'], 0)
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_partial_repository_scan_is_observed_before_rejection(self):
        from interfaces.lifecycle.accounting import InvocationMetrics
        metrics = InvocationMetrics()
        repo = Path(self.f['repo'])
        with patch.object(Path, 'rglob', return_value=iter([repo / 'config', repo / 'blocked-entry'])):
            with patch.object(merge, 'is_link', side_effect=lambda path: path.name == 'blocked-entry'):
                with self.assertRaisesRegex(ValueError, 'symlinks unsupported'):
                    merge.LocalGit(self.policy['merge'], metrics=metrics)
        observed = metrics.finish()
        self.assertEqual(observed['repository_scan_attempts'], 1)
        self.assertEqual(observed['repository_entries_scanned'], 2)
        self.assertEqual(observed['git_subprocess_attempts'], 0)

    def test_elapsed_measurement_is_monotonic_and_finalized_once(self):
        from interfaces.lifecycle.accounting import InvocationMetrics
        metrics = InvocationMetrics(clock=iter([100.0, 100.125]).__next__)
        first = metrics.finish()
        self.assertEqual(first['wall_seconds'], .125)
        self.assertEqual(metrics.finish(), first)


if __name__ == '__main__':
    unittest.main(verbosity=2)
