"""Injected host delivery, with self-owned offline Git and synthetic providers.

The provider below is deliberately a test double. It has no network, shell,
credentials, production endpoint or authenticated build/health claim. Actual
bounded artifact bytes, conditional writes and durable operation identities are
exercised locally; ArtifactContent helpers are composed, never inherited.
"""
import copy
from concurrent.futures import CancelledError
from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import artifacts, core, delivery, merge
try:
    from interfaces.lifecycle import host_delivery
except ImportError:
    host_delivery = None
from validation.lifecycle import test_artifact_content as content_tests
from validation.lifecycle import test_build_provenance as provenance_tests
from validation.lifecycle.fixture import grant, write

PROVIDER_ID = 'synthetic-owned-provider'
VERSION = 'fixture-version-1'
TARGET = 'self-owned-isolated-target'
GUARANTEES = ['conditional_apply', 'durable_idempotency']
DATA = content_tests.DATA
sha = content_tests.sha


def selection():
    return {'schema': 'host-delivery-policy/v1', 'provider_id': PROVIDER_ID,
            'provider_version': VERSION, 'target': TARGET, 'max_age_seconds': 300,
            'guarantees': list(GUARANTEES)}


def state_digest(value):
    return core.digest({k: v for k, v in value.items() if k != 'observed_at'})


class SyntheticProvider:
    """Local conditional adapter with durable results independent of responses."""
    def __init__(self):
        self.state = {'schema': 'host-delivery-state/v1', 'provider_id': PROVIDER_ID,
            'provider_version': VERSION, 'target': TARGET, 'revision': 'opaque-before',
            'artifact': 'synthetic-previous-artifact', 'cohort': 'previous-cohort',
            'config': 'actual-provider-config-before', 'data_version': 'schema-1',
            'enabled': True, 'paused': True,
            'capabilities': ['deployment', 'recovery'] + list(GUARANTEES),
            'observed_at': core.now().isoformat()}
        self.results = {}
        self.reads, self.applies, self.queries = [], [], []
        self.read_hook = self.apply_hook = self.query_hook = None
        self.result_mutator = None
        self.error = None
        self.error_after_effect = False
        self.query_status = 'not_applied'

    def read(self, context):
        self.reads.append(copy.deepcopy(context))
        value = copy.deepcopy(self.state)
        if self.read_hook is not None:
            replacement = self.read_hook(context, value)
            if replacement is not None:
                value = replacement
        return value

    def result(self, request, status):
        return {'schema': 'host-delivery-result/v1', 'operation': request['operation'],
            'request_digest': request['request_digest'], 'provider_id': PROVIDER_ID,
            'provider_version': VERSION, 'target': TARGET, 'status': status,
            'observed_at': core.now().isoformat()}

    def apply(self, request, snapshot):
        original = copy.deepcopy(request)
        self.applies.append((original, snapshot))
        if self.error is not None and not self.error_after_effect:
            raise self.error
        if self.apply_hook is not None:
            self.apply_hook(request, snapshot)
        # The adapter must reject a change between host read and conditional apply.
        if original['expected_provider'] != state_digest(self.state):
            result = self.result(original, 'not_applied')
        elif original['operation'] in self.results:
            result = copy.deepcopy(self.results[original['operation']])
        else:
            after = copy.deepcopy(self.state)
            after.update(copy.deepcopy(original['effect']))
            after.update(revision='opaque-after-' + str(len(self.results) + 1),
                         config='actual-provider-config-after-' + str(len(self.results) + 1),
                         observed_at=core.now().isoformat())
            self.state = after
            result = self.result(original, 'confirmed')
            result.update(after=copy.deepcopy(after), consumed_content={
                'sha256': sha(snapshot.data), 'size_bytes': len(snapshot.data)})
        self.results[original['operation']] = copy.deepcopy(result)
        if self.error is not None:
            raise self.error
        if self.result_mutator is not None:
            self.result_mutator(result)
        return result

    def query(self, request):
        original = copy.deepcopy(request)
        self.queries.append(original)
        if self.query_hook is not None:
            self.query_hook(request)
        result = copy.deepcopy(self.results.get(original['operation']))
        if result is None:
            result = self.result(original, self.query_status)
        if self.result_mutator is not None:
            self.result_mutator(result)
        return result


