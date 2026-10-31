"""Target-qualified persistence boundary, using only self-owned local doubles."""
import copy
from concurrent.futures import CancelledError
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import accounting, core, delivery, host_delivery, persistence, provenance, review
from validation.lifecycle import test_host_delivery as host_tests
from validation.lifecycle import test_build_provenance as provenance_tests
from validation.lifecycle.fixture import grant, write


class PersistenceContract(unittest.TestCase):
    def setUp(self):
        # Compose the fixture without inheriting its separately owned test cases.
        self.helper = host_tests.HostDelivery(methodName='runTest')
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def test_fresh_host_apply_requires_target_qualified_pending_capability(self):
        helper = self.helper
        proposal = helper.ready()
        helper.session = lambda: core.Session(helper.host, helper.pin,
            delivery_providers=helper.registry)
        before, budget = helper.disk(), copy.deepcopy(helper.read()['budget'])
        with self.assertRaisesRegex(ValueError, 'UNKNOWN|HOLD'):
            helper.execute(proposal)
        self.assertEqual(helper.provider.applies, [], 'missing capability reached external apply')
        self.assertEqual(helper.read()['budget'], budget, 'missing capability charged an attempt')
        self.assertEqual(helper.disk(), before, 'missing capability persisted a new intent')

    def test_policy_selection_and_installed_identity_are_exact_before_charge(self):
        helper = self.helper
        proposal = helper.ready()
        with helper.session().locked() as session:
            original = copy.deepcopy(session.policy['persistence'])
            changes = {'schema': 'other', 'adapter_id': '', 'adapter_version': '',
                       'storage_target': '', 'qualification': '', 'config_sha256': 'bad',
                       'command': 'echo untrusted', 'url': 'https://invalid.example'}
            for name, value in changes.items():
                with self.subTest(policy=name):
                    session.policy['persistence'] = dict(original, **{name: value})
                    with self.assertRaises(ValueError):
                        persistence.config(session)
                    helper.reject(lambda: delivery.execute(session, proposal['id']))
            session.policy['persistence'] = original
            for name in persistence.IDENTITY:
                with self.subTest(installed=name):
                    original_value = getattr(helper.persistence, name)
                    setattr(helper.persistence, name, 'different')
                    try:
                        helper.reject(lambda: delivery.execute(session, proposal['id']))
                    finally:
                        setattr(helper.persistence, name, original_value)
            session.policy.pop('persistence')
            helper.reject(lambda: delivery.execute(session, proposal['id']))
        self.assertEqual(helper.persistence.checkpoints, [])

    def test_registry_and_throwing_identity_cannot_grant_or_leak_capability(self):
        helper = self.helper
        proposal = helper.ready()
        secret = 'secret-private-adapter-detail'
        class ThrowingCallback:
            @property
            def checkpoint(self):
                raise RuntimeError(secret)
        for registry in ([], {host_tests.PERSISTENCE_ID: True},
                         {host_tests.PERSISTENCE_ID: {'status': 'confirmed'}},
                         {host_tests.PERSISTENCE_ID: ThrowingCallback()}):
            with self.subTest(registry=type(registry).__name__):
                with self.assertRaises(ValueError) as caught:
                    core.Session(helper.host, helper.pin, persistence_adapters=registry)
                self.assertNotIn(secret, str(caught.exception))
        class ThrowingIdentity:
            def checkpoint(self, binding, snapshot):
                raise AssertionError('must not call')
            @property
            def adapter_id(self):
                raise RuntimeError(secret)
        helper.persistence_registry = {host_tests.PERSISTENCE_ID: ThrowingIdentity()}
        helper.reject(lambda: helper.execute(proposal))
        with helper.session().locked() as session:
            report = delivery.evaluate(session)
            check = next(x for x in report['checks'] if x['id'] == 'host-persistence')
            self.assertEqual(check['status'], 'UNKNOWN')
            self.assertEqual(report['recommendation'], 'HOLD')
            self.assertNotIn(secret, str(report))

    def test_prior_confirmation_never_grants_the_next_fresh_apply(self):
        helper = self.helper
        first = helper.ready()
        self.assertEqual(helper.status(helper.execute(first)), 'confirmed')
        second = helper.plan({'id': first['source']['merge_plan']}, action='rollback')
        helper.persistence_registry = {}
        helper.reject(lambda: helper.execute(second))
        self.assertEqual(len(helper.provider.applies), 1)
        self.assertEqual(len(helper.persistence.checkpoints), 1)

    def test_exact_saved_bytes_checkpoint_precedes_one_apply(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()['budget']['operations']
        order = []
        def checkpoint(binding, snapshot):
            order.append('checkpoint')
            self.assertIs(type(snapshot), bytes)
            self.assertEqual(snapshot, (helper.host / 'state.json').read_bytes())
            saved = json.loads(snapshot)
            request = saved['delivery']['requests'][proposal['id']]
            self.assertEqual(request['result'], 'pending')
            self.assertEqual(saved['budget']['operations'], baseline + 1)
            self.assertEqual(binding['state_sha256'], host_tests.sha(snapshot))
            self.assertEqual(binding['state_size_bytes'], len(snapshot))
            self.assertEqual(binding['pending_request_sha256'], core.digest(request))
            self.assertEqual(binding['request_digest'], request['request_digest'])
            self.assertEqual(binding['operation'], proposal['id'])
            self.assertEqual(binding['policy_sha'], helper.pin)
            self.assertEqual(binding['task_id'], helper.policy['task_id'])
            self.assertEqual(binding['root']['locator'], str(helper.host.resolve()))
            info = helper.host.stat()
            self.assertEqual((binding['root']['device'], binding['root']['inode']),
                             (info.st_dev, info.st_ino))
            self.assertRegex(binding['challenge'], '^[0-9a-f]{32}$')
            with self.assertRaises(TypeError):
                snapshot[0] = 0
            for name in persistence.IDENTITY:
                self.assertEqual(binding[name], helper.policy['persistence'][name])
            return {'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
                    'binding': copy.deepcopy(binding)}
        helper.persistence.hook = checkpoint
        helper.provider.apply_hook = lambda request, snapshot: order.append('apply')
        result, error = None, None
        try:
            result = helper.execute(proposal)
        except (ValueError, OSError) as exc:
            error = type(exc).__name__
        self.assertIsNone(error, 'qualified positive control must save pending and complete')
        self.assertEqual(helper.status(result), 'confirmed')
        self.assertEqual(helper.read()['delivery']['requests'][proposal['id']]['result'], 'confirmed')
        self.assertEqual(order, ['checkpoint', 'apply'])
        self.assertEqual(len(helper.persistence.checkpoints), 1)
        self.assertEqual(len(helper.provider.applies), 1)
        self.assertNotIn(str(helper.host), str(result), 'private root escaped public result')

    def test_synthetic_checkpoint_uses_writable_nontruncating_file_handle(self):
        helper = self.helper
        proposal = helper.ready()
        original = Path.open
        modes = []
        def opened(path, mode='r', *args, **kwargs):
            if path == helper.host / 'state.json':
                modes.append(mode)
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            self.assertEqual(helper.status(helper.execute(proposal)), 'confirmed')
        self.assertIn('r+b', modes, 'synthetic fsync needs a writable handle on Windows')
        self.assertFalse(any('w' in mode for mode in modes), 'checkpoint must not truncate saved bytes')

    def assert_fenced_pending(self, session, proposal, baseline):
        helper = self.helper
        self.assertEqual(helper.provider.applies, [])
        self.assertEqual(helper.read()['budget']['operations'], baseline['budget']['operations'] + 1)
        self.assertEqual(helper.assert_pending(proposal)['result'], 'pending')
        before = helper.disk()
        memory = copy.deepcopy(session.state)
        for action in (session.persist, lambda: session.charge(operation='delivery-execute'),
                       lambda: delivery.execute(session, proposal['id']),
                       lambda: delivery.query(session, proposal['id'])):
            with self.assertRaisesRegex(ValueError, 'HOLD'):
                action()
        self.assertEqual(helper.disk(), before)
        self.assertEqual(session.state, memory)

    def test_unconfirmed_or_mismatched_checkpoint_fences_session_before_apply(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        changes = {'schema': 'wrong', 'status': 'unknown', 'binding': {}, 'extra': True}
        bad = [True, None, {'status': 'confirmed'}]
        for name, value in changes.items():
            bad.append((name, value))
        for name in ('challenge', 'operation', 'request_digest', 'pending_request_sha256',
                     'state_sha256', 'state_size_bytes', 'root', 'policy_sha', 'task_id',
                     *sorted(persistence.IDENTITY)):
            bad.append(('binding:' + name, 'mismatch'))
        for change in bad:
            with self.subTest(change=change):
                write(helper.host, 'state.json', baseline)
                def invalid(binding, snapshot):
                    result = {'schema': 'host-persistence-confirmation/v1',
                              'status': 'confirmed', 'binding': copy.deepcopy(binding)}
                    if type(change) is not tuple:
                        return change
                    name, value = change
                    if name.startswith('binding:'):
                        result['binding'][name.split(':', 1)[1]] = value
                    else:
                        result[name] = value
                    return result
                helper.persistence.hook = invalid
                with helper.session().locked() as session:
                    with self.assertRaisesRegex(ValueError, 'UNKNOWN'):
                        delivery.execute(session, proposal['id'])
                    self.assert_fenced_pending(session, proposal, baseline)

    def test_checkpoint_exception_cancellation_and_baseexception_fence_without_apply(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        secret = 'secret-private-checkpoint-detail'
        for error in (RuntimeError(secret), CancelledError(secret), KeyboardInterrupt('controlled abort')):
            with self.subTest(error=type(error).__name__):
                write(helper.host, 'state.json', baseline)
                def fail(binding, snapshot):
                    raise error
                helper.persistence.hook = fail
                expected = ValueError if type(error) is RuntimeError else type(error)
                with helper.session().locked() as session:
                    with self.assertRaises(expected) as caught:
                        delivery.execute(session, proposal['id'])
                    self.assertNotIn(secret, str(caught.exception))
                    self.assert_fenced_pending(session, proposal, baseline)

    def test_saved_bytes_and_policy_are_rechecked_after_callback(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        policy_bytes = (helper.host / 'policy.json').read_bytes()
        for drift in ('saved-bytes', 'disk-policy', 'session-policy', 'session-state', 'root'):
            with self.subTest(drift=drift):
                write(helper.host, 'state.json', baseline)
                (helper.host / 'policy.json').write_bytes(policy_bytes)
                session = helper.session()
                original_root = persistence._root
                root_calls = []
                def checked_root(s):
                    value = original_root(s)
                    root_calls.append(value)
                    if drift == 'root' and len(root_calls) > 2:
                        value = dict(value, inode=value['inode'] + 1)
                    return value
                def change(binding, snapshot):
                    if drift == 'saved-bytes':
                        (helper.host / 'state.json').write_bytes(snapshot + b' ')
                    elif drift == 'disk-policy':
                        (helper.host / 'policy.json').write_bytes(policy_bytes + b' ')
                    elif drift == 'session-policy':
                        session.policy['task_id'] = 'changed-in-memory'
                    elif drift == 'session-state':
                        session.state['budget']['operations'] += 1
                    return {'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
                            'binding': copy.deepcopy(binding)}
                helper.persistence.hook = change
                with session.locked(), patch.object(persistence, '_root', checked_root):
                    with self.assertRaisesRegex(ValueError, 'UNKNOWN'):
                        delivery.execute(session, proposal['id'])
                    self.assertEqual(helper.provider.applies, [])
                    self.assertTrue(session._persistence_failed)
        (helper.host / 'policy.json').write_bytes(policy_bytes)

    def test_saved_state_must_match_intended_pending_before_checkpoint(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        real_save = core.save
        calls = []
        def corrupt(root, name, value):
            calls.append(name)
            saved = copy.deepcopy(value)
            if len(calls) == 2:
                saved['delivery']['requests'][proposal['id']]['result'] = 'unknown'
            return real_save(root, name, saved)
        with helper.session().locked() as session, patch.object(core, 'save', corrupt):
            with self.assertRaisesRegex(ValueError, 'UNKNOWN'):
                delivery.execute(session, proposal['id'])
            self.assertTrue(session._persistence_failed)
        self.assertEqual(helper.persistence.checkpoints, [])
        self.assertEqual(helper.provider.applies, [])
        self.assertEqual(helper.read()['budget']['operations'], baseline['budget']['operations'] + 1)

    def test_authority_revoked_expired_wrong_binding_or_unreadable_after_checkpoint_fences(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        authority = helper.read('authority.json')
        secret = 'secret-private-authority-detail'
        for change in ('revoked', 'expired', 'wrong-binding', 'unreadable'):
            with self.subTest(change=change):
                write(helper.host, 'state.json', baseline)
                write(helper.host, 'authority.json', authority)
                def checkpoint(binding, snapshot):
                    changed = copy.deepcopy(authority)
                    if change == 'unreadable':
                        (helper.host / 'authority.json').write_text(secret)
                    else:
                        for decision in changed['decisions']:
                            if decision['action'] == 'delivery-deploy':
                                if change == 'revoked':
                                    decision['decision'] = 'revoke'
                                elif change == 'expired':
                                    decision['expires_at'] = '2000-01-01T00:00:00+00:00'
                                else:
                                    decision['binding'] = 'f' * 64
                        write(helper.host, 'authority.json', changed)
                    return {'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
                            'binding': copy.deepcopy(binding)}
                helper.persistence.hook = checkpoint
                with helper.session().locked() as session:
                    with self.assertRaisesRegex(ValueError, 'HOLD') as caught:
                        delivery.execute(session, proposal['id'])
                    self.assertNotIn(secret, str(caught.exception))
                    self.assert_fenced_pending(session, proposal, baseline)

    def test_pr_evidence_expiring_during_checkpoint_fences_before_apply(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        future = core.now() + timedelta(days=7)
        def checkpoint(binding, snapshot):
            changed_clock = patch.object(review, 'now', return_value=future)
            changed_clock.start()
            self.addCleanup(changed_clock.stop)
            return {'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
                    'binding': copy.deepcopy(binding)}
        helper.persistence.hook = checkpoint
        with helper.session().locked() as session:
            with self.assertRaisesRegex(ValueError, 'HOLD'):
                delivery.execute(session, proposal['id'])
            self.assert_fenced_pending(session, proposal, baseline)

    def test_provider_observation_expiring_during_checkpoint_fences_before_apply(self):
        helper = self.helper
        proposal = helper.ready()
        baseline = helper.read()
        future = core.now() + timedelta(seconds=301)
        def checkpoint(binding, snapshot):
            changed_clock = patch.object(host_delivery, 'now', return_value=future)
            changed_clock.start()
            self.addCleanup(changed_clock.stop)
            return {'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
                    'binding': copy.deepcopy(binding)}
        helper.persistence.hook = checkpoint
        with helper.session().locked() as session:
            with self.assertRaisesRegex(ValueError, 'HOLD'):
                delivery.execute(session, proposal['id'])
            self.assert_fenced_pending(session, proposal, baseline)

    def provenance_ready(self):
        host = self.helper
        helper = provenance_tests.BuildProvenance(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.policy['delivery_provider'] = host_tests.selection()
        helper.policy['persistence'] = host_tests.persistence_selection()
        helper.session = lambda: core.Session(helper.host, helper.pin, verifiers=helper.verifiers,
            delivery_providers=host.registry, persistence_adapters=host.persistence_registry)
        helper.start()
        return helper, helper.deployment(helper.merged())

    def test_fresh_build_observation_expiry_and_max_age_fence_without_second_verifier(self):
        host = self.helper
        helper, proposal = self.provenance_ready()
        baseline = helper.read()
        for horizon in ('expiry', 'max-age'):
            with self.subTest(horizon=horizon):
                write(helper.host, 'state.json', baseline)
                calls = len(helper.callback_calls)
                at = core.now()
                def extend_expiry(result):
                    result['expires_at'] = (at + timedelta(hours=2)).isoformat()
                helper.result_mutator = extend_expiry if horizon == 'max-age' else None
                future = at + timedelta(seconds=301 if horizon == 'expiry' else 3601)
                changed_clock = patch.object(provenance, 'now', return_value=future)
                def checkpoint(binding, snapshot):
                    changed_clock.start()
                    return {'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
                            'binding': copy.deepcopy(binding)}
                host.persistence.hook = checkpoint
                try:
                    with helper.session().locked() as session:
                        with self.assertRaisesRegex(ValueError, 'HOLD'):
                            delivery.execute(session, proposal['id'])
                        self.assertTrue(session._persistence_failed)
                        self.assertEqual(helper.read()['delivery']['requests'][proposal['id']]['result'], 'pending')
                finally:
                    changed_clock.stop()
                self.assertEqual(host.provider.applies, [])
                self.assertEqual(len(helper.callback_calls), calls + 1)
                self.assertEqual(helper.read()['budget']['operations'], baseline['budget']['operations'] + 1)

    def test_expired_stored_proof_with_current_renewed_observation_can_apply(self):
        host = self.helper
        helper, proposal = self.provenance_ready()
        original = proposal['id']
        proposal['build_provenance']['result'].update(
            checked_at=(core.now() - timedelta(seconds=20)).isoformat(),
            expires_at=(core.now() - timedelta(seconds=10)).isoformat())
        proposal['id'] = core.digest({k: v for k, v in proposal.items() if k != 'id'})
        state = helper.read()
        state['delivery']['plans'].pop(original)
        state['delivery']['plans'][proposal['id']] = copy.deepcopy(proposal)
        write(helper.host, 'state.json', state)
        grant(helper.host, helper.pin, 'delivery-deploy', proposal['id'])
        calls = len(helper.callback_calls)
        with helper.session().locked() as session:
            result, error = None, None
            try:
                result = delivery.execute(session, proposal['id'])
            except ValueError as exc:
                error = type(exc).__name__
            self.assertIsNone(error, 'fresh renewed proof must remain eligible')
        self.assertEqual(result['result']['status'], 'confirmed')
        self.assertEqual(len(helper.callback_calls), calls + 1)
        self.assertEqual(len(host.provider.applies), 1)
        stored = helper.read()['delivery']['plans'][proposal['id']]['build_provenance']
        self.assertEqual(stored, proposal['build_provenance'])

    def test_same_saved_bytes_confirmation_cannot_be_replayed_for_new_challenge(self):
        helper = self.helper
        proposal = helper.ready()
        saved_binding = []
        class StopCheckpoint(Exception):
            pass
        def capture(binding, snapshot):
            saved_binding.append(copy.deepcopy(binding))
            raise StopCheckpoint()
        helper.persistence.hook = capture
        with self.assertRaises(ValueError):
            helper.execute(proposal)
        pending = helper.read()['delivery']['requests'][proposal['id']]
        helper.persistence.hook = lambda binding, snapshot: {
            'schema': 'host-persistence-confirmation/v1', 'status': 'confirmed',
            'binding': copy.deepcopy(saved_binding[0])}
        # Exercise the checkpoint contract with identical saved bytes. Ordinary
        # execute after reload uses query and never requests this second check.
        with helper.session().locked() as session:
            selection, adapter = persistence.selected(session)
            with self.assertRaises(ValueError):
                persistence.checkpoint(session, proposal, pending, selection, adapter)
            self.assertTrue(session._persistence_failed)
        self.assertNotEqual(helper.persistence.checkpoints[0][0]['challenge'],
                            helper.persistence.checkpoints[1][0]['challenge'])
        self.assertEqual(helper.provider.applies, [])

    def test_pending_reload_and_terminal_cache_recover_without_capability_or_artifact(self):
        helper = self.helper
        proposal = helper.ready()
        helper.persistence.hook = lambda binding, snapshot: False
        with self.assertRaises(ValueError):
            helper.execute(proposal)
        helper.persistence_registry = {}
        helper.artifact.unlink()
        write(helper.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        state = helper.read()
        state['budget']['operations'] = helper.policy['budget']['max_operations']
        write(helper.host, 'state.json', state)
        budget = copy.deepcopy(state['budget'])
        result = helper.execute(proposal)
        self.assertEqual(helper.status(result), 'not_applied')
        self.assertTrue(result['historical'])
        self.assertEqual(helper.provider.applies, [])
        self.assertEqual(helper.read()['budget'], budget)
        helper.registry = {}
        before = helper.disk()
        for action in (helper.query, helper.execute):
            self.assertEqual(helper.status(action(proposal)), 'not_applied')
        self.assertEqual(helper.disk(), before)

    def test_plain_save_and_reload_accounting_scope_does_not_claim_power_loss(self):
        helper = self.helper
        helper.start()
        with helper.session().locked() as session:
            for observe in ('reload', 'save'):
                if observe == 'save':
                    session.persist()
                report = accounting.budget_report(session, accounting.InvocationMetrics())
                self.assertEqual(report['ledger']['durability'], 'persisted')
                self.assertEqual(report['ledger']['durability_scope'], {
                    'save_or_reload_visibility': 'confirmed', 'power_loss': 'unknown',
                    'host_qualified_checkpoint': 'not_asserted'})


if __name__ == '__main__':
    unittest.main()
