"""Review ordering and byte-binding regressions with complete in-memory fixtures.

Policy validation, handoff initialization, ingestion, budgets, and evaluation use
the real implementation. Only filesystem reads/persistence are replaced; no
temporary files, cleanup, Git operations, or external services are required.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import core, review


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.clock = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.files = {'raw.txt': b'Synthetic review regression evidence.\n'}
        self.reads = []
        self.after_read = None

        def read_bytes(root, name):
            self.reads.append(name)
            data = self.files[name]
            if self.after_read:
                self.after_read(name)
            return data

        for module in (core, review):
            replacement = patch.object(module, 'read_bytes', side_effect=read_bytes)
            replacement.start()
            self.addCleanup(replacement.stop)
        persistence = patch.object(core.Session, 'persist', lambda session: None)
        persistence.start()
        self.addCleanup(persistence.stop)

    def create_session(self, risk='low'):
        checks = [dict(id=name, stage='pr', gate=name, scope=['change'],
                       config='config-1', required=True, na_reason='')
                  for name in ('ci', 'semantic', 'security')]
        checks.append(dict(id='integration', stage='merge', gate='integration',
                           scope=['combination'], config='config-1', required=True, na_reason=''))
        policy = dict(schema='lifecycle-policy/v1', task_id='review-regressions',
                      risk=risk, checks=checks, budget=dict(max_rounds=10, max_operations=100),
                      humans=['fixture-human'], reviewers=['reviewer-a', 'reviewer-b'],
                      high_expert_count=2, low_auto=True,
                      allowed_actions=['pr-approve'], merge={}, delivery={})
        self.files['policy.json'] = json.dumps(policy).encode()
        pin = hashlib.sha256(self.files['policy.json']).hexdigest()
        session = core.Session(ROOT, pin)
        identity = {name: 'fixture-' + name for name in core.SUBJECT}
        session.initialize(dict(schema='pre-pr-handoff/v1', task_id=policy['task_id'],
                                subject=identity, budget=dict(rounds=0, operations=0),
                                requirements_ref='synthetic requirement',
                                history_ref='synthetic pre-PR history', findings=[]))
        session.charge(rounds=1)
        session.state['round_subject'] = core.digest(identity)
        self.files['authority.json'] = json.dumps(dict(schema='host-authority/v1', decisions=[
            dict(id='fixture-approval', actor='fixture-human', kind='human', action='pr-approve',
                 binding=core.digest(identity), policy_sha=pin, decision='approve',
                 expires_at=(self.clock + timedelta(hours=1)).isoformat())])).encode()
        baseline = [self.evidence(session, name, 'baseline-' + name, -3)
                    for name in ('ci', 'semantic', 'security')]
        if risk == 'high':
            baseline.append(self.evidence(session, 'semantic', 'baseline-expert-b', -3,
                                          reviewer='reviewer-b', context='context-b'))
        result = self.ingest(session, baseline)
        self.assertEqual({c['id']: c['status'] for c in result['checks']},
                         {'ci': 'PASS', 'semantic': 'PASS', 'security': 'PASS'})
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')
        return session

    def evidence(self, session, check, identifier, minute, status='PASS',
                 reviewer='reviewer-a', context='context-a', provider='fixture-provider'):
        return dict(id=identifier, check=check, subject=copy.deepcopy(session.state['subject']),
                    config='config-1', scope=['change'], status=status, complete=True,
                    observed_at=(self.clock + timedelta(minutes=minute)).isoformat(),
                    expires_at=(self.clock + timedelta(hours=1)).isoformat(),
                    provider=provider, run='run-' + identifier, job=check,
                    attempt='opaque-' + identifier, tool_version='fixture-1',
                    context_id=context, reviewer=reviewer,
                    raw=dict(path='raw.txt', sha256=hashlib.sha256(self.files['raw.txt']).hexdigest()))

    def ingest(self, session, entries):
        return review.ingest(session, ROOT,
                             dict(schema='review-batch/v1', evidence=entries, findings=[]))

    def check(self, result, identifier='semantic'):
        return next(c for c in result['checks'] if c['id'] == identifier)

    def test_new_failure_survives_late_old_pass_in_both_batch_orders(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                session = self.create_session()
                records = [self.evidence(session, 'semantic', 'old-pass', -2),
                           self.evidence(session, 'semantic', 'new-fail', -1, 'FAIL')]
                if reverse:
                    records.reverse()
                result = self.ingest(session, records)
                self.assertEqual(self.check(result), dict(id='semantic', status='FAIL', evidence=['new-fail'], required=True, blocking=True))
                self.assertEqual(result['recommendation'], 'HOLD')
                self.assertEqual(len(session.state['evidence']), 5)

    def test_new_pass_recovers_even_when_old_failure_arrives_in_later_batch(self):
        session = self.create_session()
        self.ingest(session, [self.evidence(session, 'semantic', 'new-pass', -1)])
        result = self.ingest(session, [self.evidence(session, 'semantic', 'late-old-fail', -2, 'FAIL')])
        self.assertEqual(self.check(result), dict(id='semantic', status='PASS', evidence=['new-pass'], required=True, blocking=False))
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')

    def test_conflicting_same_instant_observations_are_unknown_in_either_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                session = self.create_session()
                records = [self.evidence(session, 'semantic', 'same-pass', -1),
                           self.evidence(session, 'semantic', 'same-fail', -1, 'FAIL')]
                if reverse:
                    records.reverse()
                result = self.ingest(session, records)
                self.assertEqual(self.check(result), dict(id='semantic', status='UNKNOWN',
                                                         evidence=['same-fail', 'same-pass'], required=True, blocking=True))
                self.assertEqual(result['recommendation'], 'HOLD')

    def test_equivalent_same_instant_observations_preserve_pass(self):
        session = self.create_session()
        records = [self.evidence(session, 'semantic', name, -1) for name in ('same-a', 'same-b')]
        result = self.ingest(session, records)
        self.assertEqual(self.check(result), dict(id='semantic', status='PASS', evidence=['same-a', 'same-b'], required=True, blocking=False))
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')

    def test_another_provider_pass_does_not_replace_failure(self):
        session = self.create_session()
        result = self.ingest(session, [self.evidence(session, 'semantic', 'source-fail', -2, 'FAIL'),
                                     self.evidence(session, 'semantic', 'other-pass', -1, provider='other')])
        self.assertEqual(self.check(result)['status'], 'FAIL')
        self.assertEqual(set(self.check(result)['evidence']), {'source-fail', 'other-pass'})
        self.assertEqual(result['recommendation'], 'HOLD')

    def test_high_risk_old_pass_cannot_restore_failed_expert(self):
        session = self.create_session('high')
        records = [self.evidence(session, 'semantic', 'expert-b-fail', -1, 'FAIL', 'reviewer-b', 'context-b'),
                   self.evidence(session, 'semantic', 'expert-b-old-pass', -2, 'PASS', 'reviewer-b', 'context-b')]
        result = self.ingest(session, records)
        self.assertEqual(self.check(result)['status'], 'FAIL')
        self.assertEqual(result['reasons'], ['high-risk expert coverage missing'])
        self.assertEqual(result['recommendation'], 'HOLD')

    def test_high_risk_new_pass_restores_expert_despite_late_old_failure(self):
        session = self.create_session('high')
        records = [self.evidence(session, 'semantic', 'expert-b-pass', -1, 'PASS', 'reviewer-b', 'context-b'),
                   self.evidence(session, 'semantic', 'expert-b-old-fail', -2, 'FAIL', 'reviewer-b', 'context-b')]
        result = self.ingest(session, records)
        self.assertEqual(self.check(result)['status'], 'PASS')
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')

    def test_high_risk_tied_conflict_does_not_count_as_expert(self):
        session = self.create_session('high')
        result = self.ingest(session, [self.evidence(session, 'semantic', 'expert-b-fail', -1, 'FAIL', 'reviewer-b', 'context-b'),
                                     self.evidence(session, 'semantic', 'expert-b-pass', -1, 'PASS', 'reviewer-b', 'context-b')])
        self.assertEqual(self.check(result)['status'], 'UNKNOWN')
        self.assertEqual(result['reasons'], ['high-risk expert coverage missing'])
        self.assertEqual(result['recommendation'], 'HOLD')

    def security_record(self, session):
        identity = copy.deepcopy(session.state['subject'])
        request = dict(expected_subject=identity, valid_until_utc=(self.clock + timedelta(hours=1)).isoformat(),
                       request_id='fixture-security-request', expected_tool=dict(name='fixture-tool', version='1'))
        return dict(schema='security-evidence/v1', authorizes_progress=False, status='PASS',
                    subject=identity, config_digest='config-1', scope=['change'], complete=True,
                    request_binding=request, observed_at_utc=self.clock.isoformat(),
                    tool=request['expected_tool'], findings=[])

    def test_security_fields_and_digest_bind_the_same_bytes(self):
        session = self.create_session()
        raw = json.dumps(self.security_record(session)).encode()
        self.files['B.json'] = raw
        result = review.import_security(session, ROOT, dict(schema='security-import/v1', check='security', record='B.json'))
        imported = session.state['evidence'][-1]
        self.assertEqual(imported['status'], 'PASS')
        self.assertEqual(imported['id'], 'B-' + hashlib.sha256(raw).hexdigest())
        self.assertEqual(imported['raw']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.reads.count('B.json'), 2)  # Parse once, then ingestion integrity check.
        self.assertEqual(self.check(result, 'security')['status'], 'PASS')
        self.assertEqual(result['recommendation'], 'READY_FOR_NEXT')

    def test_security_file_change_between_parse_and_import_is_rejected(self):
        session = self.create_session()
        record = self.security_record(session)
        self.files['B.json'] = json.dumps(record).encode()
        changed = dict(record, status='UNKNOWN', findings=[dict(id='new-candidate')])

        def replace_after_first_read(name):
            if name == 'B.json':
                self.files[name] = json.dumps(changed).encode()

        self.after_read = replace_after_first_read
        previous_evidence = copy.deepcopy(session.state['evidence'])
        with self.assertRaisesRegex(ValueError, '^raw evidence digest mismatch$'):
            review.import_security(session, ROOT, dict(schema='security-import/v1', check='security', record='B.json'))
        self.assertEqual(session.state['evidence'], previous_evidence)
        self.assertEqual(session.state['findings'], {})
        self.assertEqual(self.reads.count('B.json'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