class HostDelivery(unittest.TestCase):
    read = content_tests.ArtifactContent.read
    descriptor = content_tests.ArtifactContent.descriptor
    ready_pr = content_tests.ArtifactContent.ready_pr
    prepare_merge = content_tests.ArtifactContent.prepare_merge
    merged = content_tests.ArtifactContent.merged
    execute_merge = content_tests.ArtifactContent.execute_merge

    def setUp(self):
        self.assertIsNotNone(host_delivery, 'explicit host delivery is not implemented')
        content_tests.ArtifactContent.setUp(self)
        self.provider = SyntheticProvider()
        self.registry = {PROVIDER_ID: self.provider}

    def session(self):
        return core.Session(self.host, self.pin, delivery_providers=self.registry)

    def start(self, required=None):
        self.policy['delivery_provider'] = selection()
        content_tests.ArtifactContent.start(self, required=required)

    def plan(self, source=None, action='deploy', approve=True, **extra):
        record = {'schema': 'delivery-plan/v1', 'action': action,
                  'parameters': {'artifact': 'sha256:' + sha(DATA)}}
        if action == 'rollback':
            record.update(artifact_content=self.descriptor(), artifact_source_merge=source['id'])
        record.update(extra)
        with self.session().locked() as s:
            proposal = delivery.plan(s, record)
        if approve:
            grant(self.host, self.pin, 'delivery-' + action, proposal['id'])
        return proposal

    def ready(self, action='deploy', approve=True):
        self.start()
        source = self.merged()
        return self.plan(source, action, approve)

    def execute(self, proposal):
        with self.session().locked() as s:
            return delivery.execute(s, proposal['id'])

    def query(self, proposal):
        with self.session().locked() as s:
            return delivery.query(s, proposal['id'])

    def status(self, result):
        return result['result']['status']

    def disk(self):
        return {name: (self.host / name).read_bytes() for name in
                ('state.json', 'authority.json', 'sim-provider.json')}

    def reject(self, action):
        before, applies = self.disk(), len(self.provider.applies)
        with self.assertRaises((ValueError, OSError)):
            action()
        self.assertEqual(self.disk(), before, 'rejection must not charge or persist intent')
        self.assertEqual(len(self.provider.applies), applies, 'rejection reached apply')

    def store(self, proposal, original=None, rehash=False):
        state = self.read()
        old = original or proposal['id']
        if rehash:
            proposal['id'] = core.digest({k: v for k, v in proposal.items() if k != 'id'})
        state['delivery']['plans'].pop(old)
        state['delivery']['plans'][proposal['id']] = copy.deepcopy(proposal)
        write(self.host, 'state.json', state)
        return proposal

    def assert_pending(self, proposal):
        attempt = self.read()['delivery']['requests'][proposal['id']]
        self.assertNotEqual(attempt.get('result'), 'confirmed')
        self.assertNotEqual(attempt.get('status'), 'confirmed')
        return attempt

    def test_deploy_passes_exact_context_envelope_and_immutable_actual_bytes(self):
        proposal = self.ready()
        before_budget = self.read()['budget']
        result = self.execute(proposal)
        self.assertEqual(self.status(result), 'confirmed')
        self.assertIs(result['simulation'], False)
        self.assertEqual(result['current_state_status'], 'UNKNOWN')
        self.assertIsNone(result['state'], 'apply receipt is not a current global state read')
        self.assertEqual(len(self.provider.applies), 1)
        request, snapshot = self.provider.applies[0]
        self.assertEqual(self.provider.reads[0], {'schema': 'host-delivery-context/v1',
            'task_id': self.policy['task_id'], 'policy_sha': self.pin,
            'provider_id': PROVIDER_ID, 'provider_version': VERSION, 'target': TARGET})
        self.assertEqual(set(request), {'schema', 'operation', 'proposal_id', 'task_id',
            'policy_sha', 'provider_id', 'provider_version', 'target', 'action',
            'parameters', 'expected_provider', 'expected_state', 'effect',
            'artifact_content', 'requested_at', 'request_digest'})
        self.assertEqual(request['schema'], 'host-delivery-request/v1')
        self.assertEqual(request['operation'], proposal['id'])
        self.assertEqual(request['proposal_id'], proposal['id'])
        self.assertEqual(request['policy_sha'], self.pin)
        self.assertEqual(request['artifact_content'], proposal['artifact_content'])
        self.assertEqual(request['request_digest'], core.digest({
            k: v for k, v in request.items() if k != 'request_digest'}))
        self.assertEqual(request['expected_provider'], state_digest(request['expected_state']))
        self.assertIsInstance(snapshot, artifacts.Snapshot)
        self.assertIs(type(snapshot.data), bytes)
        self.assertEqual((snapshot.data, snapshot.sha256, snapshot.size_bytes), (DATA, sha(DATA), len(DATA)))
        with self.assertRaises(FrozenInstanceError):
            snapshot.data = b'replaced'
        after = result['result']['after']
        self.assertEqual(after['artifact'], 'sha256:' + sha(DATA))
        self.assertEqual(after['target'], TARGET)
        self.assertEqual(after['cohort'], self.policy['delivery']['initial_cohort'])
        self.assertEqual(after['config'], 'actual-provider-config-after-1')
        self.assertEqual(after['revision'], 'opaque-after-1')
        self.assertIs(after['enabled'], False)
        self.assertIs(after['paused'], False)
        self.assertEqual(result['result']['consumed_content'], {'sha256': sha(DATA), 'size_bytes': len(DATA)})
        self.assertEqual(self.read()['budget'], dict(before_budget, operations=before_budget['operations'] + 1))
        with self.session().locked() as s:
            checks = {item['id']: item for item in delivery.evaluate(s)['checks']}
        self.assertEqual(checks['deployment']['status'], 'PASS')
        self.assertEqual(checks['observation']['status'], 'UNKNOWN')

    def test_rollback_preserves_provider_cohort_data_and_consumes_source_bytes(self):
        proposal = self.ready('rollback')
        result = self.execute(proposal)
        self.assertEqual(self.status(result), 'confirmed')
        after = result['result']['after']
        self.assertEqual(after['cohort'], 'previous-cohort')
        self.assertEqual(after['data_version'], 'schema-1')
        self.assertEqual(after['config'], 'actual-provider-config-after-1')
        self.assertIs(after['enabled'], False)
        self.assertIs(after['paused'], True)
        self.assertEqual(self.provider.applies[0][1].data, DATA)

    def test_policy_is_exact_and_cannot_supply_dynamic_code_or_endpoint(self):
        self.start()
        changes = {'schema': 'other', 'provider_id': '', 'provider_version': '',
            'target': '', 'max_age_seconds': True, 'guarantees': GUARANTEES + GUARANTEES,
            'command': 'echo untrusted', 'url': 'https://invalid.example/no-network'}
        with self.session().locked() as s:
            good = copy.deepcopy(s.policy['delivery_provider'])
            for field, value in changes.items():
                with self.subTest(field=field):
                    s.policy['delivery_provider'] = dict(good, **{field: value})
                    with self.assertRaises(ValueError):
                        host_delivery.config(s)
            for value in (0, -1, 1.5):
                s.policy['delivery_provider'] = dict(good, max_age_seconds=value)
                with self.assertRaises(ValueError):
                    host_delivery.config(s)
            s.policy['delivery_provider'] = dict(good, guarantees=list(reversed(GUARANTEES)))
            self.assertEqual(set(host_delivery.config(s)['guarantees']), set(GUARANTEES))
        self.assertEqual(self.provider.applies, [])

    def test_registry_requires_explicit_callable_object_and_selected_identity(self):
        self.start()
        for registry in ({PROVIDER_ID: 'a-command'}, {PROVIDER_ID: lambda: None}, []):
            with self.subTest(registry=type(registry).__name__), self.assertRaises(ValueError):
                core.Session(self.host, self.pin, delivery_providers=registry)
        self.registry = {'different-provider': self.provider}
        self.reject(lambda: self.plan(action='rollback', source={'id': 'f' * 64}))
        self.assertEqual(self.provider.reads, [])

    def test_missing_registry_refuses_before_read_charge_or_apply(self):
        self.start()
        self.registry = {}
        self.reject(lambda: self.plan(action='rollback', source={'id': 'f' * 64}))
        self.assertEqual(self.provider.reads, [])
        with patch.object(delivery.LocalSimulation, 'read', side_effect=AssertionError('simulation fallback')):
            for operation in ('f' * 64, 'not-an-operation'):
                with self.subTest(operation=operation):
                    for action in (delivery.query, delivery.execute):
                        def attempt():
                            with self.session().locked() as s:
                                action(s, operation)
                        self.reject(attempt)

    def test_only_deploy_and_rollback_are_supported_real_actions(self):
        self.start()
        parameters = {'pause': {}, 'flag-set': {'enabled': False, 'cohort': 'internal'},
            'promote': {'artifact': 'a', 'channel': 'stable'},
            'forward-fix': {'finding_ids': ['one'], 'handoff_ref': 'owned'}}
        for action, value in parameters.items():
            with self.subTest(action=action):
                def attempt():
                    with self.session().locked() as s:
                        delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': action,
                                          'parameters': value})
                with patch.object(self.provider, 'read', side_effect=ValueError('unexpected read')) as read:
                    self.reject(attempt)
                    read.assert_not_called()

    def test_actual_content_is_required_even_when_optional_artifact_policy_allows_labels(self):
        self.start(required=[])
        self.merged(content=False)
        def attempt():
            with self.session().locked() as s:
                delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                    'parameters': {'artifact': 'legacy-combination-artifact'}})
        self.reject(attempt)

    def test_state_identity_freshness_capabilities_and_exact_types_fail_closed(self):
        self.start()
        source = self.merged()
        good = copy.deepcopy(self.provider.state)
        changes = {'schema': 'other', 'provider_id': 'other', 'provider_version': 'other',
            'target': 'other', 'revision': 1, 'enabled': 1, 'paused': 0,
            'observed_at': (core.now() - timedelta(hours=1)).isoformat(),
            'capabilities': ['deployment', 'recovery', 'conditional_apply'],
            'extra': 'unknown'}
        for field, value in changes.items():
            with self.subTest(field=field):
                self.provider.state = dict(good, **{field: value})
                self.reject(lambda: self.plan(source))
        self.provider.state = dict(good, observed_at=(core.now() + timedelta(hours=1)).isoformat())
        self.reject(lambda: self.plan(source))
        for capability in ('deployment', 'conditional_apply', 'durable_idempotency'):
            self.provider.state = dict(good, capabilities=[c for c in good['capabilities'] if c != capability])
            self.reject(lambda: self.plan(source))

    def test_execute_rechecks_current_state_and_selected_capability_before_charge(self):
        proposal = self.ready()
        good = copy.deepcopy(self.provider.state)
        for field, value in [('target', 'drifted'), ('revision', 'externally-changed'),
                             ('data_version', 'schema-2'), ('capabilities', list(GUARANTEES))]:
            with self.subTest(field=field):
                self.provider.state = dict(good, **{field: value})
                self.reject(lambda: self.execute(proposal))
        self.provider.state = good
        with self.session().locked() as s:
            s.policy['delivery']['capabilities']['deployment']['enabled'] = False
            self.reject(lambda: delivery.execute(s, proposal['id']))

    def test_authority_missing_revoked_expired_or_for_other_plan_never_applies(self):
        proposal = self.ready(approve=False)
        self.reject(lambda: self.execute(proposal))
        grant(self.host, self.pin, 'delivery-deploy', 'f' * 64)
        self.reject(lambda: self.execute(proposal))
        grant(self.host, self.pin, 'delivery-deploy', proposal['id'])
        grant(self.host, self.pin, 'delivery-deploy', proposal['id'], decision='revoke')
        self.reject(lambda: self.execute(proposal))
        authority = self.read('authority.json')
        for item in authority['decisions']:
            item['decision'] = 'approve'
            item['expires_at'] = (core.now() - timedelta(seconds=1)).isoformat()
        write(self.host, 'authority.json', authority)
        self.reject(lambda: self.execute(proposal))

    def test_plan_digest_map_key_provider_and_content_bindings_are_immutable(self):
        proposal = self.ready()
        original = self.read()
        mutations = {'id': lambda p: p.update(id='f' * 64),
            'action': lambda p: p.update(action='rollback'),
            'policy': lambda p: p.update(policy_sha='f' * 64),
            'provider': lambda p: p.update(expected_provider='f' * 64),
            'artifact': lambda p: p['parameters'].update(artifact='forged-label'),
            'content': lambda p: p['artifact_content']['proof'].update(sha256='f' * 64),
            'source': lambda p: p['source'].update(merge_plan='f' * 64),
            'extra': lambda p: p.update(url='https://invalid.example')}
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                write(self.host, 'state.json', original)
                changed = copy.deepcopy(proposal)
                mutate(changed)
                self.store(changed, original=proposal['id'])
                self.reject(lambda: self.execute(proposal))
        write(self.host, 'state.json', original)
        changed = copy.deepcopy(proposal)
        changed['parameters']['artifact'] = 'sha256:' + 'f' * 64
        self.store(changed, rehash=True)
        self.reject(lambda: self.execute(changed))
        write(self.host, 'state.json', original)
        changed = copy.deepcopy(proposal)
        changed['effect']['enabled'] = 0
        self.store(changed, rehash=True)
        grant(self.host, self.pin, 'delivery-deploy', changed['id'])
        self.reject(lambda: self.execute(changed))
        write(self.host, 'state.json', original)
        changed = copy.deepcopy(proposal)
        changed['provider_snapshot']['config'] = 'new-actual-provider-config'
        self.provider.state['config'] = 'new-actual-provider-config'
        changed['expected_provider'] = state_digest(changed['provider_snapshot'])
        self.store(changed, rehash=True)
        self.reject(lambda: self.execute(changed))

    def test_forged_hash_bytes_and_rehashed_source_association_are_not_delivery(self):
        self.start()
        source = self.merged()
        forged = self.descriptor(data=b'bytes that are absent')
        self.reject(lambda: self.plan(source, 'rollback', artifact_content=forged))
        proposal = self.plan(source)
        self.artifact.unlink()
        self.reject(lambda: self.execute(proposal))
        self.artifact.write_bytes(DATA)
        changed = copy.deepcopy(proposal)
        changed['artifact_content']['proof']['source_subject_digest'] = 'f' * 64
        self.store(changed, rehash=True)
        grant(self.host, self.pin, 'delivery-deploy', changed['id'])
        self.reject(lambda: self.execute(changed))

    def test_callback_mutation_cannot_change_persisted_context_request_or_result(self):
        proposal = self.ready()
        self.provider.read_hook = lambda context, value: context.clear()
        self.provider.apply_hook = lambda request, snapshot: request.clear()
        self.assertEqual(self.status(self.execute(proposal)), 'confirmed')
        request = self.provider.applies[0][0]
        self.assertEqual(request['policy_sha'], self.pin)
        self.assertEqual(request['target'], TARGET)
        self.provider.state['target'] = 'later-target'
        self.provider.results[proposal['id']]['after']['artifact'] = 'later-mutation'
        self.assertEqual(self.status(self.query(proposal)), 'confirmed')
        self.assertEqual(self.query(proposal)['result']['after']['artifact'], 'sha256:' + sha(DATA))

    def test_replaced_file_after_capture_consumes_original_snapshot_once(self):
        proposal = self.ready()
        real_fresh = artifacts.fresh
        def replace_after_capture(*args, **kwargs):
            snapshot = real_fresh(*args, **kwargs)
            self.artifact.write_bytes(content_tests.CHANGED)
            return snapshot
        with patch.object(artifacts, 'fresh', replace_after_capture):
            result = self.execute(proposal)
        self.assertEqual(self.status(result), 'confirmed')
        self.assertEqual(self.artifact.read_bytes(), content_tests.CHANGED)
        self.assertEqual(self.provider.applies[0][1].data, DATA)

    def test_provider_conditional_apply_rejects_drift_after_host_read(self):
        proposal = self.ready()
        def drift(request, snapshot):
            self.provider.state['revision'] = 'changed-after-host-read'
        self.provider.apply_hook = drift
        result = self.execute(proposal)
        self.assertEqual(self.status(result), 'not_applied')
        self.assertEqual(self.provider.state['artifact'], 'synthetic-previous-artifact')
        self.assertEqual(len(self.provider.applies), 1)
        before = self.disk()
        self.assertEqual(self.status(self.execute(proposal)), 'not_applied')
        self.assertEqual(self.disk(), before)
        self.assertEqual(len(self.provider.applies), 1)

    def invalid_results(self, mutations):
        proposal = self.ready()
        baseline = self.read()
        provider_state = copy.deepcopy(self.provider.state)
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                write(self.host, 'state.json', baseline)
                self.provider.state = copy.deepcopy(provider_state)
                self.provider.results.clear()
                self.provider.result_mutator = mutate
                try:
                    result = self.execute(proposal)
                except ValueError:
                    pass
                else:
                    self.assertNotEqual(self.status(result), 'confirmed')
                self.assert_pending(proposal)
                self.assertEqual(len(self.provider.applies), 1)
                self.provider.applies.clear()

    def test_result_must_bind_exact_operation_digest_provider_version_and_target(self):
        self.invalid_results({field: (lambda r, field=field: r.update(**{field: 'wrong'}))
            for field in ('operation', 'request_digest', 'provider_id', 'provider_version', 'target')})

    def test_confirmed_result_requires_actual_after_effect_and_exact_consumed_bytes(self):
        self.invalid_results({
            'artifact': lambda r: r['after'].update(artifact='forged'),
            'target': lambda r: r['after'].update(target='wrong'),
            'cohort': lambda r: r['after'].update(cohort='wrong'),
            'data': lambda r: r['after'].update(data_version='schema-2'),
            'enabled': lambda r: r['after'].update(enabled=True),
            'paused': lambda r: r['after'].update(paused=True),
            'revision': lambda r: r['after'].update(revision='opaque-before'),
            'config': lambda r: r['after'].update(config=''),
            'consumed-sha': lambda r: r['consumed_content'].update(sha256='f' * 64),
            'consumed-size': lambda r: r['consumed_content'].update(size_bytes=True),
            'consumed-missing': lambda r: r.pop('consumed_content'),
            'after-missing': lambda r: r.pop('after')})

    def test_result_exact_schema_status_and_time_cannot_forge_confirmation(self):
        self.invalid_results({
            'schema': lambda r: r.update(schema='other'),
            'unknown-field': lambda r: r.update(raw_provider_output='secret'),
            'status': lambda r: r.update(status='success'),
            'future': lambda r: r.update(observed_at=(core.now() + timedelta(hours=1)).isoformat()),
            'old': lambda r: r.update(observed_at=(core.now() - timedelta(hours=1)).isoformat()),
            'after-future': lambda r: r['after'].update(observed_at=(core.now() + timedelta(hours=1)).isoformat()),
            'after-type': lambda r: r['after'].update(enabled=0)})

    def test_pending_and_all_nonconfirmed_results_are_query_only_without_recharge(self):
        proposal = self.ready()
        self.provider.error = OSError('controlled lost response')
        try:
            self.execute(proposal)
        except ValueError:
            pass
        self.provider.error = None
        pending = self.read()
        provider_state = copy.deepcopy(self.provider.state)
        initial_applies = copy.deepcopy(self.provider.applies)
        for status in ('pending', 'unknown', 'not_applied', 'failed'):
            with self.subTest(status=status):
                write(self.host, 'state.json', pending)
                self.provider.state = copy.deepcopy(provider_state)
                self.provider.applies = copy.deepcopy(initial_applies)
                self.provider.results.clear()
                self.provider.query_status = status
                before_budget = self.read()['budget']
                before_provider = copy.deepcopy(self.provider.state)
                self.assertEqual(self.status(self.query(proposal)), status)
                self.assertEqual(self.status(self.execute(proposal)), status)
                self.assertEqual(self.read()['budget'], before_budget)
                self.assertEqual(self.provider.state, before_provider)
                self.assertEqual(len(self.provider.applies), 1)

    def test_lost_response_before_or_after_effect_recovers_exactly_once(self):
        proposal = self.ready()
        baseline, provider_state = self.read(), copy.deepcopy(self.provider.state)
        for applied in (False, True):
            with self.subTest(applied=applied):
                write(self.host, 'state.json', baseline)
                self.provider.state = copy.deepcopy(provider_state)
                self.provider.results.clear()
                self.provider.applies.clear()
                self.provider.error = OSError('controlled lost response')
                self.provider.error_after_effect = applied
                try:
                    result = self.execute(proposal)
                except ValueError:
                    pass
                else:
                    self.assertNotEqual(self.status(result), 'confirmed')
                self.assert_pending(proposal)
                self.provider.error = None
                expected = 'confirmed' if applied else 'not_applied'
                self.assertEqual(self.status(self.query(proposal)), expected)
                before = self.disk()
                self.assertEqual(self.status(self.execute(proposal)), expected)
                self.assertEqual(self.disk(), before)
                self.assertEqual(len(self.provider.applies), 1)
                self.assertEqual(self.read()['budget']['operations'], baseline['budget']['operations'] + 1)

    def test_callback_errors_are_sanitized_without_recording_untrusted_details(self):
        proposal = self.ready()
        secret = 'secret-token=/tmp/private-provider-output'
        self.provider.error = RuntimeError(secret)
        try:
            result = self.execute(proposal)
        except ValueError as exc:
            self.assertNotIn(secret, str(exc))
        else:
            self.assertNotIn(secret, str(result))
            self.assertNotEqual(self.status(result), 'confirmed')
        self.assertNotIn(secret.encode(), (self.host / 'state.json').read_bytes())
        self.assert_pending(proposal)
        self.provider.error = None
        def failed_query(request):
            raise RuntimeError(secret)
        self.provider.query_hook = failed_query
        result = self.query(proposal)
        self.assertEqual(self.status(result), 'unknown')
        self.assertNotIn(secret, str(result))
        self.assertNotIn(secret.encode(), (self.host / 'state.json').read_bytes())

    def test_cancellation_propagates_retains_pending_and_recovers_without_reapply(self):
        proposal = self.ready()
        self.provider.error = CancelledError('controlled cancellation')
        self.provider.error_after_effect = True
        with self.assertRaises(CancelledError):
            self.execute(proposal)
        self.assertEqual(self.assert_pending(proposal)['result'], 'pending')
        self.provider.error = None
        self.assertEqual(self.status(self.query(proposal)), 'confirmed')
        before = self.disk()
        self.assertEqual(self.status(self.execute(proposal)), 'confirmed')
        self.assertEqual(len(self.provider.applies), 1)
        self.assertEqual(self.disk(), before)

    def test_unresolved_attempt_blocks_status_until_terminal_recovery(self):
        self.policy['delivery']['capabilities']['observation']['required'] = False
        first = self.ready()
        self.assertEqual(self.status(self.execute(first)), 'confirmed')
        with self.session().locked() as s:
            self.assertEqual(delivery.evaluate(s)['recommendation'], 'READY_FOR_NEXT')
        second = self.plan()
        self.assertNotEqual(first['id'], second['id'])
        self.provider.error = OSError('controlled loss before effect')
        self.assertEqual(self.status(self.execute(second)), 'unknown')
        saved = self.read()
        before_budget = copy.deepcopy(saved['budget'])
        before_applies = len(self.provider.applies)
        for status in ('pending', 'unknown'):
            with self.subTest(status=status):
                current = copy.deepcopy(saved)
                current['delivery']['requests'][second['id']]['result'] = status
                write(self.host, 'state.json', current)
                with self.session().locked() as s:
                    report = delivery.evaluate(s)
                self.assertEqual(report['recommendation'], 'HOLD')
                self.assertEqual(self.read()['budget'], before_budget)
                self.assertEqual(len(self.provider.applies), before_applies)
        write(self.host, 'state.json', saved)
        self.provider.error = None
        self.provider.query_status = 'not_applied'
        self.assertEqual(self.status(self.query(second)), 'not_applied')
        with self.session().locked() as s:
            self.assertEqual(delivery.evaluate(s)['recommendation'], 'READY_FOR_NEXT')
        self.assertEqual(self.read()['budget'], before_budget)
        self.assertEqual(len(self.provider.applies), before_applies)

    def test_orphan_attempt_blocks_status_before_provider_read(self):
        proposal = self.ready()
        self.provider.error = OSError('controlled loss before effect')
        self.assertEqual(self.status(self.execute(proposal)), 'unknown')
        state = self.read()
        state['delivery']['plans'].pop(proposal['id'])
        write(self.host, 'state.json', state)
        with patch.object(self.provider, 'read', side_effect=ValueError('must not read')) as read:
            with self.session().locked() as s:
                self.reject(lambda: delivery.evaluate(s))
            read.assert_not_called()

    def test_unresolved_same_target_attempt_blocks_new_intent(self):
        proposal = self.ready()
        self.provider.error = OSError('lost before effect')
        try:
            self.execute(proposal)
        except ValueError:
            pass
        self.provider.error = None
        self.provider.query_status = 'unknown'
        source = self.read()['merge']
        self.reject(lambda: self.plan(source, 'rollback'))
        self.assertEqual(len(self.provider.applies), 1)

    def test_historical_cache_survives_missing_registry_files_expired_authority_and_budget(self):
        proposal = self.ready()
        confirmed = self.execute(proposal)
        self.registry = {}
        self.artifact.unlink()
        authority = self.read('authority.json')
        for item in authority['decisions']:
            item['expires_at'] = (core.now() - timedelta(hours=1)).isoformat()
            item['decision'] = 'revoke'
        write(self.host, 'authority.json', authority)
        state = self.read()
        state['budget']['operations'] = self.policy['budget']['max_operations']
        state['subject']['head'] = 'later-unrelated-subject'
        write(self.host, 'state.json', state)
        before = self.disk()
        with patch.object(core.Session, 'authorized', side_effect=AssertionError('renewed authorization')), \
             patch.object(artifacts, 'fresh', side_effect=AssertionError('historical file read')):
            for action in (self.query, self.execute):
                result = action(proposal)
                self.assertEqual(result['result'], confirmed['result'])
                self.assertIs(result['historical'], True)
        self.assertEqual(self.disk(), before)
        self.assertEqual(len(self.provider.applies), 1)

    def test_changed_policy_pin_refuses_old_history_without_migration(self):
        proposal = self.ready()
        self.execute(proposal)
        policy = self.read('policy.json')
        policy['delivery_provider']['provider_version'] = 'new-version'
        write(self.host, 'policy.json', policy)
        old_pin = self.pin
        self.reject(lambda: self.query(proposal))
        self.pin = sha((self.host / 'policy.json').read_bytes())
        self.reject(lambda: self.execute(proposal))
        self.pin = old_pin

    def test_stored_request_and_cached_result_require_canonical_consistent_attempt(self):
        proposal = self.ready()
        self.execute(proposal)
        baseline = self.read()
        mutations = {
            'no-request': lambda d: d['requests'].pop(proposal['id']),
            'no-result': lambda d: d['host_results'].pop(proposal['id']),
            'request-operation': lambda d: d['requests'][proposal['id']].update(operation='f' * 64),
            # Python equality accepts 0 == False; canonical request binding must not.
            'request-bool': lambda d: d['requests'][proposal['id']]['effect'].update(enabled=0),
            'request-state-bool': lambda d: d['requests'][proposal['id']]['expected_state'].update(enabled=1),
            'request-digest': lambda d: d['requests'][proposal['id']].update(request_digest='f' * 64),
            'receipt-target': lambda d: d['host_results'][proposal['id']].update(target='other'),
            'receipt-consumption': lambda d: d['host_results'][proposal['id']]['consumed_content'].update(size_bytes=True),
            'receipt-time': lambda d: d['host_results'][proposal['id']].update(observed_at=(core.now() + timedelta(hours=1)).isoformat())}
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(baseline)
                mutate(changed['delivery'])
                write(self.host, 'state.json', changed)
                self.reject(lambda: self.query(proposal))
                self.reject(lambda: self.execute(proposal))

    def test_selected_provenance_reverification_cannot_change_approved_intent(self):
        # Compose an existing verifier fixture, without inheriting/discovering tests.
        helper = provenance_tests.BuildProvenance(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.policy['delivery_provider'] = selection()
        helper.session = lambda: core.Session(helper.host, helper.pin,
            verifiers=helper.verifiers, delivery_providers=self.registry)
        helper.start()
        source = helper.merged()
        proposal = helper.deployment(source)
        self.assertEqual(proposal['build_provenance']['result']['binding']['action'], 'delivery-deploy')
        before = helper.read()
        helper.result_mutator = lambda result: result['binding'].update(intent_sha256='f' * 64)
        with helper.session().locked() as s:
            with self.assertRaises(ValueError):
                delivery.execute(s, proposal['id'])
        self.assertEqual(helper.read(), before)
        self.assertEqual(self.provider.applies, [])
        helper.result_mutator = None
        with helper.session().locked() as s:
            self.assertEqual(self.status(delivery.execute(s, proposal['id'])), 'confirmed')

    def test_plan_digest_is_checked_on_read_without_action_authority(self):
        proposal = self.ready(approve=False)
        self.assertEqual(self.status(self.query(proposal)), 'not_applied')
        state = self.read()
        state['delivery']['plans'][proposal['id']]['provider_snapshot']['observed_at'] = (
            core.now() - timedelta(minutes=1)).isoformat()
        write(self.host, 'state.json', state)
        self.reject(lambda: self.query(proposal))

    def test_forged_snapshot_is_rejected_before_charge_or_callback(self):
        proposal = self.ready()
        forged = artifacts.Snapshot(b'wrong bytes', sha(DATA), len(DATA))
        with patch.object(artifacts, 'fresh', return_value=forged):
            self.reject(lambda: self.execute(proposal))

    def test_delayed_exact_receipt_recovers_after_freshness_window(self):
        proposal = self.ready()
        self.provider.error = OSError('synthetic lost response')
        self.provider.error_after_effect = True
        self.assertEqual(self.status(self.execute(proposal)), 'unknown')
        budget = copy.deepcopy(self.read()['budget'])
        self.provider.error = None
        with patch.object(host_delivery, 'now', return_value=core.now() + timedelta(hours=1)):
            result = self.query(proposal)
        self.assertEqual(self.status(result), 'confirmed')
        self.assertEqual(result['current_state_status'], 'UNKNOWN')
        self.assertEqual(len(self.provider.applies), 1)
        self.assertEqual(self.read()['budget'], budget)

    def legacy_plan(self):
        self.start()
        source = self.merged()
        with self.session().locked() as s:
            selected = s.policy.pop('delivery_provider')
            try:
                proposal = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                    'parameters': {'artifact': source['subject']['artifact_digest']}})
            finally:
                s.policy['delivery_provider'] = selected
        grant(self.host, self.pin, 'delivery-deploy', proposal['id'])
        return proposal

    def test_selected_host_refuses_unattempted_legacy_simulation(self):
        proposal = self.legacy_plan()
        self.reject(lambda: self.execute(proposal))
        self.assertEqual(self.provider.applies, [])

    def test_selected_host_retains_exact_attempted_legacy_recovery(self):
        proposal = self.legacy_plan()
        with self.session().locked() as s:
            selected = s.policy.pop('delivery_provider')
            try:
                original = delivery.execute(s, proposal['id'])
            finally:
                s.policy['delivery_provider'] = selected
        before = self.disk()
        with patch.object(delivery.LocalSimulation, 'apply', side_effect=AssertionError('replay')):
            result = self.execute(proposal)
            self.assertEqual(result['result'], original['result'])
            self.assertTrue(result['simulation'])
        self.assertEqual(self.disk(), before)
        self.assertEqual(self.provider.applies, [])

    def test_orphan_or_malformed_unresolved_plan_cannot_allow_new_target_action(self):
        proposal = self.ready()
        self.provider.error = OSError('synthetic unknown before effect')
        self.assertEqual(self.status(self.execute(proposal)), 'unknown')
        original = self.read()
        for mutation in ('missing', 'schema'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(original)
                if mutation == 'missing':
                    del changed['delivery']['plans'][proposal['id']]
                else:
                    changed['delivery']['plans'][proposal['id']]['schema'] = 'damaged-host-plan/v0'
                write(self.host, 'state.json', changed)
                self.reject(lambda: self.plan({'id': proposal['source']['merge_plan']}, action='rollback'))
        self.assertEqual(len(self.provider.applies), 1)

    def test_persistence_faults_fence_session_and_reload_exact_charge_pending_receipt(self):
        proposal = self.ready()
        baseline, provider_state = self.read(), copy.deepcopy(self.provider.state)
        for phase, fail_call in [('charge', 1), ('pending', 2), ('receipt', 3)]:
            for replaced in (False, True):
                with self.subTest(phase=phase, replaced=replaced):
                    write(self.host, 'state.json', baseline)
                    self.provider.state = copy.deepcopy(provider_state)
                    self.provider.results.clear()
                    self.provider.applies.clear()
                    calls = []
                    real_save = core.save
                    def fail_save(root, name, value):
                        calls.append(copy.deepcopy(value))
                        if len(calls) == fail_call:
                            if replaced:
                                real_save(root, name, value)
                            raise OSError('controlled ' + phase + ' persistence fault')
                        return real_save(root, name, value)
                    s = self.session()
                    with s.locked():
                        with patch.object(core, 'save', fail_save):
                            with self.assertRaisesRegex(OSError, 'controlled ' + phase):
                                delivery.execute(s, proposal['id'])
                        self.assertEqual(len(calls), fail_call)
                        self.assertEqual(len(self.provider.applies), int(phase == 'receipt'))
                        disk, memory = self.disk(), copy.deepcopy(s.state)
                        for action in (s.persist, lambda: s.charge(operation='delivery-execute'),
                                       lambda: delivery.query(s, proposal['id']),
                                       lambda: delivery.execute(s, proposal['id'])):
                            with self.assertRaises(ValueError):
                                action()
                        self.assertEqual(self.disk(), disk)
                        self.assertEqual(s.state, memory)
                    persisted = self.read()
                    charged = phase != 'charge' or replaced
                    durable_pending = phase == 'receipt' or (phase == 'pending' and replaced)
                    self.assertEqual(persisted['budget']['operations'], baseline['budget']['operations'] + int(charged))
                    if phase == 'receipt' and not replaced:
                        self.assert_pending(proposal)
                    cached = persisted['delivery'].get('host_results', {}).get(proposal['id'])
                    if cached is not None:
                        request = persisted['delivery']['requests'][proposal['id']]
                        self.assertEqual(request['result'], cached['status'])
                        self.assertEqual(request['receipt_sha'], core.digest(cached))
                    with s.locked():
                        self.assertEqual(s.state, persisted)
                        before = self.disk()
                        if durable_pending:
                            result = delivery.execute(s, proposal['id'])
                            self.assertEqual(self.status(result), 'confirmed' if phase == 'receipt' else 'not_applied')
                            self.assertEqual(self.read()['budget'], persisted['budget'])
                            recovered = self.disk()
                            self.assertEqual(self.status(delivery.execute(s, proposal['id'])), self.status(result))
                            self.assertEqual(self.disk(), recovered)
                        else:
                            self.assertEqual(self.status(delivery.execute(s, proposal['id'])), 'confirmed')
                            self.assertEqual(s.state['budget']['operations'], persisted['budget']['operations'] + 1)
                    self.assertEqual(len(self.provider.applies), int(phase == 'receipt') + int(not durable_pending))


if __name__ == '__main__':
    unittest.main()
