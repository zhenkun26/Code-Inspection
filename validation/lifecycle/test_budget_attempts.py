"""Started-attempt accounting and uncertain-save recovery in self-owned fixtures.

These are in-process integration tests with actual bounded state-file saves,
isolated bare Git refs, and the local simulated provider. No external actions.
"""
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
from interfaces.lifecycle import accounting, cli, core, delivery, merge, review
from validation.lifecycle.fixture import create, evidence, grant, write
from validation.test_support import temporary_directory


class BudgetAttempts(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture('initial')

    def fixture(self, label):
        info = create(Path(self.tmp.name) / label)
        self.host, self.inputs = Path(info['host']), Path(info['inputs'])
        self.pin = info['policy_sha']
        self.policy = self.read('policy.json')
        self.subject = self.read('handoff.json')['subject']
        with self.session().locked() as s:
            s.initialize(self.read('handoff.json'))

    def read(self, name='state.json'):
        return json.loads((self.host / name).read_text(encoding='utf-8'))

    def session(self):
        return core.Session(self.host, self.pin)

    def starts(self, state):
        return [event for event in state['events'] if event['action'] == 'attempt-started']

    def report(self, s):
        return accounting.budget_report(s, accounting.InvocationMetrics())

    def begin_review(self, s):
        s.charge(rounds=1, operation='begin-review')
        s.state['round_subject'] = core.digest(s.state['subject'])
        s.event('begin-review', {'subject': s.state['subject']})
        s.persist()

    def merge_ready(self):
        grant(self.host, self.pin, 'pr-approve', core.digest(self.subject))
        with self.session().locked() as s:
            self.begin_review(s)
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, self.subject, c) for c in self.policy['checks'] if c['stage'] == 'pr']})
            self.assertEqual(review.evaluate(s)['recommendation'], 'READY_FOR_NEXT')
            binding = core.digest({'repository': self.policy['merge']['repository'],
                'base': self.subject['base'], 'head': self.subject['head'], 'policy_sha': self.pin})
            grant(self.host, self.pin, 'merge-prepare', binding)
            plan = merge.prepare(s, {'schema': 'merge-plan/v1', 'base': self.subject['base'],
                'head': self.subject['head'], 'integration_artifact': 'artifact-combined'})['plan']
            check = next(c for c in self.policy['checks'] if c['stage'] == 'merge')
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, plan['subject'], check)]})
            grant(self.host, self.pin, 'merge-approve', core.digest(plan['subject']))
            grant(self.host, self.pin, 'merge-execute', plan['id'])
            self.assertEqual(merge.preflight(s)['recommendation'], 'READY_FOR_NEXT')
        return plan

    def delivery_ready(self):
        with self.session().locked() as s:
            plan = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'rollback',
                'parameters': {'artifact': 'old-artifact'}})
        grant(self.host, self.pin, 'delivery-rollback', plan['id'])
        return plan

    def execute(self, kind, s, plan):
        return merge.execute(s) if kind == 'merge' else delivery.execute(s, plan['id'])

    def query(self, kind, s, plan):
        return merge.query(s) if kind == 'merge' else delivery.LocalSimulation(s).query(plan['id'])

    def result(self, kind, value):
        return value['result'] if kind == 'merge' else value['result']['result']

    def assert_started(self, event, sequence, operation, round_delta):
        self.assertEqual(event['action'], 'attempt-started')
        self.assertLessEqual(core.timestamp(event['at']), core.now())
        detail = event['detail']
        self.assertEqual(set(detail), {'schema', 'attempt_id', 'sequence', 'operation', 'round_delta'})
        self.assertEqual(detail['schema'], 'lifecycle-attempt/v1')
        self.assertRegex(detail['attempt_id'], r'^[0-9a-f]{32}$')
        self.assertIs(type(detail['sequence']), int)
        self.assertIs(type(detail['round_delta']), int)
        self.assertEqual((detail['sequence'], detail['operation'], detail['round_delta']),
                         (sequence, operation, round_delta))
        return detail['attempt_id']

    def test_round_arguments_reject_before_memory_disk_or_persist_effects(self):
        invalid = [-2, -1, True, False, 0.0, 1.0, 1.5, 2, '1', None, [], {}]
        with self.session().locked() as s:
            for rounds in invalid:
                with self.subTest(rounds=repr(rounds)):
                    before = copy.deepcopy(s.state)
                    disk = (self.host / 'state.json').read_bytes()
                    with patch.object(s, 'persist', wraps=s.persist) as persist:
                        with self.assertRaises(ValueError):
                            s.charge(rounds=rounds, operation='begin-review')
                        persist.assert_not_called()
                    self.assertEqual(s.state, before)
                    self.assertEqual((self.host / 'state.json').read_bytes(), disk)
                    self.assertIsNone(self.report(s)['current_attempt'])
            for operation in ['', 'arbitrary-command', True, None, [], {}]:
                with self.subTest(operation=repr(operation)):
                    before = copy.deepcopy(s.state)
                    disk = (self.host / 'state.json').read_bytes()
                    with patch.object(s, 'persist', wraps=s.persist) as persist:
                        with self.assertRaises(ValueError):
                            s.charge(operation=operation)
                        persist.assert_not_called()
                    self.assertEqual(s.state, before)
                    self.assertEqual((self.host / 'state.json').read_bytes(), disk)
            for rounds in [0, 1]:
                before = s.state['budget'].copy()
                s.charge(rounds=rounds, operation='begin-review')
                self.assertEqual(s.state['budget'], {'operations': before['operations'] + 1,
                                                    'rounds': before['rounds'] + rounds})

    def test_malformed_event_history_rejects_charge_before_any_session_effect(self):
        original = self.read()
        observed = []
        for label, history in [('missing', None), ('null', None), ('mapping', {})]:
            state = copy.deepcopy(original)
            if label == 'missing': state.pop('events')
            else: state['events'] = history
            write(self.host, 'state.json', state)
            disk = (self.host / 'state.json').read_bytes()
            with self.session().locked() as s:
                before = copy.deepcopy(s.state)
                durability = s.accounting_durability
                error = None
                with patch.object(s, 'persist', wraps=s.persist) as persist:
                    try:
                        s.charge(operation='ingest')
                    except Exception as exc:
                        error = type(exc).__name__
                    observed.append({'history': label, 'error': error,
                        'memory_unchanged': s.state == before,
                        'disk_unchanged': (self.host / 'state.json').read_bytes() == disk,
                        'current_attempt_unchanged': s.current_attempt is None,
                        'durability_unchanged': s.accounting_durability == durability,
                        'persist_calls': persist.call_count})
        expected = [{'history': label, 'error': 'ValueError', 'memory_unchanged': True,
            'disk_unchanged': True, 'current_attempt_unchanged': True,
            'durability_unchanged': True, 'persist_calls': 0}
            for label in ['missing', 'null', 'mapping']]
        self.assertEqual(observed, expected, json.dumps(observed, indent=2))

    def test_charge_saves_counters_and_unique_started_record_once_and_resets_report(self):
        s = self.session()
        identifiers = []
        with s.locked():
            self.assertIsNone(self.report(s)['current_attempt'])
            for rounds in [0, 1]:
                before = s.state['budget'].copy()
                saved = []
                real_save = core.save
                def capture(root, name, state):
                    saved.append(copy.deepcopy(state))
                    return real_save(root, name, state)
                with patch.object(core, 'save', capture):
                    s.charge(rounds=rounds, operation='begin-review')
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0], self.read())
                self.assertEqual(saved[0]['budget'], {'operations': before['operations'] + 1,
                                                    'rounds': before['rounds'] + rounds})
                started = self.starts(saved[0])
                self.assertEqual(len(started), len(identifiers) + 1)
                event = started[-1]
                identifiers.append(self.assert_started(event, before['operations'] + 1, 'begin-review', rounds))
                current = self.report(s)['current_attempt']
                self.assertEqual(current, dict(event['detail'], durability='persisted'))
                current['operation'] = 'response-copy-tampering'
                self.assertEqual(self.starts(s.state)[-1], event)
                self.assertEqual(self.report(s)['current_attempt']['operation'], 'begin-review')
            self.assertEqual(len(set(identifiers)), 2)
        before = (self.host / 'state.json').read_bytes()
        with s.locked():
            self.assertIsNone(s.current_attempt)
            self.assertIsNone(self.report(s)['current_attempt'])
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_failed_ingest_keeps_started_consumption_and_success_links_business_event(self):
        item = evidence(self.inputs, self.subject, self.policy['checks'][0], 'raw-digest-attempt')
        bad = copy.deepcopy(item)
        bad['raw']['sha256'] = '0' * 64
        initial = self.read()
        for count in [1, 2]:
            with self.session().locked() as s:
                with self.assertRaisesRegex(ValueError, 'raw evidence digest mismatch'):
                    review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'evidence': [bad], 'findings': []})
                state = self.read()
                self.assertEqual(state['budget'], dict(initial['budget'], operations=initial['budget']['operations'] + count))
                self.assertEqual(state['evidence'], [])
                self.assertEqual(len(self.starts(state)), count)
                event = self.starts(state)[-1]
                self.assert_started(event, state['budget']['operations'], 'ingest', 0)
                self.assertEqual(self.report(s)['current_attempt'], dict(event['detail'], durability='persisted'))
        with self.session().locked() as s:
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'evidence': [item], 'findings': []})
        state = self.read()
        identifiers = [event['detail']['attempt_id'] for event in self.starts(state)]
        self.assertEqual(len(set(identifiers)), 3)
        self.assertEqual([event['detail']['sequence'] for event in self.starts(state)], [3, 4, 5])
        completed = [event for event in state['events'] if event['action'] == 'ingest']
        self.assertEqual(len(completed), 1)
        self.assertIn('attempt_id', completed[0])
        self.assertEqual(completed[0]['attempt_id'], identifiers[-1])

    def test_ingest_save_fault_keeps_speculative_snapshot_until_valid_reload(self):
        for replaced in [False, True]:
            with self.subTest(replaced=replaced):
                self.fixture('ingest-save-' + str(replaced))
                item = evidence(self.inputs, self.subject, self.policy['checks'][0], 'persist-fault-ci')
                real_save = core.save
                saves = []
                def fail_business_save(root, name, state):
                    saves.append(copy.deepcopy(state))
                    if len(saves) == 2:
                        if replaced: real_save(root, name, state)
                        raise OSError('controlled ingest business save failure')
                    return real_save(root, name, state)
                s = self.session()
                with s.locked():
                    with patch.object(core, 'save', fail_business_save):
                        with self.assertRaisesRegex(OSError, 'ingest business save failure'):
                            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'evidence': [item], 'findings': []})
                    self.assertEqual(s.state, saves[-1])
                    self.assertEqual(s.state['evidence'], [item])
                    self.assertEqual(self.read(), saves[-1] if replaced else saves[0])
                    report = self.report(s)
                    self.assertEqual(report['current_attempt']['durability'], 'unknown')
                    self.assertEqual(report['ledger']['basis'], 'session_snapshot')
                # A failed lock-time identity validation must not clear the
                # existing persistence fence or permit saving its bad snapshot.
                disk = (self.host / 'state.json').read_bytes()
                invalid = self.read()
                invalid['task_id'] = 'another-task'
                write(self.host, 'state.json', invalid)
                with self.assertRaisesRegex(ValueError, 'state/policy mismatch'):
                    with s.locked(): pass
                with self.assertRaises(ValueError): s.persist()
                with self.assertRaises(ValueError): s.charge(operation='ingest')
                (self.host / 'state.json').write_bytes(disk)
                with s.locked():
                    self.assertEqual(s.state, saves[-1] if replaced else saves[0])
                    self.assertIsNone(self.report(s)['current_attempt'])
                self.assertEqual((self.host / 'state.json').read_bytes(), disk)

    def test_last_allowed_round_and_operation_then_denial_leave_ledger_unchanged(self):
        # Change only host-owned fixture policy before creating the Session.
        self.policy['budget'] = {'max_rounds': 2, 'max_operations': 4}
        write(self.host, 'policy.json', self.policy)
        import hashlib
        self.pin = hashlib.sha256((self.host / 'policy.json').read_bytes()).hexdigest()
        state = self.read()
        state['policy_sha'] = self.pin
        write(self.host, 'state.json', state)
        with self.session().locked() as s:
            self.begin_review(s)
            self.assertEqual(s.state['budget'], {'operations': 3, 'rounds': 2})
            before = copy.deepcopy(s.state)
            disk = (self.host / 'state.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'budget exhausted'):
                s.charge(rounds=1, operation='begin-review')
            self.assertEqual(s.state, before)
            self.assertEqual((self.host / 'state.json').read_bytes(), disk)
            # Other work in the last registered round is still permitted.
            s.charge(operation='ingest')
            self.assertEqual(s.state['budget'], {'operations': 4, 'rounds': 2})
            before = copy.deepcopy(s.state)
            disk = (self.host / 'state.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'budget exhausted'):
                s.charge(operation='ingest')
            self.assertEqual(s.state, before)
            self.assertEqual((self.host / 'state.json').read_bytes(), disk)

    def test_cli_begin_review_reports_current_attempt_and_status_does_not_backfill(self):
        args = ['lifecycle', '--host-root', str(self.host), '--policy-sha', self.pin]
        with patch.object(sys, 'argv', args + ['begin-review']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(), 0)
        value = json.loads(output.getvalue())
        event = self.starts(self.read())[-1]
        self.assertEqual(value['budget_report']['current_attempt'], dict(event['detail'], durability='persisted'))
        before = (self.host / 'state.json').read_bytes()
        with patch.object(sys, 'argv', args + ['pr-status']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(), 0)
        self.assertIsNone(json.loads(output.getvalue())['budget_report']['current_attempt'])
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_save_faults_fence_same_session_and_preserve_exact_fresh_recovery(self):
        # A post-replace exception leaves disk outcome uncertain to the caller.
        # Charge-only persisted history has no pending intent: a fresh retry is
        # another charged attempt. A durable pending intent is queried, not run.
        for kind in ['merge', 'delivery']:
            for phase, fail_call in [('charge', 1), ('pending', 2), ('receipt', 3)]:
                for replaced in [False, True]:
                    with self.subTest(kind=kind, phase=phase, replaced=replaced):
                        self.fixture(kind + '-' + phase + '-' + str(replaced))
                        plan = self.merge_ready() if kind == 'merge' else self.delivery_ready()
                        baseline = self.read()
                        baseline_provider = (self.host / 'sim-provider.json').read_bytes()
                        real_save, real_git, real_provider_save = core.save, merge.LocalGit.run, delivery.save
                        calls, effects = [], []
                        def failing_save(root, name, value):
                            calls.append(copy.deepcopy(value))
                            if len(calls) == fail_call:
                                if replaced:
                                    real_save(root, name, value)
                                raise OSError('controlled ' + phase + ' persistence failure')
                            return real_save(root, name, value)
                        def track_git(adapter, *args, **kwargs):
                            if args[0] == 'update-ref': effects.append('cas')
                            return real_git(adapter, *args, **kwargs)
                        def track_provider(root, name, value):
                            effects.append('provider-write')
                            return real_provider_save(root, name, value)
                        with patch.object(merge.LocalGit, 'run', track_git), patch.object(delivery, 'save', track_provider):
                            s = self.session()
                            with s.locked():
                                with patch.object(core, 'save', failing_save):
                                    with self.assertRaisesRegex(OSError, 'controlled ' + phase):
                                        self.execute(kind, s, plan)
                                self.assertEqual(len(calls), fail_call)
                                self.assertEqual(len(effects), int(phase == 'receipt'))
                                if phase == 'pending' and replaced:
                                    requested = [event for event in self.read()['events'] if event['action'] == kind + '-requested']
                                    self.assertEqual(len(requested), 1)
                                    self.assertEqual(requested[0]['detail']['operation'], plan['id'])
                                    self.assertEqual(requested[0]['attempt_id'], self.starts(self.read())[-1]['detail']['attempt_id'])
                                report = self.report(s)
                                self.assertEqual(report['current_attempt']['durability'], 'unknown')
                                self.assertEqual(report['ledger']['durability'], 'unknown')
                                disk = (self.host / 'state.json').read_bytes()
                                provider = (self.host / 'sim-provider.json').read_bytes()
                                memory = copy.deepcopy(s.state)
                                guarded = [lambda: s.charge(operation='ingest'), s.persist,
                                           lambda: self.query(kind, s, plan), lambda: self.execute(kind, s, plan)]
                                if kind == 'merge': guarded.append(lambda: merge.preflight(s))
                                else: guarded.append(lambda: delivery.LocalSimulation(s).apply(plan))
                                for action in guarded:
                                    with self.assertRaises(ValueError): action()
                                self.assertEqual(s.state, memory)
                                self.assertEqual((self.host / 'state.json').read_bytes(), disk)
                                self.assertEqual((self.host / 'sim-provider.json').read_bytes(), provider)
                                self.assertEqual(len(effects), int(phase == 'receipt'))
                            persisted = self.read()
                            pending = phase == 'receipt' or (phase == 'pending' and replaced)
                            charge_persisted = phase != 'charge' or replaced
                            self.assertEqual(persisted['budget']['operations'], baseline['budget']['operations'] + int(charge_persisted))
                            # Reload on this same object must clear the latch only
                            # after the real policy/state read has succeeded.
                            with s.locked():
                                self.assertIsNone(self.report(s)['current_attempt'])
                                before_query = (self.host / 'state.json').read_bytes()
                                queried = self.query(kind, s, plan)
                                self.assertEqual(self.result(kind, queried), 'confirmed' if phase == 'receipt' else 'not_applied')
                                self.assertEqual((self.host / 'state.json').read_bytes(), before_query)
                                recovered = self.execute(kind, s, plan)
                                self.assertEqual(self.result(kind, recovered), 'not_applied' if pending and phase != 'receipt' else 'confirmed')
                                if pending:
                                    self.assertEqual((self.host / 'state.json').read_bytes(), before_query)
                                    self.assertIsNone(self.report(s)['current_attempt'])
                                else:
                                    self.assertEqual(s.state['budget']['operations'], persisted['budget']['operations'] + 1)
                            self.assertEqual(len(effects), int(phase == 'receipt') + int(not pending))
                            if pending and phase != 'receipt':
                                self.assertEqual((self.host / 'sim-provider.json').read_bytes(), baseline_provider)

    def test_lost_action_responses_recover_without_replaying_or_recharging(self):
        for kind in ['merge', 'delivery']:
            for applied in [False, True]:
                with self.subTest(kind=kind, applied=applied):
                    self.fixture('lost-' + kind + '-' + str(applied))
                    plan = self.merge_ready() if kind == 'merge' else self.delivery_ready()
                    baseline = self.read()['budget'].copy()
                    attempts = []
                    real_git, real_apply = merge.LocalGit.run, delivery.LocalSimulation.apply
                    def lost_git(adapter, *args, **kwargs):
                        if args[0] == 'update-ref':
                            attempts.append('cas')
                            if applied: real_git(adapter, *args, **kwargs)
                            raise subprocess.TimeoutExpired(args, 10)
                        return real_git(adapter, *args, **kwargs)
                    def lost_apply(provider, proposal):
                        attempts.append('apply')
                        if applied: real_apply(provider, proposal)
                        raise OSError('controlled lost provider response')
                    with patch.object(merge.LocalGit, 'run', lost_git), patch.object(delivery.LocalSimulation, 'apply', lost_apply):
                        with self.session().locked() as s:
                            if kind == 'delivery':
                                with self.assertRaisesRegex(OSError, 'lost provider response'):
                                    delivery.execute(s, plan['id'])
                            else:
                                merge.execute(s)
                        disk = (self.host / 'state.json').read_bytes()
                        with self.session().locked() as s:
                            expected = 'confirmed' if applied else 'not_applied' if kind == 'merge' else 'unknown'
                            self.assertEqual(self.result(kind, self.query(kind, s, plan)), expected)
                            self.assertEqual(self.result(kind, self.execute(kind, s, plan)), expected)
                            self.assertIsNone(self.report(s)['current_attempt'])
                        self.assertEqual((self.host / 'state.json').read_bytes(), disk)
                        self.assertEqual(len(attempts), 1)
                        self.assertEqual(self.read()['budget'], dict(baseline, operations=baseline['operations'] + 1))

    def test_ledger_size_limit_fails_closed_without_erasing_attempt_or_history(self):
        plan = self.delivery_ready()
        state = self.read()
        state['events'].append({'at': core.now().isoformat(), 'action': 'host-padding', 'detail': {'padding': ''}})
        encode = lambda value: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b'\n'
        state['events'][-1]['detail']['padding'] = 'x' * (1024 * 1024 - 64 - len(encode(state)))
        core.save(self.host, 'state.json', state)
        disk = (self.host / 'state.json').read_bytes()
        provider = (self.host / 'sim-provider.json').read_bytes()
        self.assertEqual(len(disk), 1024 * 1024 - 64)
        with self.session().locked() as s:
            with self.assertRaisesRegex(ValueError, 'ledger limit reached'):
                delivery.execute(s, plan['id'])
            self.assertEqual(s.state['budget']['operations'], state['budget']['operations'] + 1)
            self.assertEqual(len(self.starts(s.state)), len(self.starts(state)) + 1)
            self.assertEqual(self.report(s)['current_attempt']['durability'], 'unknown')
            with self.assertRaises(ValueError): s.persist()
            with self.assertRaises(ValueError): delivery.LocalSimulation(s).apply(plan)
        self.assertEqual((self.host / 'state.json').read_bytes(), disk)
        self.assertEqual((self.host / 'sim-provider.json').read_bytes(), provider)
        with self.session().locked() as s:
            self.assertEqual(self.result('delivery', self.query('delivery', s, plan)), 'not_applied')
        self.assertEqual((self.host / 'state.json').read_bytes(), disk)

    def test_trace_free_legacy_recovery_is_read_only_even_with_unknown_or_exhausted_budget(self):
        merged = self.merge_ready()
        with self.session().locked() as s:
            self.assertEqual(merge.execute(s)['result'], 'confirmed')
        delivered = self.delivery_ready()
        with self.session().locked() as s:
            self.assertEqual(delivery.execute(s, delivered['id'])['result']['result'], 'confirmed')
        legacy = self.read()
        legacy['events'] = [{key: value for key, value in event.items() if key != 'attempt_id'}
            for event in legacy['events'] if event['action'] not in {'attempt-started', 'merge-requested', 'delivery-requested'}]
        for usage in [legacy['budget'], {'operations': 60, 'rounds': 3}, {'operations': True, 'rounds': 1}, None]:
            with self.subTest(usage=usage):
                state = copy.deepcopy(legacy)
                if usage is None: state.pop('budget')
                else: state['budget'] = usage
                write(self.host, 'state.json', state)
                disk = (self.host / 'state.json').read_bytes()
                provider = (self.host / 'sim-provider.json').read_bytes()
                with self.session().locked() as s:
                    for kind, plan in [('merge', merged), ('delivery', delivered)]:
                        self.assertEqual(self.result(kind, self.query(kind, s, plan)), 'confirmed')
                        self.assertEqual(self.result(kind, self.execute(kind, s, plan)), 'confirmed')
                        self.assertIsNone(self.report(s)['current_attempt'])
                self.assertEqual((self.host / 'state.json').read_bytes(), disk)
                self.assertEqual((self.host / 'sim-provider.json').read_bytes(), provider)

    def test_absent_or_unbound_receipt_does_not_become_completion_from_trace(self):
        plan = self.merge_ready()
        with self.session().locked() as s:
            s.charge(operation='merge-execute')
        adapter = merge.LocalGit(self.policy['merge'])
        self.assertEqual(adapter.run('update-ref', adapter.ref, plan['candidate'], plan['base']).returncode, 0)
        disk = (self.host / 'state.json').read_bytes()
        with self.session().locked() as s:
            self.assertEqual(merge.query(s)['result'], 'unknown')
            with self.assertRaises(ValueError): merge.execute(s)
        self.assertEqual((self.host / 'state.json').read_bytes(), disk)
        self.fixture('unbound-delivery')
        proposal = self.delivery_ready()
        with self.session().locked() as s:
            delivery.execute(s, proposal['id'])
        state = self.read()
        state['delivery']['requests'].pop(proposal['id'])
        write(self.host, 'state.json', state)
        disk = (self.host / 'state.json').read_bytes()
        provider = (self.host / 'sim-provider.json').read_bytes()
        with self.session().locked() as s:
            with self.assertRaises(ValueError): self.query('delivery', s, proposal)
            with self.assertRaises(ValueError): delivery.execute(s, proposal['id'])
        self.assertEqual((self.host / 'state.json').read_bytes(), disk)
        self.assertEqual((self.host / 'sim-provider.json').read_bytes(), provider)


if __name__ == '__main__':
    unittest.main(verbosity=2)
