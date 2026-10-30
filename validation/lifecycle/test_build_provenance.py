"""Optional verifier integration, using explicitly synthetic trusted callbacks.

The callback below is a test double, not a cryptographic verifier. These tests
exercise local binding, freshness, accounting and recovery; no signature,
issuer, build service, real human decision or production provider is accepted.
"""
import copy
from concurrent.futures import CancelledError
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import artifacts, core, delivery, merge, review
try:
    from interfaces.lifecycle import provenance as build_provenance
except ImportError:
    build_provenance = None
from validation.lifecycle import test_artifact_content as artifact_tests
from validation.lifecycle.fixture import evidence, grant, write

EVIDENCE = b'SYNTHETIC verifier evidence; no authenticated build or provider.\n'
BASIS = 'Synthetic injected verifier fixture; no cryptographic or provider acceptance.'


class BuildProvenance(unittest.TestCase):
    # Compose helpers; inheriting ArtifactContent would rediscover all its tests.
    read = artifact_tests.ArtifactContent.read
    descriptor = artifact_tests.ArtifactContent.descriptor
    ready_pr = artifact_tests.ArtifactContent.ready_pr
    snapshot = artifact_tests.ArtifactContent.snapshot
    assert_no_action = artifact_tests.ArtifactContent.assert_no_action
    execute_merge = artifact_tests.ArtifactContent.execute_merge
    execute_delivery = artifact_tests.ArtifactContent.execute_delivery
    merged = artifact_tests.ArtifactContent.merged

    def setUp(self):
        self.assertIsNotNone(build_provenance, 'optional build provenance capability is not implemented')
        artifact_tests.ArtifactContent.setUp(self)
        self.evidence_root = self.host / 'provenance-evidence'
        self.evidence_root.mkdir()
        self.evidence_file = self.evidence_root / 'build.json'
        self.evidence_file.write_bytes(EVIDENCE)
        self.callback_calls = []
        self.built_at = (core.now() - timedelta(seconds=30)).isoformat()
        self.result_mutator = None
        self.callback_error = None
        self.verifiers = {'fixture-verifier': self.verifier}

    def session(self):
        return core.Session(self.host, self.pin, verifiers=self.verifiers)

    def start(self, required=None, legacy=False):
        if not legacy:
            self.policy['build_provenance'] = {
                'schema': 'build-provenance-policy/v1',
                'required_actions': ['merge-execute', 'delivery-deploy', 'delivery-rollback']
                    if required is None else required,
                'verifier_id': 'fixture-verifier', 'trust_root_id': 'fixture-root',
                'repository': 'fixture-owner/fixture-repository', 'issuer': 'fixture-issuer',
                'builder_id': 'fixture-builder', 'build_environment': 'fixture-environment',
                'max_age_seconds': 3600}
        artifact_tests.ArtifactContent.start(self, required=[] if legacy else None, legacy=legacy)

    def provenance_descriptor(self, data=EVIDENCE, path='build.json'):
        return {'run_id': 'fixture-run-17', 'run_attempt': 1, 'evidence_path': path,
                'evidence_sha256': hashlib.sha256(data).hexdigest()}

    def verifier(self, request, raw):
        self.assertIs(type(raw), bytes)
        self.callback_calls.append((copy.deepcopy(request), raw))
        if self.callback_error is not None:
            raise self.callback_error
        at = core.now()
        result = {'schema': 'build-verifier-result/v1',
                  'status': 'verified' if raw == EVIDENCE else 'unverified',
                  'binding': copy.deepcopy(request), 'evidence_sha256': hashlib.sha256(raw).hexdigest(),
                  'built_at': self.built_at,
                  'checked_at': at.isoformat(), 'expires_at': (at + timedelta(minutes=5)).isoformat(),
                  'revoked': False, 'basis': BASIS}
        if self.result_mutator is not None:
            changed = self.result_mutator(result)
            if changed is not None:
                return changed
        return result

    def context(self, s):
        content, _ = artifacts.capture(s, self.descriptor(), self.subject, artifact_tests.TREE)
        intent = {'repository': self.policy['merge']['repository'], 'base': self.subject['base'],
                  'head': self.subject['head'], 'tree': artifact_tests.TREE, 'artifact_content': content}
        return content, intent

    def capture(self, s, descriptor=None, content=None, intent=None):
        if content is None:
            content, intent = self.context(s)
        return build_provenance.capture(s, descriptor or self.provenance_descriptor(), self.subject,
                                        artifact_tests.TREE, content, 'merge-execute', intent)

    def prepare_merge(self, content=True, descriptor=None, approve_execute=True, provenance=True):
        self.ready_pr()
        grant(self.host, self.pin, 'merge-prepare', core.digest({
            'repository': self.policy['merge']['repository'], 'base': self.subject['base'],
            'head': self.subject['head'], 'policy_sha': self.pin}))
        request = {'schema': 'merge-plan/v1', 'base': self.subject['base'], 'head': self.subject['head'],
                   'integration_artifact': 'legacy-combination-artifact'}
        if content:
            descriptor = descriptor or self.descriptor()
            request.update(artifact_content=descriptor, integration_artifact='sha256:' + descriptor['sha256'])
        if provenance:
            request['build_provenance'] = self.provenance_descriptor()
        with self.session().locked() as s:
            plan = merge.prepare(s, request)['plan']
            check = next(c for c in self.policy['checks'] if c['stage'] == 'merge')
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, plan['subject'], check, 'integration-' + plan['id'])]})
        grant(self.host, self.pin, 'merge-approve', core.digest(plan['subject']))
        if approve_execute:
            grant(self.host, self.pin, 'merge-execute', plan['id'])
        return plan

    def deployment(self, source):
        with self.session().locked() as s:
            proposal = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                'parameters': {'artifact': source['subject']['artifact_digest']}})
        grant(self.host, self.pin, 'delivery-deploy', proposal['id'])
        return proposal

    def test_selected_callback_receives_exact_bytes_and_complete_binding(self):
        self.start()
        with self.session().locked() as s:
            content, intent = self.context(s)
            descriptor = self.provenance_descriptor()
            expected = build_provenance.expected(s, descriptor, self.subject, artifact_tests.TREE,
                                                  content, 'merge-execute', intent)
            proof = self.capture(s, content=content, intent=intent)
        self.assertEqual(self.callback_calls, [(expected, EVIDENCE)])
        self.assertEqual(proof['descriptor'], descriptor)
        self.assertEqual(proof['result']['binding'], expected)
        self.assertEqual(expected['source_subject'], self.subject)
        self.assertEqual(expected['candidate_tree'], artifact_tests.TREE)
        self.assertEqual(expected['artifact_sha256'], artifact_tests.sha(artifact_tests.DATA))
        self.assertEqual(expected['size_bytes'], len(artifact_tests.DATA))
        self.assertEqual(expected['build_config_digest'], artifact_tests.BUILD_CONFIG)
        self.assertEqual(expected['policy_sha'], self.pin)
        self.assertEqual(expected['intent_sha256'], core.digest(intent))
        self.assertEqual(expected['action'], 'merge-execute')
        self.assertEqual(proof['result']['basis'], BASIS)

    def test_missing_required_proof_holds_before_prepare_charge(self):
        self.start()
        self.ready_pr()
        def missing():
            with self.session().locked() as s:
                merge.prepare(s, {'schema': 'merge-plan/v1', 'base': self.subject['base'],
                    'head': self.subject['head'], 'integration_artifact': 'sha256:' + artifact_tests.sha(artifact_tests.DATA),
                    'artifact_content': self.descriptor()})
        self.assert_no_action(missing)
        self.assertEqual(self.callback_calls, [])

    def test_optional_absence_remains_unverified_and_supplied_proof_is_checked(self):
        self.start(required=[])
        plan = self.prepare_merge(provenance=False)
        self.assertNotIn('build_provenance', plan)
        self.assertEqual(self.execute_merge()['result'], 'confirmed')
        with self.session().locked() as s:
            report = merge.query(s)['provenance_binding']
            self.assertNotEqual(report.get('status'), 'PASS')
        self.assertEqual(self.callback_calls, [])
        self.result_mutator = lambda r: r.update(status='unverified')
        with self.session().locked() as s, self.assertRaises(ValueError):
            self.capture(s)

    def test_policy_absent_legacy_records_keep_their_schema_and_unknown_report(self):
        self.start(legacy=True)
        plan = self.prepare_merge(content=False, provenance=False)
        self.assertNotIn('schema', plan)
        self.assertNotIn('build_provenance', plan)
        self.assertEqual(self.execute_merge()['result'], 'confirmed')
        with self.session().locked() as s:
            rolled = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'rollback',
                                      'parameters': {'artifact': 'old-artifact'}})
        self.assertEqual(rolled['schema'], 'delivery-proposal/v2')
        grant(self.host, self.pin, 'delivery-rollback', rolled['id'])
        self.execute_delivery(rolled['id'])
        def query_and_repeat():
            with self.session().locked() as s:
                queried = merge.query(s)
                self.assertEqual(queried['provenance_binding']['record_kind'], 'legacy-unverified')
                self.assertEqual(queried['provenance_binding']['current_status'], 'UNKNOWN')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
                result = delivery.execute(s, rolled['id'])
                self.assertEqual(result['provenance_binding']['record_kind'], 'legacy-unverified')
                self.assertEqual(result['provenance_binding']['current_status'], 'UNKNOWN')
        self.assert_no_action(query_and_repeat, expected_exception=False)
        self.assertEqual(self.callback_calls, [])

    def test_required_deployment_and_rollback_missing_proof_do_not_charge(self):
        self.start(required=['delivery-deploy', 'delivery-rollback'])
        source = self.prepare_merge(provenance=False)
        self.execute_merge()
        def deploy_missing():
            with self.session().locked() as s:
                delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                    'parameters': {'artifact': source['subject']['artifact_digest']}})
        self.assert_no_action(deploy_missing)
        def rollback_missing():
            with self.session().locked() as s:
                delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'rollback',
                    'parameters': {'artifact': source['subject']['artifact_digest']},
                    'artifact_content': self.descriptor(), 'artifact_source_merge': source['id']})
        self.assert_no_action(rollback_missing)
        self.assertEqual(self.callback_calls, [])

    def test_default_cli_cannot_supply_a_verifier_for_a_new_execution(self):
        self.start()
        self.prepare_merge()
        before = self.snapshot()
        result = subprocess.run([sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
            str(ROOT / 'interfaces/lifecycle/cli.py'), '--host-root', str(self.host),
            '--policy-sha', self.pin, 'merge-execute'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual((out['status'], out['recommendation']), ('UNKNOWN', 'HOLD'))
        self.assertFalse(out['authorizes_action'])
        self.assertEqual(self.snapshot(), before)

    def test_policy_descriptor_and_raw_evidence_cannot_choose_the_trust_root(self):
        self.start()
        with self.session().locked() as s:
            for field in ('verifier_id', 'trust_root_id', 'issuer', 'repository'):
                descriptor = dict(self.provenance_descriptor(), **{field: 'attacker-choice'})
                with self.subTest(descriptor_field=field), self.assertRaises(ValueError):
                    self.capture(s, descriptor)
            self.assertEqual(self.callback_calls, [])
            s.verifiers = {}
            with self.assertRaises(ValueError):
                self.capture(s)
            self.assertEqual(self.callback_calls, [])
            s.verifiers = {'unselected-verifier': self.verifier}
            with self.assertRaises(ValueError):
                self.capture(s)
            self.assertEqual(self.callback_calls, [])
        forged = b'{"status":"verified","trust_root_id":"attacker-choice"}'
        self.evidence_file.write_bytes(forged)
        with self.session().locked() as s, self.assertRaises(ValueError):
            self.capture(s, self.provenance_descriptor(forged))
        self.assertEqual(self.callback_calls[-1][1], forged)
        self.evidence_file.write_bytes(EVIDENCE)
        with self.session().locked() as s:
            self.assertEqual(self.capture(s)['result']['status'], 'verified')

    def test_policy_bounds_and_invalid_callback_registries_fail_closed(self):
        self.start()
        with self.session().locked() as s:
            good = copy.deepcopy(s.policy['build_provenance'])
            changes = [('schema', 'unknown'), ('required_actions', ['delivery-promote']),
                       ('required_actions', ['merge-execute', 'merge-execute']),
                       ('max_age_seconds', True), ('max_age_seconds', 0),
                       ('max_age_seconds', build_provenance.MAX_AGE_SECONDS + 1),
                       ('issuer', ''), ('verifier_id', 'x' * (build_provenance.MAX_TEXT + 1))]
            for field, value in changes:
                s.policy['build_provenance'] = dict(good, **{field: value})
                with self.subTest(policy_field=field), self.assertRaises(ValueError):
                    build_provenance.policy_config(s)
            for field in build_provenance.SELECTORS:
                s.policy['build_provenance'] = {key: value for key, value in good.items() if key != field}
                with self.subTest(missing_selector=field), self.assertRaises(ValueError):
                    build_provenance.policy_config(s)
            s.policy['build_provenance'] = good
            self.assertEqual(build_provenance.policy_config(s), good)
        before = self.snapshot()
        for registry in ([], {'fixture-verifier': None}, {'fixture-verifier': 'not-callable'}, {1: self.verifier}):
            self.verifiers = registry
            with self.subTest(registry=registry), self.assertRaises(ValueError):
                self.session()
        self.verifiers = {'fixture-verifier': self.verifier}
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.callback_calls, [])

    def test_wrong_repository_run_commit_tree_config_and_artifact_are_rejected(self):
        self.start()
        with self.session().locked() as s:
            content, intent = self.context(s)
            changes = {'schema': 'unknown', 'task_id': 'another-task', 'policy_sha': 'f' * 64,
                'verifier_id': 'another-verifier', 'trust_root_id': 'another-root',
                'repository': 'another-owner/repository', 'issuer': 'unknown-issuer',
                'builder_id': 'another-builder', 'build_environment': 'another-environment',
                'candidate_tree': '2' * 40, 'build_config_digest': 'f' * 64,
                'artifact_sha256': artifact_tests.sha(artifact_tests.CHANGED),
                'size_bytes': len(artifact_tests.DATA) + 1, 'run_id': 'another-run', 'run_attempt': 2,
                'action': 'delivery-deploy', 'intent_sha256': 'f' * 64}
            for field, value in changes.items():
                self.result_mutator = lambda r, f=field, v=value: r['binding'].update({f: v})
                with self.subTest(binding_field=field), self.assertRaises(ValueError):
                    self.capture(s, content=content, intent=intent)
            for field in self.subject:
                self.result_mutator = lambda r, f=field: r['binding']['source_subject'].update({f: 'changed'})
                with self.subTest(source_subject_field=field), self.assertRaises(ValueError):
                    self.capture(s, content=content, intent=intent)
            self.result_mutator = None
            self.assertEqual(self.capture(s, content=content, intent=intent)['result']['status'], 'verified')

    def test_stored_validation_never_reads_evidence_calls_verifier_or_checks_current_time(self):
        self.start()
        with self.session().locked() as s:
            content, intent = self.context(s)
            proof = self.capture(s, content=content, intent=intent)
            historical = copy.deepcopy(proof)
            for field in ('built_at', 'checked_at', 'expires_at'):
                historical['result'][field] = (core.timestamp(historical['result'][field]) - timedelta(days=2)).isoformat()
            s.verifiers = {'fixture-verifier': lambda *_: self.fail('pure validation invoked verifier')}
            with patch.object(build_provenance, 'read_bytes', side_effect=AssertionError('historical evidence IO')), \
                    patch.object(build_provenance, 'now', side_effect=AssertionError('historical current clock')):
                validated = build_provenance.validate(s, historical, self.subject, artifact_tests.TREE,
                                                       content, 'merge-execute', intent)
            self.assertEqual(validated, historical)
            self.assertEqual(len(self.callback_calls), 1)

    def test_stored_validation_checks_context_without_an_outer_plan_id(self):
        self.start()
        with self.session().locked() as s:
            content, intent = self.context(s)
            proof = self.capture(s, content=content, intent=intent)
            count = len(self.callback_calls)
            with patch.object(build_provenance, 'read_bytes', side_effect=AssertionError('stored context read evidence')), \
                    patch.object(build_provenance, 'now', side_effect=AssertionError('stored context checked current time')):
                for action, context in (('delivery-deploy', intent),
                                        ('merge-execute', dict(intent, target='different-target'))):
                    with self.subTest(action=action, intent=context), self.assertRaisesRegex(ValueError, 'binding mismatch'):
                        build_provenance.validate(s, proof, self.subject, artifact_tests.TREE,
                                                  content, action, context)
                self.assertEqual(build_provenance.validate(s, proof, self.subject, artifact_tests.TREE,
                                                          content, 'merge-execute', intent), proof)
            self.assertEqual(len(self.callback_calls), count)

    def test_callback_cannot_mutate_the_expected_binding_or_retained_result(self):
        self.start()
        observed = []
        def mutating(request, raw):
            request['source_subject']['head'] = 'changed-by-callback'
            return self.verifier(request, raw)
        self.verifiers = {'fixture-verifier': mutating}
        with self.session().locked() as s, self.assertRaises(ValueError):
            self.capture(s)
        self.assertEqual(self.read()['subject'], self.subject)
        def retaining(request, raw):
            result = self.verifier(request, raw)
            observed.append(result)
            return result
        self.verifiers = {'fixture-verifier': retaining}
        with self.session().locked() as s:
            proof = self.capture(s)
        observed[0]['binding']['source_subject']['head'] = 'later-callback-mutation'
        observed[0]['basis'] = 'later-callback-mutation'
        self.assertEqual(proof['result']['binding']['source_subject'], self.subject)
        self.assertEqual(proof['result']['basis'], BASIS)

    def test_nonverified_forged_expired_revoked_and_malformed_results_are_unknown(self):
        self.start()
        at = core.now()
        changes = {'status': 'unverified', 'evidence_sha256': 'f' * 64, 'revoked': True,
                   'checked_at': (at + timedelta(hours=1)).isoformat(),
                   'built_at': (at - timedelta(hours=2)).isoformat(),
                   'expires_at': (at - timedelta(seconds=1)).isoformat(), 'basis': ''}
        with self.session().locked() as s:
            for field, value in changes.items():
                self.result_mutator = lambda r, f=field, v=value: r.update({f: v})
                with self.subTest(result_field=field), self.assertRaises(ValueError):
                    self.capture(s)
            for status in ('unknown', 'PASS', True, None):
                self.result_mutator = lambda r, value=status: r.update(status=value)
                with self.subTest(status=status), self.assertRaises(ValueError):
                    self.capture(s)
            for mutate in (lambda r: r.pop('binding'), lambda r: r.update(unexpected=True),
                           lambda r: r.update(revoked=0), lambda r: r.update(binding=[])):
                def apply_mutation(r, mutate=mutate):
                    mutate(r)
                self.result_mutator = apply_mutation
                with self.subTest(malformed=mutate), self.assertRaises(ValueError):
                    self.capture(s)
            self.result_mutator = None
            self.callback_error = ValueError('synthetic verifier unavailable')
            with self.assertRaises(ValueError):
                self.capture(s)
            self.callback_error = None
            self.assertEqual(self.capture(s)['result']['status'], 'verified')

    def test_current_window_rejects_ordered_future_and_expired_verifications(self):
        self.start()
        at = core.now()
        with self.session().locked() as s, patch.object(build_provenance, 'now', return_value=at):
            content, intent = self.context(s)
            def verify():
                return build_provenance.verify(s, self.provenance_descriptor(), self.subject,
                    artifact_tests.TREE, content, 'merge-execute', intent)
            for label, checked, expires in (('future', 10, 20), ('expired', -30, -1)):
                self.result_mutator = lambda r, check=checked, expiry=expires: r.update(
                    built_at=(at - timedelta(seconds=60)).isoformat(),
                    checked_at=(at + timedelta(seconds=check)).isoformat(),
                    expires_at=(at + timedelta(seconds=expiry)).isoformat())
                with self.subTest(window=label):
                    outcome = verify()
                    self.assertEqual(outcome['status'], 'unknown')
                    self.assertNotIn('proof', outcome)
            self.result_mutator = lambda r: r.update(
                built_at=(at - timedelta(seconds=60)).isoformat(),
                checked_at=(at - timedelta(seconds=1)).isoformat(),
                expires_at=(at + timedelta(seconds=20)).isoformat())
            self.assertEqual(verify()['status'], 'verified')

    def test_legacy_fresh_delivery_without_proof_does_not_resolve_a_source(self):
        self.start(legacy=True)
        proposal = {'action': 'deploy', 'source': {'merge_plan': 'unavailable-legacy-source'},
                    'parameters': {'artifact': 'legacy-label'}}
        before = self.snapshot()
        with self.session().locked() as s, patch.object(delivery, 'provenance_context',
                side_effect=AssertionError('legacy delivery resolved provenance source')):
            self.assertIsNone(delivery.fresh_provenance(s, proposal))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.callback_calls, [])

    def test_verify_classifies_outcomes_redacts_errors_and_propagates_cancellation(self):
        self.start()
        with self.session().locked() as s:
            content, intent = self.context(s)
            def verify():
                return build_provenance.verify(s, self.provenance_descriptor(), self.subject,
                    artifact_tests.TREE, content, 'merge-execute', intent)
            for status, expected in (('unverified', 'unverified'), ('unknown', 'unknown'), ('PASS', 'unknown')):
                self.result_mutator = lambda r, value=status: r.update(status=value)
                outcome = verify()
                self.assertEqual(outcome['status'], expected)
                self.assertFalse(outcome['authorizes_action'])
                self.assertEqual(outcome['cryptographic_verification'], 'UNKNOWN')
                self.assertEqual(outcome['remote_identity'], 'UNKNOWN')
                self.assertNotIn('proof', outcome)
            self.result_mutator = None
            self.callback_error = ValueError('PRIVATE fixture-only callback diagnostic')
            outcome = verify()
            self.assertEqual(outcome['status'], 'unknown')
            self.assertNotIn('PRIVATE', json.dumps(outcome))
            for failure in (CancelledError(), KeyboardInterrupt(), SystemExit(99)):
                self.callback_error = failure
                with self.subTest(cancel=type(failure).__name__), self.assertRaises(type(failure)):
                    verify()
            self.callback_error = None
            self.assertEqual(verify()['status'], 'verified')

    def test_evidence_locator_digest_and_size_fail_before_callback(self):
        self.start()
        with self.session().locked() as s:
            for path in ('', '../outside', '/absolute', './build.json', 'nested/../build.json'):
                with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                    self.capture(s, self.provenance_descriptor(path=path))
            with self.assertRaises(ValueError):
                self.capture(s, dict(self.provenance_descriptor(), evidence_sha256='f' * 64))
            for descriptor in (dict(self.provenance_descriptor(), evidence_sha256='not-a-hash'),
                               dict(self.provenance_descriptor(), size_bytes=len(EVIDENCE))):
                with self.subTest(malformed_descriptor=descriptor), self.assertRaises(ValueError):
                    self.capture(s, descriptor)
            for attempt in (True, 0, -1, '1', 2147483648):
                with self.subTest(run_attempt=attempt), self.assertRaises(ValueError):
                    self.capture(s, dict(self.provenance_descriptor(), run_attempt=attempt))
            self.assertEqual(self.callback_calls, [])
            self.evidence_file.write_bytes(b'x' * (1024 * 1024 + 1))
            with self.assertRaises((ValueError, OSError)):
                self.capture(s, self.provenance_descriptor(self.evidence_file.read_bytes()))
            self.assertEqual(self.callback_calls, [])
            self.evidence_file.write_bytes(EVIDENCE)
            self.assertEqual(self.capture(s)['result']['status'], 'verified')

    def test_evidence_hardlink_and_replaced_namespace_never_reach_callback(self):
        self.start()
        linked = self.evidence_root / 'hardlinked.json'
        os.link(self.evidence_file, linked)
        try:
            with self.session().locked() as s, self.assertRaises(ValueError):
                self.capture(s, self.provenance_descriptor(path='hardlinked.json'))
        finally:
            linked.unlink()
        parked = self.evidence_root.with_name('parked-evidence')
        self.evidence_root.rename(parked)
        try:
            if os.name == 'nt':
                result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(self.evidence_root), str(parked)],
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.evidence_root.symlink_to(parked, target_is_directory=True)
            with self.session().locked() as s, self.assertRaises(ValueError):
                self.capture(s)
        finally:
            if os.name == 'nt' and self.evidence_root.exists():
                self.evidence_root.rmdir()
            elif self.evidence_root.is_symlink():
                self.evidence_root.unlink()
            parked.rename(self.evidence_root)
        self.assertEqual(self.callback_calls, [])
        with self.session().locked() as s:
            self.assertEqual(self.capture(s)['result']['status'], 'verified')

    def test_stored_proof_and_locator_changes_need_new_plan_and_approval(self):
        self.start()
        plan = self.prepare_merge()
        self.assertEqual(plan['schema'], 'merge-proposal/v4')
        original = self.read()
        changed = copy.deepcopy(original)
        changed['merge']['build_provenance']['result']['basis'] = BASIS + ' changed'
        write(self.host, 'state.json', changed)
        self.assert_no_action(self.execute_merge)
        write(self.host, 'state.json', original)
        new_path = self.evidence_root / 'same-bytes.json'
        new_path.write_bytes(EVIDENCE)
        self.provenance_descriptor = lambda data=EVIDENCE, path='same-bytes.json': {
            'run_id': 'fixture-run-17', 'run_attempt': 1, 'evidence_path': path,
            'evidence_sha256': hashlib.sha256(data).hexdigest()}
        replacement = self.prepare_merge(approve_execute=False)
        self.assertNotEqual(plan['id'], replacement['id'])
        self.assert_no_action(self.execute_merge)
        grant(self.host, self.pin, 'merge-execute', replacement['id'])
        self.assertEqual(self.execute_merge()['result'], 'confirmed')

    def test_new_merge_rechecks_revocation_and_expiry_before_charge_or_cas(self):
        self.start()
        self.prepare_merge()
        for mutate in (lambda r: r.update(revoked=True), lambda r: r.update(status='unknown'),
                       lambda r: r.update(expires_at=(core.now() - timedelta(seconds=1)).isoformat())):
            self.result_mutator = mutate
            with self.subTest(refusal=mutate):
                self.assert_no_action(self.execute_merge)
        self.result_mutator = None
        self.assertEqual(self.execute_merge()['result'], 'confirmed')

    def test_fresh_verification_does_not_rewrite_approved_proof_or_identity(self):
        self.start()
        plan = self.prepare_merge()
        self.result_mutator = lambda r: r.update(basis=BASIS + ' fresh verification')
        self.assertEqual(self.execute_merge()['result'], 'confirmed')
        self.assertEqual(self.read()['merge'], plan)

    def test_reverification_cannot_reset_build_age_but_can_refresh_check_time(self):
        self.start()
        plan = self.prepare_merge()
        advanced_build = (core.timestamp(self.built_at) + timedelta(seconds=1)).isoformat()
        self.result_mutator = lambda r: r.update(built_at=advanced_build)
        self.assert_no_action(self.execute_merge)
        # A different spelling of the same build instant and a fresh check are
        # valid. They do not rewrite the original approved verification record.
        self.result_mutator = lambda r: r.update(built_at=self.built_at.replace('+00:00', 'Z'),
            checked_at=(core.now() - timedelta(milliseconds=1)).isoformat(),
            expires_at=(core.now() + timedelta(minutes=5)).isoformat())
        self.assertEqual(self.execute_merge()['result'], 'confirmed')
        self.assertEqual(self.read()['merge'], plan)

    def test_explicit_null_rollback_descriptor_cannot_fall_back_to_merge_proof(self):
        self.start()
        source = self.merged()
        record = {'schema': 'delivery-plan/v1', 'action': 'rollback',
                  'parameters': {'artifact': source['subject']['artifact_digest']},
                  'artifact_content': self.descriptor(), 'artifact_source_merge': source['id'],
                  'build_provenance': None}
        def explicit_null():
            with self.session().locked() as s:
                delivery.plan(s, record)
        calls = len(self.callback_calls)
        self.assert_no_action(explicit_null)
        self.assertEqual(len(self.callback_calls), calls)
        record.pop('build_provenance')
        with self.session().locked() as s:
            accepted = delivery.plan(s, record)
        self.assertEqual(accepted['build_provenance']['descriptor'], source['build_provenance']['descriptor'])

    def test_deploy_and_rollback_bind_their_own_intent_to_target_evidence(self):
        self.start()
        source = self.merged()
        proposal = self.deployment(source)
        self.assertEqual(proposal['schema'], 'delivery-proposal/v4')
        self.assertEqual(proposal['build_provenance']['descriptor'], source['build_provenance']['descriptor'])
        bound = proposal['build_provenance']['result']['binding']
        self.assertEqual(bound['action'], 'delivery-deploy')
        self.assertNotEqual(bound['intent_sha256'], source['build_provenance']['result']['binding']['intent_sha256'])
        self.assertEqual(self.execute_delivery(proposal['id'])['result']['result'], 'confirmed')
        state = self.read()
        state['merge'] = {}
        state['subject'] = dict(self.subject, head='later-current-subject')
        state['findings']['current-blocker'] = {'id': 'current-blocker', 'blocking': True, 'alias': None}
        write(self.host, 'state.json', state)
        with self.session().locked() as s:
            rollback = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'rollback',
                'parameters': {'artifact': source['subject']['artifact_digest']},
                'artifact_content': self.descriptor(), 'artifact_source_merge': source['id'],
                'build_provenance': self.provenance_descriptor()})
        self.assertEqual(rollback['build_provenance']['result']['binding']['source_subject'], source['source_subject'])
        self.assertEqual(rollback['build_provenance']['result']['binding']['action'], 'delivery-rollback')
        grant(self.host, self.pin, 'delivery-rollback', rollback['id'])
        self.result_mutator = lambda r: r.update(revoked=True)
        self.assert_no_action(lambda: self.execute_delivery(rollback['id']))
        self.result_mutator = None
        result = self.execute_delivery(rollback['id'])
        self.assertTrue(result['simulation'])
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertEqual(result['result']['consumed_content'],
                         {'sha256': artifact_tests.sha(artifact_tests.DATA), 'size_bytes': len(artifact_tests.DATA)})

    def test_enable_inherits_deployment_descriptor_and_rechecks_without_artifact_read(self):
        self.start()
        source = self.merged()
        deployed = self.deployment(source)
        self.execute_delivery(deployed['id'])
        self.artifact.unlink()
        with self.session().locked() as s, patch.object(artifacts, 'read_bytes', side_effect=AssertionError('enable read artifact')):
            enabled = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'flag-set',
                'parameters': {'enabled': True, 'cohort': 'internal'}})
        self.assertEqual(enabled['build_provenance']['descriptor'], deployed['build_provenance']['descriptor'])
        self.assertEqual(enabled['build_provenance']['result']['binding']['action'], 'delivery-flag-enable')
        grant(self.host, self.pin, 'delivery-flag-set', enabled['id'])
        self.result_mutator = lambda r: r.update(revoked=True)
        self.assert_no_action(lambda: self.execute_delivery(enabled['id']))
        self.result_mutator = None
        with patch.object(artifacts, 'read_bytes', side_effect=AssertionError('enable reopened artifact')):
            result = self.execute_delivery(enabled['id'])
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertNotIn('consumed_content', result['result'])

    def test_promote_maps_deployment_requirement_but_binds_its_literal_action(self):
        self.start()
        deployed = self.deployment(self.merged())
        self.execute_delivery(deployed['id'])
        # Observation is outside this test's scope; this does not establish health.
        with patch.object(delivery, 'observation_status', return_value={'status': 'PASS'}), \
                patch.object(artifacts, 'read_bytes', side_effect=AssertionError('promotion read artifact')):
            with self.session().locked() as s:
                promoted = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'promote',
                    'parameters': {'artifact': deployed['parameters']['artifact'], 'channel': 'fixture-stable'}})
            self.assertEqual(promoted['build_provenance']['result']['binding']['action'], 'delivery-promote')
            grant(self.host, self.pin, 'delivery-promote', promoted['id'])
            original_registry = self.verifiers
            self.verifiers = {}
            self.assert_no_action(lambda: self.execute_delivery(promoted['id']))
            self.verifiers = original_registry
            result = self.execute_delivery(promoted['id'])
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertNotIn('consumed_content', result['result'])

    def test_receipt_cannot_be_rebound_to_another_provenance_operation(self):
        self.start()
        proposal = self.deployment(self.merged())
        self.execute_delivery(proposal['id'])
        state = self.read()
        provider = self.read('sim-provider.json')
        receipt = provider['operations'][proposal['id']]
        receipt['operation'] = 'f' * 64
        state['delivery']['requests'][proposal['id']]['receipt_sha'] = core.digest(receipt)
        write(self.host, 'state.json', state)
        write(self.host, 'sim-provider.json', provider)
        def query():
            with self.session().locked() as s:
                delivery.LocalSimulation(s).query(proposal['id'])
        self.assert_no_action(query)

    def test_exact_historical_query_and_repeat_never_reverify_reexecute_or_recharge(self):
        self.start()
        source = self.merged()
        proposal = self.deployment(source)
        self.execute_delivery(proposal['id'])
        self.evidence_root.rename(self.evidence_root.with_name('removed-evidence'))
        self.artifact.unlink()
        state = self.read()
        state['subject'] = dict(self.subject, head='later-current-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']
        state['receipts'][source['id']]['result'] = 'unknown'
        request = state['delivery']['requests'][proposal['id']]
        request.update(result='pending')
        request.pop('receipt_sha'); request.pop('observed_at')
        write(self.host, 'state.json', state)
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        self.verifiers = {'fixture-verifier': lambda *_: self.fail('historical recovery called verifier')}
        def recover():
            with self.session().locked() as s, patch.object(artifacts, 'read_bytes', side_effect=AssertionError('historical artifact IO')):
                self.assertEqual(merge.query(s)['result'], 'confirmed')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
                self.assertEqual(delivery.LocalSimulation(s).query(proposal['id'])['result']['result'], 'confirmed')
                self.assertEqual(delivery.execute(s, proposal['id'])['result']['result'], 'confirmed')
                self.assertEqual(s.state['merge']['build_provenance'], source['build_provenance'])
        self.assert_no_action(recover, expected_exception=False)

    def test_actual_policy_change_fails_closed_instead_of_upgrading_old_records(self):
        self.start()
        self.prepare_merge()
        before = self.snapshot()
        changed = copy.deepcopy(self.policy)
        changed['build_provenance']['trust_root_id'] = 'different-root'
        write(self.host, 'policy.json', changed)
        with self.assertRaisesRegex(ValueError, 'policy pin mismatch'):
            self.session()
        new_pin = hashlib.sha256((self.host / 'policy.json').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'explicit migration'):
            with core.Session(self.host, new_pin, verifiers=self.verifiers).locked():
                self.fail('changed policy must not silently migrate a ledger')
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
