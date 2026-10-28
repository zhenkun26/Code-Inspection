"""Delivery intent/receipt integrity using the real CLI and self-owned fixtures.

The simulated provider and host ledgers are deliberately corrupted here. This
tests consistency inside the documented trusted-host boundary, not hostile-host
authentication. No remote provider or production state is involved.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from interfaces.lifecycle import delivery
from interfaces.lifecycle.core import Session, digest
from validation.lifecycle.fixture import create, evidence, grant, write
from validation.test_support import temporary_directory

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'interfaces/lifecycle/cli.py'


class DeliveryIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        fixture = create(Path(self.tmp.name) / 'fixture')
        self.host = Path(fixture['host'])
        self.inputs = Path(fixture['inputs'])
        self.pin = fixture['policy_sha']
        provider = self.read('sim-provider.json')
        provider['effective_at'] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        write(self.host, 'sim-provider.json', provider)
        self.policy = self.read('policy.json')
        self.subject = self.read('handoff.json')['subject']
        self.log = []
        self.addCleanup(self.save_log)
        self.cli('init', record='handoff.json')

    def save_log(self):
        destination = os.environ.get('LIFECYCLE_DELIVERY_EVIDENCE_DIR')
        if destination:
            path = Path(destination)
            path.mkdir(parents=True, exist_ok=True)
            write(path, self.id().rsplit('.', 1)[-1] + '.json', {
                'fixture': str(self.host.parent), 'optimized': bool(sys.flags.optimize),
                'kind': 'actual lifecycle CLI; self-owned bare Git and local simulated provider',
                'commands': self.log})

    def read(self, name):
        return json.loads((self.host / name).read_text(encoding='utf-8'))

    def cli(self, command, record=None, operation=None, code=0):
        cmd = [sys.executable] + (['-O'] if sys.flags.optimize else []) + [
            '-B', str(CLI), '--host-root', str(self.host), '--policy-sha', self.pin,
            '--input-root', str(self.inputs)]
        if record:
            cmd += ['--record', record]
        if operation:
            cmd += ['--operation', operation]
        cmd.append(command)
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=15)
        self.log.append(dict(command=cmd, exit_code=result.returncode,
                             stdout=result.stdout, stderr=result.stderr))
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        if code:
            self.assertEqual(out['status'], 'UNKNOWN')
            self.assertEqual(out['recommendation'], 'HOLD')
            self.assertFalse(out['authorizes_action'])
        return out

    def proposal(self, action='rollback', parameters=None):
        if parameters is None:
            parameters = {'artifact': 'old-artifact'}
        write(self.host, 'action.json', dict(schema='delivery-plan/v1',
              action=action, parameters=parameters))
        out = self.cli('delivery-plan', record='action.json')
        stored = self.read('state.json')['delivery']['plans'][out['id']]
        # CLI observations are not canonical plan fields. Check the projection
        # before corrupting the actual stored intent, or extra-field rejection
        # could hide a missing integrity guard.
        self.assertEqual({k: v for k, v in out.items()
                          if k not in {'budget_report', 'authorizes_action'}}, stored)
        return stored

    def approve(self, proposal):
        grant(self.host, self.pin, 'delivery-' + proposal['action'], proposal['id'])

    def execute(self, proposal):
        self.approve(proposal)
        return self.cli('delivery-execute', operation=proposal['id'])

    def store(self, proposal, original=None, rehash=False):
        state = self.read('state.json')
        old_id = original or proposal['id']
        if rehash:
            proposal['id'] = digest({k: v for k, v in proposal.items() if k != 'id'})
        state['delivery']['plans'].pop(old_id)
        state['delivery']['plans'][proposal['id']] = copy.deepcopy(proposal)
        write(self.host, 'state.json', state)
        return proposal

    def rejected_unchanged(self, operation, commands=('delivery-query', 'delivery-execute')):
        before = {name: (self.host / name).read_bytes() for name in
                  ['state.json', 'sim-provider.json', 'authority.json']}
        for command in commands:
            with self.subTest(command=command):
                self.cli(command, operation=operation, code=2)
                self.assertEqual({name: (self.host / name).read_bytes()
                                  for name in before}, before)

    def merged(self):
        self.cli('begin-review')
        def batch(items):
            write(self.inputs, 'batch.json', dict(schema='review-batch/v1',
                  evidence=items, findings=[]))
            self.cli('ingest', record='batch.json')
        batch([evidence(self.inputs, self.subject, c) for c in
               self.policy['checks'] if c['stage'] == 'pr'])
        grant(self.host, self.pin, 'pr-approve', digest(self.subject))
        binding = digest({'repository': self.policy['merge']['repository'],
                          'base': self.subject['base'], 'head': self.subject['head'],
                          'policy_sha': self.pin})
        grant(self.host, self.pin, 'merge-prepare', binding)
        write(self.host, 'merge.json', dict(schema='merge-plan/v1',
              base=self.subject['base'], head=self.subject['head'],
              integration_artifact='artifact-combined'))
        plan = self.cli('merge-prepare', record='merge.json')['plan']
        batch([evidence(self.inputs, plan['subject'], c) for c in
               self.policy['checks'] if c['stage'] == 'merge'])
        grant(self.host, self.pin, 'merge-approve', digest(plan['subject']))
        grant(self.host, self.pin, 'merge-execute', plan['id'])
        self.assertEqual(self.cli('merge-execute')['result'], 'confirmed')

    def test_approved_rollback_mutated_artifact_is_rejected(self):
        proposal = self.proposal()
        self.approve(proposal)
        proposal['parameters']['artifact'] = 'unapproved-incompatible-artifact'
        self.store(proposal)
        self.rejected_unchanged(proposal['id'])

    def test_canonical_payload_fields_and_map_key_are_checked(self):
        proposal = self.proposal()
        self.approve(proposal)
        initial = self.read('state.json')
        provider = self.read('sim-provider.json')
        mutations = {
            'action': lambda p: p.update(action='pause'),
            'policy': lambda p: p.update(policy_sha='unrelated-policy'),
            'subject': lambda p: p['subject'].update(head='unreviewed-head'),
            # A well-shaped identity change isolates digest binding from the
            # independent type/shape guards exercised by the invalid head.
            'subject_worktree': lambda p: p['subject'].update(worktree_digest='different-snapshot'),
            'source': lambda p: p.update(source={'merge_plan': 'unrelated'}),
            'simulation': lambda p: p.update(simulation=False),
            'provider': lambda p: p.update(expected_provider='0' * 64),
            'extra': lambda p: p.update(unrecognized='value'),
            'missing': lambda p: p.pop('parameters'),
            'id': lambda p: p.update(id='1' * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                write(self.host, 'state.json', initial)
                write(self.host, 'sim-provider.json', provider)
                changed = copy.deepcopy(proposal)
                mutate(changed)
                state = self.read('state.json')
                state['delivery']['plans'][proposal['id']] = changed
                write(self.host, 'state.json', state)
                self.rejected_unchanged(proposal['id'])

    def test_rehashed_changed_plan_needs_its_own_authority(self):
        proposal = self.proposal('pause', {})
        self.approve(proposal)
        proposal['action'] = 'rollback'
        proposal['parameters'] = {'artifact': 'old-artifact'}
        changed = self.store(proposal, rehash=True)
        self.rejected_unchanged(changed['id'], commands=('delivery-execute',))

    def test_coherently_rehashed_invalid_action_source_is_rejected(self):
        proposal = self.proposal()
        proposal['source'] = {'merge_plan': 'not-a-rollback-source'}
        changed = self.store(proposal, rehash=True)
        self.approve(changed)
        self.rejected_unchanged(changed['id'])

    def test_rehashed_reapproved_incompatible_rollback_is_rejected(self):
        proposal = self.proposal()
        proposal['parameters']['artifact'] = 'unapproved-incompatible-artifact'
        changed = self.store(proposal, rehash=True)
        self.approve(changed)
        self.rejected_unchanged(changed['id'])

    def test_provider_target_and_cohort_cannot_be_hidden_by_rehash(self):
        proposal = self.proposal()
        original = self.read('state.json')
        provider = self.read('sim-provider.json')
        for field in ['target', 'cohort', 'data_version']:
            with self.subTest(field=field):
                write(self.host, 'state.json', original)
                current = dict(provider, **{field: 'unapproved-' + field})
                write(self.host, 'sim-provider.json', current)
                changed = copy.deepcopy(proposal)
                changed['expected_provider'] = digest(current)
                self.store(changed, rehash=True)
                self.approve(changed)
                self.rejected_unchanged(changed['id'])

    def test_legacy_hash_only_plan_is_rejected_without_migration(self):
        proposal = self.proposal()
        proposal.pop('schema', None)
        # v1 stored plans contained only these fields; retain that exact unsafe form.
        allowed = {'action', 'parameters', 'expected_provider', 'policy_sha',
                   'subject', 'source', 'simulation', 'id'}
        proposal = {k: v for k, v in proposal.items() if k in allowed}
        original = next(iter(self.read('state.json')['delivery']['plans']))
        self.store(proposal, original=original, rehash=True)
        self.approve(proposal)
        self.rejected_unchanged(proposal['id'])

    def test_provider_receipt_fields_are_bound_before_query_or_repeat(self):
        proposal = self.proposal('pause', {})
        self.execute(proposal)
        initial = self.read('sim-provider.json')
        mutations = {
            'parameters': lambda r: r.update(parameters={'artifact': 'different'}),
            'action': lambda r: r.update(action='rollback'),
            'target': lambda r: r.update(target='unrelated-target'),
            'artifact': lambda r: r.update(artifact='unrelated-artifact'),
            'cohort': lambda r: r.update(cohort='unrelated-cohort'),
            'config': lambda r: r.update(config='unrelated-config'),
            'revision': lambda r: r.update(revision=99),
            'simulation': lambda r: r.update(simulation=False),
            'operation': lambda r: r.update(operation='f' * 64),
            'extra': lambda r: r.update(unrecognized=True),
            'empty': lambda r: r.clear(),
            'after-target': lambda r: r['after'].update(target='unrelated-target'),
            'after-data': lambda r: r['after'].update(data_version='unrelated-data'),
            'after-channels': lambda r: r['after'].update(channels={'other': 'artifact'}),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                state = copy.deepcopy(initial)
                mutate(state['operations'][proposal['id']])
                write(self.host, 'sim-provider.json', state)
                self.rejected_unchanged(proposal['id'])

    def test_request_receipt_contradiction_is_rejected_before_shortcut(self):
        proposal = self.proposal('pause', {})
        self.execute(proposal)
        state = self.read('state.json')
        state['delivery']['requests'][proposal['id']]['parameters'] = {'wrong': 'intent'}
        write(self.host, 'state.json', state)
        self.rejected_unchanged(proposal['id'])

    def test_legacy_receipt_is_rejected_without_guessing_bindings(self):
        proposal = self.proposal('pause', {})
        self.execute(proposal)
        state = self.read('sim-provider.json')
        state['operations'][proposal['id']].pop('schema')
        write(self.host, 'sim-provider.json', state)
        self.rejected_unchanged(proposal['id'])

    def test_receipt_operation_mismatch_with_pending_request_is_rejected(self):
        proposal = self.proposal('pause', {})
        self.execute(proposal)
        state = self.read('state.json')
        request = state['delivery']['requests'][proposal['id']]
        request.update(result='pending')
        request.pop('receipt_sha');request.pop('observed_at')
        write(self.host, 'state.json', state)
        provider = self.read('sim-provider.json')
        provider['operations'][proposal['id']]['operation'] = 'f' * 64
        write(self.host, 'sim-provider.json', provider)
        self.rejected_unchanged(proposal['id'])

    def test_numeric_values_cannot_replace_request_or_receipt_boolean_fields(self):
        proposal = self.proposal('flag-set', {'enabled': False, 'cohort': 'internal'})
        self.execute(proposal)
        ledger = self.read('state.json')
        request = ledger['delivery']['requests'][proposal['id']]
        request.update(result='pending')
        request.pop('receipt_sha');request.pop('observed_at')
        original_provider = self.read('sim-provider.json')
        cases = ['request-simulation', 'request-enabled', 'receipt-simulation',
                 'receipt-enabled', 'receipt-paused', 'receipt-revision', 'receipt-parameters']
        for case in cases:
            with self.subTest(field=case):
                state = copy.deepcopy(ledger)
                provider = copy.deepcopy(original_provider)
                request = state['delivery']['requests'][proposal['id']]
                receipt = provider['operations'][proposal['id']]
                if case == 'request-simulation':request['simulation'] = 1
                elif case == 'request-enabled':request['parameters']['enabled'] = 0
                elif case == 'receipt-simulation':receipt['simulation'] = 1
                elif case == 'receipt-enabled':receipt['enabled'] = 0
                elif case == 'receipt-paused':receipt['paused'] = 0
                elif case == 'receipt-revision':receipt['revision'] = True
                else:receipt['parameters']['enabled'] = 0
                write(self.host, 'state.json', state)
                write(self.host, 'sim-provider.json', provider)
                self.rejected_unchanged(proposal['id'])

    def test_receipt_without_a_recorded_attempt_is_not_completion(self):
        proposal = self.proposal('pause', {})
        self.execute(proposal)
        state = self.read('state.json')
        state['delivery']['requests'].pop(proposal['id'])
        write(self.host, 'state.json', state)
        self.rejected_unchanged(proposal['id'])

    def test_provider_state_must_agree_with_latest_receipt(self):
        proposal = self.proposal('pause', {})
        self.execute(proposal)
        state = self.read('sim-provider.json')
        state['paused'] = False
        write(self.host, 'sim-provider.json', state)
        self.rejected_unchanged(proposal['id'])

    def test_query_and_repeat_completed_action_do_not_need_fresh_authority(self):
        proposal = self.proposal()
        completed = self.execute(proposal)
        grant(self.host, self.pin, 'delivery-rollback', proposal['id'], decision='revoke')
        state = self.read('state.json')
        state['subject'] = dict(state['subject'], head='later-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']
        write(self.host, 'state.json', state)
        before = (self.host / 'sim-provider.json').read_bytes()
        for command in ['delivery-query', 'delivery-execute']:
            out = self.cli(command, operation=proposal['id'])
            self.assertEqual(out['result']['result'], 'confirmed')
            self.assertEqual(out['state']['revision'], completed['state']['revision'])
        self.assertEqual((self.host / 'sim-provider.json').read_bytes(), before)

    def test_pending_before_effect_queries_without_replay_and_drift_is_unknown(self):
        proposal = self.proposal('pause', {})
        original_provider = self.read('sim-provider.json')
        self.execute(proposal)
        state = self.read('state.json')
        request = state['delivery']['requests'][proposal['id']]
        request.update(result='pending')
        request.pop('receipt_sha');request.pop('observed_at')
        write(self.host, 'state.json', state)
        write(self.host, 'sim-provider.json', original_provider)
        grant(self.host, self.pin, 'delivery-pause', proposal['id'], decision='revoke')
        ledger = (self.host / 'state.json').read_bytes()
        for expected in ['not_applied', 'unknown']:
            if expected == 'unknown':
                original_provider['target'] = 'drifted-target'
                write(self.host, 'sim-provider.json', original_provider)
            before = (self.host / 'sim-provider.json').read_bytes()
            for command in ['delivery-query', 'delivery-execute']:
                queried = self.cli(command, operation=proposal['id'])
                self.assertEqual(queried['result']['result'], expected)
            self.assertEqual((self.host / 'sim-provider.json').read_bytes(), before)
            self.assertEqual((self.host / 'state.json').read_bytes(), ledger)

    def test_pending_exact_attempt_recovers_lost_response_without_authority(self):
        proposal = self.proposal('pause', {})
        complete = self.execute(proposal)
        state = self.read('state.json')
        request = state['delivery']['requests'][proposal['id']]
        request['result'] = 'pending'
        # A pending attempt retains identity; receipt-only outcome fields are absent.
        for key in ['observed_at', 'receipt_sha']:
            request.pop(key, None)
        write(self.host, 'state.json', state)
        grant(self.host, self.pin, 'delivery-pause', proposal['id'], decision='revoke')
        before = (self.host / 'sim-provider.json').read_bytes()
        for command in ['delivery-query', 'delivery-execute']:
            recovered = self.cli(command, operation=proposal['id'])
            self.assertEqual(recovered['result']['result'], 'confirmed')
            self.assertEqual(recovered['state']['revision'], complete['state']['revision'])
        self.assertEqual((self.host / 'sim-provider.json').read_bytes(), before)

    def test_fresh_rollback_rechecks_compatibility_and_module_capability(self):
        proposal = self.proposal()
        self.approve(proposal)
        initial_state = self.read('state.json')
        initial_provider = self.read('sim-provider.json')
        changes = [lambda p: p.update(enabled=False),
                   lambda p: p['capabilities']['recovery'].update(enabled=False),
                   lambda p: p['rollback_compatibility'].update({'old-artifact': 'schema-2'})]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                write(self.host, 'state.json', initial_state)
                write(self.host, 'sim-provider.json', initial_provider)
                session = Session(self.host, self.pin)
                with session.locked():
                    # Exercise the original service boundary with changed live config,
                    # without relying on CLI policy-pin rejection to stop execution.
                    change(session.policy['delivery'])
                    before = (self.host / 'sim-provider.json').read_bytes()
                    budget = copy.deepcopy(session.state['budget'])
                    with self.assertRaises(ValueError):
                        delivery.execute(session, proposal['id'])
                    self.assertEqual(session.state['budget'], budget)
                    self.assertEqual((self.host / 'sim-provider.json').read_bytes(), before)

    def test_missing_provider_capability_is_rejected_before_charge(self):
        state = self.read('sim-provider.json')
        state['capabilities'].remove('recovery')
        write(self.host, 'sim-provider.json', state)
        before = (self.host / 'state.json').read_bytes()
        write(self.host, 'action.json', dict(schema='delivery-plan/v1',
              action='rollback', parameters={'artifact': 'old-artifact'}))
        self.cli('delivery-plan', record='action.json', code=2)
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_happy_rollback_pause_and_historical_query(self):
        first = self.proposal()
        completed = self.execute(first)
        self.assertEqual(completed['result']['result'], 'confirmed')
        self.assertEqual(completed['state']['artifact'], 'old-artifact')
        self.assertTrue(completed['state']['paused'])
        second = self.proposal('pause', {})
        self.execute(second)
        self.assertEqual(self.cli('delivery-query', operation=first['id'])['result']['result'], 'confirmed')

    def test_happy_deploy_requires_valid_merge_and_records_source(self):
        self.merged()
        proposal = self.proposal('deploy', {'artifact': 'artifact-combined'})
        self.assertEqual(proposal['source']['merge_plan'], self.read('state.json')['merge']['id'])
        result = self.execute(proposal)
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertFalse(result['state']['enabled'])

    def test_delivery_source_requires_valid_historical_merge_attempt(self):
        self.merged()
        proposal = self.proposal('deploy', {'artifact': 'artifact-combined'})
        self.execute(proposal)
        original = self.read('state.json')
        merge_id = proposal['source']['merge_plan']
        for case in ['missing', 'operation', 'schema', 'failed']:
            with self.subTest(field=case):
                state = copy.deepcopy(original)
                if case == 'missing':state['receipts'].pop(merge_id)
                elif case == 'operation':state['receipts'][merge_id]['operation'] = 'f' * 64
                elif case == 'schema':state['receipts'][merge_id].pop('schema')
                else:state['receipts'][merge_id]['result'] = 'failed'
                write(self.host, 'state.json', state)
                self.rejected_unchanged(proposal['id'],
                    commands=('delivery-query', 'delivery-execute', 'delivery-status'))
        # Historical source lookup survives a later current merge/subject; the
        # original plan is retained in the real merge-prepare event.
        state = copy.deepcopy(original)
        state['merge'] = {}
        state['subject'] = dict(state['subject'], head='later-subject')
        write(self.host, 'state.json', state)
        self.assertEqual(self.cli('delivery-query', operation=proposal['id'])['result']['result'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
