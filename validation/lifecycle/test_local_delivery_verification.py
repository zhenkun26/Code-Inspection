"""Single fresh verifier observation per local action; all providers are fixtures."""
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import artifacts, core, delivery, provenance
from validation.lifecycle import test_build_provenance as builds
from validation.lifecycle.fixture import grant, write


class LocalDeliveryVerification(unittest.TestCase):
    def fixture(self, *, optional=False, legacy=False):
        h = builds.BuildProvenance()
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.start(required=[] if optional else None, legacy=legacy)
        source = h.merged()
        proposal = h.deployment(source)
        return h, source, proposal

    def after_pending(self, h, proposal, mutate):
        real = core.Session.persist
        seen = []
        def persist(s):
            result = real(s)
            request = s.state['delivery'].get('requests', {}).get(proposal['id'])
            if request is not None and request['result'] == 'pending' and not seen:
                seen.append(True)
                mutate(s)
            return result
        return patch.object(core.Session, 'persist', persist)

    def assert_started_unknown(self, h, proposal, budget, provider):
        state = h.read()
        self.assertEqual(state['budget']['operations'], budget['operations'] + 1)
        self.assertEqual(state['budget']['rounds'], budget['rounds'])
        self.assertEqual(state['delivery']['requests'][proposal['id']]['result'], 'unknown')
        self.assertEqual((h.host / 'sim-provider.json').read_bytes(), provider)

    def test_deploy_verifies_once_and_keeps_approved_proof(self):
        h, _, proposal = self.fixture()
        calls, budget = len(h.callback_calls), h.read()['budget']
        result = h.execute_delivery(proposal['id'])
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertEqual(len(h.callback_calls) - calls, 1)
        self.assertEqual(h.read()['delivery']['plans'][proposal['id']], proposal)
        self.assertEqual(h.read()['budget']['operations'], budget['operations'] + 1)
        self.assertEqual(result['result']['consumed_content'], {
            name: proposal['artifact_content']['proof'][name] for name in ('sha256', 'size_bytes')})

    def test_unavailable_second_callback_cannot_spoil_verified_action(self):
        h, _, proposal = self.fixture()
        calls = len(h.callback_calls)
        h.result_mutator = lambda r: r.update(status='unknown') if len(h.callback_calls) - calls > 1 else None
        try:
            result = h.execute_delivery(proposal['id'])
        except ValueError as error:
            self.fail('one verified observation should complete the action: ' + str(error))
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertEqual(len(h.callback_calls) - calls, 1)

    def test_first_callback_failure_has_no_charge_or_apply(self):
        h, _, proposal = self.fixture()
        before, calls = h.snapshot(), len(h.callback_calls)
        h.result_mutator = lambda r: r.update(status='unknown')
        with patch.object(delivery.LocalSimulation, 'apply', side_effect=AssertionError('rejected action applied')):
            with self.assertRaisesRegex(ValueError, 'fresh build provenance not verified'):
                h.execute_delivery(proposal['id'])
        self.assertEqual(len(h.callback_calls) - calls, 1)
        self.assertEqual(h.snapshot(), before)

    def test_optional_supplied_proof_still_verifies_once(self):
        h, _, proposal = self.fixture(optional=True)
        calls = len(h.callback_calls)
        self.assertEqual(h.execute_delivery(proposal['id'])['result']['result'], 'confirmed')
        self.assertEqual(len(h.callback_calls) - calls, 1)

    def test_renewed_observation_outlives_stored_proof_without_rewriting_it(self):
        h, _, proposal = self.fixture()
        stored = proposal['build_provenance']['result']
        current = core.timestamp(stored['expires_at']) + timedelta(seconds=1)
        # A trusted verifier may return an earlier valid cached check. Its fresh
        # observation has a renewed window, while the approved proof is expired.
        h.result_mutator = lambda r: r.update(checked_at=stored['checked_at'],
            expires_at=(current + timedelta(minutes=5)).isoformat(), basis=builds.BASIS + ' renewed')
        calls = len(h.callback_calls)
        with patch.object(provenance, 'now', return_value=current):
            try:
                result = h.execute_delivery(proposal['id'])
            except ValueError as error:
                self.fail('valid renewed observation should complete the action: ' + str(error))
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertEqual(len(h.callback_calls) - calls, 1)
        self.assertEqual(h.read()['delivery']['plans'][proposal['id']], proposal)

    def test_no_proof_or_policy_needs_no_callback(self):
        h = builds.BuildProvenance()
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.start(legacy=True)
        source = h.prepare_merge(content=False, provenance=False)
        with h.session().locked() as s:
            from interfaces.lifecycle import merge
            merge.execute(s)
        proposal = h.deployment(source)
        with patch.object(provenance, 'fresh', side_effect=AssertionError('legacy called verifier')):
            self.assertEqual(h.execute_delivery(proposal['id'])['result']['result'], 'confirmed')
        self.assertEqual(h.callback_calls, [])

    def test_rollback_enable_and_promote_each_verify_once(self):
        h, source, deployed = self.fixture()
        h.execute_delivery(deployed['id'])
        records = [
            ('flag-set', {'enabled': True, 'cohort': 'internal'}),
            ('promote', {'artifact': deployed['parameters']['artifact'], 'channel': 'fixture-stable'}),
            ('rollback', {'artifact': deployed['parameters']['artifact']})]
        with patch.object(delivery, 'observation_status', return_value={'status': 'PASS'}):
            for action, parameters in records:
                with self.subTest(action=action), h.session().locked() as s:
                    record = {'schema': 'delivery-plan/v1', 'action': action, 'parameters': parameters}
                    if action == 'rollback':
                        record.update(artifact_content=h.descriptor(), artifact_source_merge=source['id'])
                    proposal = delivery.plan(s, record)
                    grant(h.host, h.pin, 'delivery-' + action, proposal['id'])
                    calls = len(h.callback_calls)
                    self.assertEqual(delivery.execute(s, proposal['id'])['result']['result'], 'confirmed')
                    self.assertEqual(len(h.callback_calls) - calls, 1)

    def test_authority_revoked_or_expired_after_pending_still_blocks(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                h, _, proposal = self.fixture()
                budget, provider, calls = h.read()['budget'], (h.host / 'sim-provider.json').read_bytes(), len(h.callback_calls)
                def revoke(s):
                    authority = h.read('authority.json')
                    for decision in authority['decisions']:
                        if decision['action'] == 'delivery-deploy':
                            if expired:
                                decision['expires_at'] = (core.now() - timedelta(seconds=1)).isoformat()
                            else:
                                decision['decision'] = 'deny'
                    write(h.host, 'authority.json', authority)
                with self.after_pending(h, proposal, revoke), self.assertRaisesRegex(ValueError, 'authority'):
                    h.execute_delivery(proposal['id'])
                self.assertEqual(len(h.callback_calls) - calls, 1)
                self.assert_started_unknown(h, proposal, budget, provider)

    def test_local_eligibility_drift_after_pending_still_blocks(self):
        for kind in ('provider', 'subject', 'pr', 'source', 'capability', 'content'):
            with self.subTest(kind=kind):
                h, _, proposal = self.fixture()
                budget, calls = h.read()['budget'], len(h.callback_calls)
                expected_provider = [(h.host / 'sim-provider.json').read_bytes()]
                def drift(s):
                    if kind == 'provider':
                        state = h.read('sim-provider.json')
                        state['config'] = 'changed-after-pending'
                        write(h.host, 'sim-provider.json', state)
                        expected_provider[0] = (h.host / 'sim-provider.json').read_bytes()
                    elif kind == 'subject':
                        s.state['subject'] = dict(s.state['subject'], head='changed-after-pending')
                    elif kind == 'pr':
                        s.state['findings']['new-blocker'] = {'id': 'new-blocker', 'blocking': True, 'alias': None}
                    elif kind == 'source':
                        s.state['merge'] = {}
                    elif kind == 'capability':
                        s.policy['delivery']['capabilities']['deployment']['enabled'] = False
                    else:
                        s.policy['artifact_content']['isolation'] = 'unconfirmed'
                with self.after_pending(h, proposal, drift), self.assertRaises(ValueError):
                    h.execute_delivery(proposal['id'])
                self.assertEqual(len(h.callback_calls) - calls, 1)
                self.assert_started_unknown(h, proposal, budget, expected_provider[0])

    def test_retained_observation_clock_window_and_build_age_fail_closed(self):
        for kind in ('checked', 'expires', 'age'):
            with self.subTest(kind=kind):
                h, _, proposal = self.fixture()
                calls, budget = len(h.callback_calls), h.read()['budget']
                provider = (h.host / 'sim-provider.json').read_bytes()
                at = core.now()
                h.result_mutator = lambda r: r.update(checked_at=at.isoformat(),
                    expires_at=(at + timedelta(hours=2) if kind == 'age' else at + timedelta(seconds=10)).isoformat())
                clock = [at]
                def advance(s):
                    clock[0] = at + timedelta(seconds={'checked': -1, 'expires': 11, 'age': 3600}[kind])
                with patch.object(provenance, 'now', side_effect=lambda: clock[0]), \
                        self.after_pending(h, proposal, advance), self.assertRaises(ValueError):
                    h.execute_delivery(proposal['id'])
                self.assertEqual(len(h.callback_calls) - calls, 1)
                self.assert_started_unknown(h, proposal, budget, provider)

    def test_retained_observation_binding_descriptor_and_build_instant_are_checked(self):
        for kind in ('binding', 'descriptor', 'built', 'missing'):
            with self.subTest(kind=kind):
                h, _, proposal = self.fixture()
                calls, budget = len(h.callback_calls), h.read()['budget']
                provider = (h.host / 'sim-provider.json').read_bytes()
                real = provenance.fresh
                def corrupt(*args, **kwargs):
                    result = real(*args, **kwargs)
                    if kind == 'missing':
                        return None
                    if kind == 'binding':
                        result['result']['binding']['intent_sha256'] = 'f' * 64
                    elif kind == 'descriptor':
                        result['descriptor']['evidence_path'] = 'different-evidence.json'
                    else:
                        result['result']['built_at'] = (core.timestamp(h.built_at) + timedelta(seconds=1)).isoformat()
                    return result
                with patch.object(provenance, 'fresh', corrupt), self.assertRaises(ValueError):
                    h.execute_delivery(proposal['id'])
                self.assertEqual(len(h.callback_calls) - calls, 1)
                self.assert_started_unknown(h, proposal, budget, provider)

    def test_missing_optional_proof_cannot_suppress_new_requirement(self):
        h = builds.BuildProvenance()
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.start(required=[])
        source = h.prepare_merge(provenance=False)
        with h.session().locked() as s:
            from interfaces.lifecycle import merge
            merge.execute(s)
        proposal = h.deployment(source)
        self.assertNotIn('build_provenance', proposal)
        budget, calls = h.read()['budget'], len(h.callback_calls)
        provider = (h.host / 'sim-provider.json').read_bytes()
        def require_proof(s):
            s.policy['build_provenance']['required_actions'] = ['delivery-deploy']
        with self.after_pending(h, proposal, require_proof), self.assertRaisesRegex(ValueError, 'required build provenance'):
            h.execute_delivery(proposal['id'])
        self.assertEqual(len(h.callback_calls), calls)
        self.assert_started_unknown(h, proposal, budget, provider)

    def test_observation_and_rollback_compatibility_drift_still_block(self):
        for action in ('promote', 'rollback'):
            with self.subTest(action=action):
                h, source, deployed = self.fixture()
                h.execute_delivery(deployed['id'])
                status = ['PASS']
                with patch.object(delivery, 'observation_status', side_effect=lambda *_: {'status': status[0]}):
                    record = {'schema': 'delivery-plan/v1', 'action': action,
                              'parameters': {'artifact': deployed['parameters']['artifact']}}
                    if action == 'promote':
                        record['parameters']['channel'] = 'fixture-stable'
                    else:
                        record.update(artifact_content=h.descriptor(), artifact_source_merge=source['id'])
                    with h.session().locked() as s:
                        proposal = delivery.plan(s, record)
                    grant(h.host, h.pin, 'delivery-' + action, proposal['id'])
                    calls, budget = len(h.callback_calls), h.read()['budget']
                    provider = (h.host / 'sim-provider.json').read_bytes()
                    def drift(s):
                        status[0] = 'UNKNOWN'
                        if action == 'rollback':
                            s.policy['delivery']['rollback_compatibility'].clear()
                    with self.after_pending(h, proposal, drift), self.assertRaises(ValueError):
                        h.execute_delivery(proposal['id'])
                    self.assertEqual(len(h.callback_calls) - calls, 1)
                    self.assert_started_unknown(h, proposal, budget, provider)

    def test_private_context_is_bound_to_provider_and_consumed_on_refusal(self):
        h, _, proposal = self.fixture()
        with h.session().locked() as s:
            s.charge(operation='delivery-execute')
            s.state['delivery']['requests'] = {proposal['id']: delivery.request_intent(proposal, core.now().isoformat())}
            s.persist()
            snapshot = artifacts.fresh(s, 'delivery-deploy', proposal['artifact_content'])
            first, other = delivery.LocalSimulation(s), delivery.LocalSimulation(s)
            first._prepare(proposal, first.read())
            other._verification = first._verification
            calls, before = len(h.callback_calls), h.snapshot()
            with self.assertRaisesRegex(ValueError, 'context mismatch'):
                other.apply(proposal, snapshot=snapshot)
            self.assertIsNone(other._verification)
            self.assertEqual(len(h.callback_calls), calls)
            self.assertEqual(h.snapshot(), before)
            # Also refuse an accidentally mixed context on the same provider.
            # The caller's actual approved plan and its stored proof stay valid.
            first._verification.proposal['id'] = 'f' * 64
            with self.assertRaisesRegex(ValueError, 'context mismatch'):
                first.apply(proposal, snapshot=snapshot)
            self.assertIsNone(first._verification)
            self.assertEqual(len(h.callback_calls), calls)
            self.assertEqual(h.snapshot(), before)

    def test_execute_discards_private_context_on_success_or_error(self):
        for kind in ('confirmed', 'snapshot', 'persist'):
            with self.subTest(kind=kind):
                h, _, proposal = self.fixture()
                prepared, prepare, apply = [], delivery.LocalSimulation._prepare, delivery.LocalSimulation.apply
                def remember(provider, plan, state):
                    result = prepare(provider, plan, state)
                    prepared.append(provider)
                    return result
                def consume(provider, plan, snapshot=None):
                    if kind == 'snapshot':
                        snapshot = artifacts.Snapshot(b'forged', snapshot.sha256, snapshot.size_bytes)
                    return apply(provider, plan, snapshot=snapshot)
                def persisted(s):
                    if kind == 'persist':
                        raise OSError('synthetic after-pending persistence error')
                with patch.object(delivery.LocalSimulation, '_prepare', remember), \
                        patch.object(delivery.LocalSimulation, 'apply', consume), self.after_pending(h, proposal, persisted):
                    if kind == 'confirmed':
                        self.assertEqual(h.execute_delivery(proposal['id'])['result']['result'], 'confirmed')
                    else:
                        with self.assertRaises((ValueError, OSError)):
                            h.execute_delivery(proposal['id'])
                self.assertEqual(len(prepared), 1)
                self.assertIsNone(prepared[0]._verification)

    def test_direct_apply_verifies_and_preserves_snapshot_guard(self):
        for kind in ('verified', 'unverified', 'forged-snapshot'):
            with self.subTest(kind=kind):
                h, _, proposal = self.fixture()
                with h.session().locked() as s:
                    s.charge(operation='delivery-execute')
                    s.state['delivery']['requests'] = {proposal['id']: delivery.request_intent(proposal, core.now().isoformat())}
                    s.persist()
                    snapshot = artifacts.fresh(s, 'delivery-deploy', proposal['artifact_content'])
                    if kind == 'forged-snapshot':
                        snapshot = artifacts.Snapshot(b'forged bytes', snapshot.sha256, snapshot.size_bytes)
                    if kind == 'unverified':
                        h.result_mutator = lambda r: r.update(status='unknown')
                    before, calls = h.snapshot(), len(h.callback_calls)
                    provider = delivery.LocalSimulation(s)
                    if kind == 'verified':
                        self.assertEqual(provider.apply(proposal, snapshot=snapshot)['result']['result'], 'confirmed')
                    else:
                        with self.assertRaises(ValueError):
                            provider.apply(proposal, snapshot=snapshot)
                        self.assertEqual(h.snapshot(), before)
                    self.assertEqual(len(h.callback_calls) - calls, 1)
                    self.assertEqual(s.state['budget'], before['budget'])

    def test_lost_response_recovers_without_verifier_apply_or_charge(self):
        for applied in (False, True):
            with self.subTest(applied=applied):
                h, _, proposal = self.fixture()
                calls, budget, attempts = len(h.callback_calls), h.read()['budget'], []
                real = delivery.LocalSimulation.apply
                def lose(provider, plan, snapshot=None):
                    attempts.append(plan['id'])
                    if applied:
                        real(provider, plan, snapshot=snapshot)
                    raise OSError('synthetic lost response')
                with patch.object(delivery.LocalSimulation, 'apply', lose), self.assertRaises(OSError):
                    h.execute_delivery(proposal['id'])
                self.assertEqual(len(h.callback_calls) - calls, 1)
                self.assertEqual(attempts, [proposal['id']])
                h.artifact.unlink()
                h.evidence_file.unlink()
                h.verifiers = {}
                write(h.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
                before = h.snapshot()
                with patch.object(delivery.LocalSimulation, 'apply', side_effect=AssertionError('recovery applied')), \
                        patch.object(provenance, 'fresh', side_effect=AssertionError('recovery verified')):
                    for _ in range(2):
                        with h.session().locked() as s:
                            expected = 'confirmed' if applied else 'unknown'
                            self.assertEqual(delivery.query(s, proposal['id'])['result']['result'], expected)
                            self.assertEqual(delivery.execute(s, proposal['id'])['result']['result'], expected)
                self.assertEqual(h.snapshot(), before)
                self.assertEqual(h.read()['budget']['operations'], budget['operations'] + 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
