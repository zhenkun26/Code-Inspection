"""Host applicability, advisory visibility, and historical recovery regressions."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import core, delivery, merge, review
from validation.lifecycle import test_lifecycle as fixtures
from validation.lifecycle.fixture import write
from validation.lifecycle import test_review_regressions as review_fixtures


class Applicability(unittest.TestCase):
    # Real Session validation/ingestion with only byte storage replaced.
    setUp = review_fixtures.ReviewRegressions.setUp
    evidence = review_fixtures.ReviewRegressions.evidence
    ingest = review_fixtures.ReviewRegressions.ingest
    check = review_fixtures.ReviewRegressions.check

    def session(self, reason='', risk='low', optional=False, record=None):
        checks = [dict(id=name, stage='pr', gate=name, scope=['change'], config='config-1',
                       required=True, na_reason=reason)
                  for name in ('ci', 'semantic', 'security')]
        checks.append(dict(id='integration', stage='merge', gate='integration',
                           scope=['combination'], config='config-1', required=True, na_reason=reason))
        if optional:
            checks.append(dict(id='advisory', stage='pr', gate='ci', scope=['change'],
                               config='config-1', required=False, na_reason=''))
        if record is not None:
            for check in checks:
                check['applicability'] = copy.deepcopy(record)
        policy = dict(schema='lifecycle-policy/v1', task_id='applicability-regressions',
                      risk=risk, checks=checks, budget=dict(max_rounds=5, max_operations=40),
                      humans=['fixture-human'], reviewers=['reviewer-a', 'reviewer-b'],
                      high_expert_count=2, low_auto=True, allowed_actions=['pr-approve'],
                      merge={}, delivery={})
        raw = json.dumps(policy).encode()
        self.files['policy.json'] = raw
        self.files['authority.json'] = json.dumps(dict(schema='host-authority/v1', decisions=[])).encode()
        session = core.Session(ROOT, hashlib.sha256(raw).hexdigest())
        identity = {name: 'fixture-' + name for name in core.SUBJECT}
        session.initialize(dict(schema='pre-pr-handoff/v1', task_id=policy['task_id'], subject=identity,
                                budget=dict(rounds=0, operations=0), requirements_ref='synthetic requirement',
                                history_ref='synthetic history', findings=[]))
        session.charge(rounds=1)
        session.state['round_subject'] = core.digest(identity)
        return session

    def attestation(self, session, check):
        return dict(schema='check-applicability/v1', reason='No relevant surface in this change',
                    basis='Host examined the scoped change and caller inventory',
                    decider='fixture-host-reviewer', source_subject_digest=core.digest(session.state['subject']),
                    scope=copy.deepcopy(check['scope']))

    def attest(self, session):
        for check in session.policy['checks']:
            check['applicability'] = self.attestation(session, check)

    def pass_pr(self, session):
        return self.ingest(session, [self.evidence(session, name, 'pass-' + name, -1)
                                    for name in ('ci', 'semantic', 'security')])

    def test_empty_legacy_reason_requires_evidence_and_preserves_pass(self):
        session = self.session()
        for stage in ('pr', 'merge'):
            with self.subTest(stage=stage):
                result = review.evaluate(session, stage)
                self.assertEqual({c['status'] for c in result['checks']}, {'NOT_RUN'})
                self.assertEqual(result['recommendation'], 'HOLD')
        self.assertEqual(self.pass_pr(session)['recommendation'], 'READY_FOR_NEXT')

    def test_legacy_reason_and_whitespace_are_unknown_even_with_pass(self):
        for reason in (' ', '\t\n', 'Host previously said not applicable'):
            with self.subTest(reason=repr(reason)):
                session = self.session(reason)
                for stage in ('pr', 'merge'):
                    result = review.evaluate(session, stage)
                    self.assertEqual({c['status'] for c in result['checks']}, {'UNKNOWN'})
                    self.assertTrue(all(c['blocking'] for c in result['checks']))
                    self.assertIn('reattestation', result['checks'][0]['reason'])
                    self.assertEqual(result['recommendation'], 'HOLD')
                self.assertEqual(self.pass_pr(session)['recommendation'], 'HOLD')

    def test_valid_attestation_preserves_provenance_without_evidence(self):
        session = self.session()
        self.attest(session)
        before = copy.deepcopy(session.policy)
        result = review.evaluate(session)
        self.assertEqual(session.state['evidence'], [])
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')
        for item in result['checks']:
            check = next(c for c in session.policy['checks'] if c['id'] == item['id'])
            self.assertEqual(item, dict(id=check['id'], status='N_A', required=True, blocking=False,
                                       reason=check['applicability']['reason'], applicability=check['applicability'],
                                       policy_sha=session.pin, scope=check['scope'], config=check['config']))
        result['checks'][0]['applicability']['basis'] = 'output-only mutation'
        result['checks'][0]['scope'].append('output-only scope')
        self.assertEqual(session.policy, before)

    def test_structured_record_loads_and_malformed_records_hold(self):
        session = self.session()
        record = self.attestation(session, session.policy['checks'][0])
        loaded = self.session(record=record)
        self.assertEqual(self.check(review.evaluate(loaded), 'ci')['status'], 'N_A')
        self.assertEqual(self.check(review.evaluate(loaded, 'merge'), 'integration')['status'], 'UNKNOWN')
        invalid = [None, [], {}, dict(record, extra=True), dict(record, schema='unknown')]
        for key in record:
            incomplete = copy.deepcopy(record)
            del incomplete[key]
            invalid.append(incomplete)
        for key in ('reason', 'basis', 'decider', 'source_subject_digest'):
            for value in ('', ' \t\n', [], False):
                invalid.append(dict(record, **{key: value}))
        for value in ([], ['other'], ['change', 'change'], ['change', 'other'], 'change', [None]):
            invalid.append(dict(record, scope=value))
        for value in invalid:
            with self.subTest(record=value):
                # Loading remains possible for safe exact historical queries.
                loaded = self.session(record=value if value is not None else {})
                loaded.policy['checks'][0]['applicability'] = value
                self.assertEqual(self.check(review.evaluate(loaded), 'ci')['status'], 'UNKNOWN')
                self.assertEqual(review.evaluate(loaded)['recommendation'], 'HOLD')

    def test_conflicting_legacy_reason_cannot_be_overridden_by_record(self):
        session = self.session('legacy reason')
        self.attest(session)
        self.assertEqual({c['status'] for c in review.evaluate(session)['checks']}, {'UNKNOWN'})

    def test_changed_subject_invalidates_attestation_without_rewriting_it(self):
        session = self.session()
        self.attest(session)
        original = copy.deepcopy(session.policy)
        changed = dict(session.state['subject'], worktree_digest='changed-source')
        review.advance(session, dict(schema='subject-change/v1', subject=changed,
                                     impact='scoped caller changed', reused_evidence=[]))
        self.assertEqual({c['status'] for c in review.evaluate(session)['checks']}, {'UNKNOWN'})
        self.assertEqual(session.policy, original)
        self.assertEqual(session.state['budget'], dict(rounds=1, operations=2))

    def test_changed_check_scope_invalidates_attestation(self):
        session = self.session()
        self.attest(session)
        for scope in (['other'], ['change', 'other'], ['change', 'change']):
            session.policy['checks'][0]['scope'] = scope
            self.assertEqual(self.check(review.evaluate(session), 'ci')['status'], 'UNKNOWN')

    def test_merge_attestation_needs_explicit_current_source(self):
        session = self.session()
        self.attest(session)
        combination = dict(session.state['subject'], head='combination', worktree_digest='combined-tree')
        for source in (None, dict(session.state['subject'], head='stale-source')):
            with self.subTest(source=source):
                result = review.evaluate(session, 'merge', combination, source_identity=source)
                self.assertEqual(self.check(result, 'integration')['status'], 'UNKNOWN')
                self.assertEqual(result['recommendation'], 'HOLD')
        result = review.evaluate(session, 'merge', combination, source_identity=session.state['subject'])
        self.assertEqual(self.check(result, 'integration')['status'], 'N_A')
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')

    def test_imported_na_cannot_attest_applicability(self):
        session = self.session()
        result = self.ingest(session, [self.evidence(session, name, 'na-' + name, -1, 'N_A')
                                       for name in ('ci', 'semantic', 'security')])
        self.assertEqual({c['status'] for c in result['checks']}, {'UNKNOWN'})
        self.assertEqual(result['recommendation'], 'HOLD')
        self.assertEqual({e['status'] for e in session.state['evidence']}, {'N_A'})

    def test_na_does_not_satisfy_human_or_expert_conditions(self):
        session = self.session(risk='high')
        self.attest(session)
        result = review.evaluate(session)
        self.assertEqual({c['status'] for c in result['checks']}, {'N_A'})
        self.assertIn('valid human decision missing', result['reasons'])
        self.assertIn('high-risk expert coverage missing', result['reasons'])
        self.assertEqual(result['recommendation'], 'HOLD')
        self.assertFalse(result['authorizes_action'])

    def test_na_does_not_close_formal_findings(self):
        session = self.session()
        self.attest(session)
        session.state['findings']['F1'] = dict(id='F1', blocking=True, alias=None)
        result = review.evaluate(session)
        self.assertEqual(result['reasons'], ['unresolved finding F1'])
        self.assertEqual(result['recommendation'], 'HOLD')

    def test_optional_failure_visible_without_blocking_and_required_control(self):
        session = self.session(optional=True)
        self.pass_pr(session)
        result = self.ingest(session, [self.evidence(session, 'advisory', 'advisory-fail', -1, 'FAIL')])
        self.assertEqual(self.check(result, 'advisory'),
                         dict(id='advisory', status='FAIL', required=False, blocking=False, evidence=['advisory-fail']))
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')
        session.policy['checks'][-1]['required'] = True
        result = review.evaluate(session)
        self.assertTrue(self.check(result, 'advisory')['blocking'])
        self.assertEqual(result['recommendation'], 'HOLD')

    def test_optional_missing_unknown_and_na_remain_visible(self):
        session = self.session(optional=True)
        self.pass_pr(session)
        for reason, expected in (('', 'NOT_RUN'), ('legacy reason', 'UNKNOWN')):
            session.policy['checks'][-1]['na_reason'] = reason
            result = review.evaluate(session)
            self.assertEqual(self.check(result, 'advisory')['status'], expected)
            self.assertFalse(self.check(result, 'advisory')['blocking'])
            self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')
        check = session.policy['checks'][-1]
        check.update(na_reason='', applicability=self.attestation(session, check))
        result = review.evaluate(session)
        self.assertEqual(self.check(result, 'advisory')['status'], 'N_A')
        session.state['findings']['F1'] = dict(id='F1', blocking=True, alias=None)
        self.assertEqual(review.evaluate(session)['recommendation'], 'HOLD')


class ApplicabilityRecovery(unittest.TestCase):
    setUp = fixtures.Lifecycle.setUp
    run_cli = fixtures.Lifecycle.run_cli
    edit_policy = fixtures.Lifecycle.edit_policy
    initialize = fixtures.Lifecycle.initialize
    batch = fixtures.Lifecycle.batch
    approval = fixtures.Lifecycle.approval
    ready = fixtures.Lifecycle.ready
    prepare = fixtures.Lifecycle.prepare
    merge_ready = fixtures.Lifecycle.merge_ready

    def test_merge_preflight_uses_validated_source_and_exact_authority(self):
        record = dict(schema='check-applicability/v1', reason='No integration surface',
                      basis='Host checked the scoped combination inputs', decider='fixture-host',
                      source_subject_digest=core.digest(self.subject), scope=['combination'])
        self.edit_policy(lambda p: p['checks'][-1].update(applicability=record))
        plan = self.merge_ready()
        result = self.run_cli('merge-preflight')
        self.assertEqual(result['checks'][0]['status'], 'N_A')
        self.assertEqual(result['checks'][0]['applicability'], record)
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')
        write(self.host, 'authority.json', dict(schema='host-authority/v1', decisions=[]))
        self.assertEqual(self.run_cli('merge-preflight')['recommendation'], 'HOLD')
        before = (self.host / 'state.json').read_bytes()
        self.run_cli('merge-execute', code=2)
        self.assertEqual((self.host / 'state.json').read_bytes(), before)
        self.assertEqual(merge.LocalGit(self.policy['merge']).target(), plan['base'])

    def test_legacy_policy_preserves_exhausted_exact_attempt_recovery(self):
        self.edit_policy(lambda p: [c.update(na_reason='historical host reason') for c in p['checks']])
        self.initialize()
        self.run_cli('begin-review')
        self.approval('pr-approve', core.digest(self.subject))
        binding = core.digest(dict(repository=self.f['repo'], base=self.subject['base'],
                                   head=self.subject['head'], policy_sha=self.pin))
        self.approval('merge-prepare', binding)
        # Model the prior runner's proven reason-only gate interpretation only
        # while producing historical exact-authorized attempts. Plan, authority,
        # physical Git, request and receipt validation stay real throughout.
        def historical_checks(session, stage, identity, source_identity=None):
            return [dict(id=c['id'], status='N_A', reason=c['na_reason'])
                    for c in session.policy['checks'] if c['stage'] == stage and c['required']]

        with patch.object(review, 'checks', side_effect=historical_checks):
            with core.Session(self.host, self.pin).locked() as session:
                plan = merge.prepare(session, dict(schema='merge-plan/v1', base=self.subject['base'],
                                                    head=self.subject['head'], integration_artifact='artifact-combined'))['plan']
                self.approval('merge-approve', core.digest(plan['subject']))
                self.approval('merge-execute', plan['id'])
                self.assertEqual(merge.execute(session)['result'], 'confirmed')
                proposal = delivery.plan(session, dict(schema='delivery-plan/v1', action='deploy',
                                                       parameters=dict(artifact='artifact-combined')))
                self.approval('delivery-deploy', proposal['id'])
                self.assertEqual(delivery.execute(session, proposal['id'])['result']['result'], 'confirmed')
                session.state['budget'] = dict(operations=self.policy['budget']['max_operations'],
                                              rounds=self.policy['budget']['max_rounds'])
                session.persist()
        write(self.host, 'authority.json', dict(schema='host-authority/v1', decisions=[]))
        self.assertEqual(self.run_cli('pr-status')['recommendation'], 'REPLAN')
        result = self.run_cli('merge-preflight')
        self.assertEqual(result['recommendation'], 'REPLAN')
        self.assertEqual({c['status'] for c in result['checks']}, {'UNKNOWN'})
        for attempted_result in ('confirmed', 'pending', 'unknown'):
            with self.subTest(attempted_result=attempted_result):
                ledger = json.loads((self.host / 'state.json').read_text(encoding='utf-8'))
                ledger['receipts'][plan['id']]['result'] = attempted_result
                request = ledger['delivery']['requests'][proposal['id']]
                request['result'] = attempted_result
                if attempted_result != 'confirmed':
                    request.pop('receipt_sha', None)
                    request.pop('observed_at', None)
                write(self.host, 'state.json', ledger)
                before = {name: (self.host / name).read_bytes()
                          for name in ('policy.json', 'authority.json', 'state.json', 'sim-provider.json')}
                for command in ('merge-query', 'merge-execute'):
                    self.assertEqual(self.run_cli(command)['result'], 'confirmed')
                for command in ('delivery-query', 'delivery-execute'):
                    self.assertEqual(self.run_cli(command, operation=proposal['id'])['result']['result'], 'confirmed')
                with core.Session(self.host, self.pin).locked() as session:
                    with self.assertRaisesRegex(ValueError, 'PR gate incomplete'):
                        merge.prepare(session, dict(schema='merge-plan/v1', base=self.subject['base'],
                                                    head=self.subject['head'], integration_artifact='new-artifact'))
                    with self.assertRaisesRegex(ValueError, 'unresolved PR evidence or finding'):
                        delivery.plan(session, dict(schema='delivery-plan/v1', action='deploy',
                                                    parameters=dict(artifact='artifact-combined')))
                for name, raw in before.items():
                    self.assertEqual((self.host / name).read_bytes(), raw, name)
        self.assertEqual(merge.LocalGit(self.policy['merge']).target(), plan['candidate'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
