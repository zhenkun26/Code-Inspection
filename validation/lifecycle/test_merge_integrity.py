"""Exact merge intent, Git facts, and receipt association in self-owned fixtures."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from interfaces.lifecycle import merge
from interfaces.lifecycle.core import Session, digest
from validation.lifecycle import test_lifecycle as fixture_tests
from validation.lifecycle.fixture import evidence, write


class MergeIntegrity(unittest.TestCase):
    # Reuse fixture helpers without inheriting/discovering the original test class.
    setUp = fixture_tests.Lifecycle.setUp
    run_cli = fixture_tests.Lifecycle.run_cli
    initialize = fixture_tests.Lifecycle.initialize
    batch = fixture_tests.Lifecycle.batch
    approval = fixture_tests.Lifecycle.approval
    ready = fixture_tests.Lifecycle.ready
    prepare = fixture_tests.Lifecycle.prepare
    merge_ready = fixture_tests.Lifecycle.merge_ready

    def state(self):
        return json.loads((self.host / 'state.json').read_text(encoding='utf-8'))

    def store(self, state):
        write(self.host, 'state.json', state)

    def refs(self):
        result = merge.LocalGit(self.policy['merge']).run('for-each-ref', '--format=%(refname) %(objectname)')
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split() for line in result.stdout.splitlines())

    def assert_rejected(self, entrypoints=('preflight', 'query', 'execute')):
        """Rejection must precede budget/receipt/ref changes, including on retries."""
        g = merge.LocalGit(self.policy['merge'])
        before_state = (self.host / 'state.json').read_bytes()
        before_authority = (self.host / 'authority.json').read_bytes()
        before_refs = self.refs()
        for name in entrypoints:
            with self.subTest(entrypoint=name):
                try:
                    with Session(self.host, self.pin).locked() as session:
                        with self.assertRaises(ValueError):
                            getattr(merge, name)(session)
                    self.assertEqual((self.host / 'state.json').read_bytes(), before_state)
                    self.assertEqual((self.host / 'authority.json').read_bytes(), before_authority)
                    self.assertEqual(self.refs(), before_refs)
                finally:
                    # A vulnerable baseline can mutate these isolated fixture refs.
                    # Restore them so every entrypoint is independently exercised.
                    (self.host / 'state.json').write_bytes(before_state)
                    for ref in set(self.refs()) - set(before_refs):
                        self.assertEqual(g.run('update-ref', '-d', ref).returncode, 0)
                    for ref, oid in before_refs.items():
                        self.assertEqual(g.run('update-ref', ref, oid).returncode, 0)

    def rehash(self, plan):
        plan['id'] = digest({k: v for k, v in plan.items() if k != 'id'})
        return plan

    def authorize_combination(self, plan):
        state = self.state()
        state['merge'] = plan
        self.store(state)
        check = next(c for c in self.policy['checks'] if c['stage'] == 'merge')
        self.batch([evidence(self.inputs, plan['subject'], check, 'combination-' + plan['id'])])
        self.approval('merge-approve', digest(plan['subject']))
        self.approval('merge-execute', plan['id'])

    def candidate(self, plan, *, tree=None, parents=None):
        g = merge.LocalGit(self.policy['merge'])
        args = ['commit-tree', tree or plan['tree']]
        for parent in parents if parents is not None else [plan['base'], plan['head']]:
            args += ['-p', parent]
        result = g.run(*args, input='Independent integrity fixture candidate\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def pending_attempt(self, plan):
        real = merge.LocalGit.run
        def lost_before(g, *args, **kwargs):
            if args[0] == 'update-ref':
                raise subprocess.TimeoutExpired(args, 10)
            return real(g, *args, **kwargs)
        with Session(self.host, self.pin).locked() as session, patch.object(merge.LocalGit, 'run', lost_before):
            merge.execute(session)
        self.assertEqual(merge.LocalGit(self.policy['merge']).target(), plan['base'])
        return self.state()['receipts'][plan['id']]

    def test_happy_merge_and_repeat_preserve_budget(self):
        plan = self.merge_ready()
        self.assertEqual(self.run_cli('merge-preflight')['recommendation'], 'READY_FOR_NEXT')
        result = self.run_cli('merge-execute')
        self.assertEqual(result['result'], 'confirmed')
        self.assertEqual(result['actual_target'], plan['candidate'])
        before = (self.host / 'state.json').read_bytes()
        self.assertEqual(self.run_cli('merge-query')['result'], 'confirmed')
        self.assertEqual(self.run_cli('merge-execute')['result'], 'confirmed')
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_candidate_only_tamper_keeps_original_authority(self):
        plan = self.merge_ready()
        state = self.state()
        state['merge']['candidate'] = self.candidate(plan, parents=[])
        self.store(state)
        self.assert_rejected()

    def test_every_plan_field_is_digest_bound(self):
        plan = self.merge_ready()
        original = self.state()
        mutations = {
            'repository': str(Path(self.f['repo']).parent / 'other.git'),
            'target_ref': 'refs/heads/other', 'method': 'squash',
            'base': plan['head'], 'head': plan['base'],
            'candidate': plan['head'], 'tree': '1' * 40,
            'policy_sha': '2' * 64, 'id': '3' * 64,
            'subject': dict(plan['subject'], artifact_digest='changed'),
            'source_subject': dict(plan['source_subject'], ci_config_digest='changed'),
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                state = copy.deepcopy(original)
                state['merge'][field] = value
                self.store(state)
                self.assert_rejected()

    def test_rehashed_plan_still_requires_exact_execution_approval(self):
        plan = self.merge_ready()
        plan['subject']['artifact_digest'] = 'different-combination-artifact'
        self.rehash(plan)
        state = self.state(); state['merge'] = plan; self.store(state)
        # Matching integration/human review alone is insufficient for a new id.
        check = next(c for c in self.policy['checks'] if c['stage'] == 'merge')
        self.batch([evidence(self.inputs, plan['subject'], check, 'new-integration')])
        self.approval('merge-approve', digest(plan['subject']))
        self.assertEqual(self.run_cli('merge-preflight')['recommendation'], 'HOLD')
        before = (self.host / 'state.json').read_bytes()
        self.run_cli('merge-execute', code=2)
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_rehashed_policy_ref_and_method_substitutions_are_rejected(self):
        plan = self.merge_ready()
        original = self.state()
        g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', 'refs/heads/other', plan['base']).returncode, 0)
        for field, value in [('repository', self.f['repo'] + '/.'),
                             ('target_ref', 'refs/heads/other'), ('target_ref', 'HEAD'),
                             ('target_ref', 'not a valid ref'), ('method', 'squash'),
                             ('policy_sha', '5' * 64)]:
            with self.subTest(field=field, value=value):
                state = copy.deepcopy(original); state['merge'][field] = value
                self.rehash(state['merge']); self.store(state)
                self.approval('merge-execute', state['merge']['id'])
                self.assert_rejected()

    def test_non_commit_zero_missing_and_revision_oids_are_rejected(self):
        plan = self.merge_ready()
        original = self.state()
        g = merge.LocalGit(self.policy['merge'])
        blob = g.run('hash-object', '-w', '--stdin', input='not a commit\n').stdout.strip()
        tag = g.run('mktag', input='object ' + plan['head'] + '\ntype commit\ntag fixture\n'
                    'tagger Fixture <fixture@example.invalid> 1 +0000\n\nSynthetic tag\n')
        self.assertEqual(tag.returncode, 0, tag.stderr)
        for value in ['0' * 40, 'f' * 40, 'HEAD', 'refs/heads/main', blob, plan['tree'], tag.stdout.strip()]:
            with self.subTest(candidate=value):
                state = copy.deepcopy(original)
                state['merge']['candidate'] = value
                state['merge']['subject']['head'] = value
                self.rehash(state['merge']); self.store(state)
                self.approval('merge-execute', state['merge']['id'])
                self.assert_rejected()

    def test_exact_candidate_tree_and_ordered_parents_are_required(self):
        plan = self.merge_ready()
        original = self.state()
        g = merge.LocalGit(self.policy['merge'])
        wrong_tree = g.run('rev-parse', plan['base'] + '^{tree}').stdout.strip()
        variants = [('root', self.candidate(plan, parents=[]), plan['tree']),
                    ('reversed', self.candidate(plan, parents=[plan['head'], plan['base']]), plan['tree']),
                    ('one-parent', self.candidate(plan, parents=[plan['base']]), plan['tree']),
                    ('extra-parent', self.candidate(plan, parents=[plan['base'], plan['head'], plan['candidate']]), plan['tree']),
                    ('wrong-tree', self.candidate(plan, tree=wrong_tree), plan['tree'])]
        for label, candidate, tree in variants:
            with self.subTest(case=label):
                state = copy.deepcopy(original); altered = state['merge']
                altered.update(candidate=candidate, tree=tree)
                altered['subject'].update(head=candidate, worktree_digest='tree:' + tree)
                self.rehash(altered); self.store(state); self.authorize_combination(altered)
                self.assert_rejected()

    def test_coherent_wrong_merge_tree_is_rejected_before_new_attempt(self):
        plan = self.merge_ready()
        g = merge.LocalGit(self.policy['merge'])
        wrong_tree = g.run('rev-parse', plan['base'] + '^{tree}').stdout.strip()
        plan.update(candidate=self.candidate(plan, tree=wrong_tree), tree=wrong_tree)
        plan['subject'].update(head=plan['candidate'], worktree_digest='tree:' + wrong_tree)
        self.rehash(plan); self.authorize_combination(plan)
        self.assert_rejected(('preflight', 'execute'))

    def test_source_and_combination_subjects_must_match_plan(self):
        plan = self.merge_ready(); original = self.state()
        for field, value in [('base', plan['head']), ('head', plan['base']),
                             ('tree', plan['base']), ('source_subject', dict(plan['source_subject'], base=plan['head'])),
                             ('subject', dict(plan['subject'], ci_config_digest='other-config'))]:
            with self.subTest(field=field):
                state = copy.deepcopy(original); state['merge'][field] = value
                self.rehash(state['merge']); self.store(state)
                self.approval('merge-execute', state['merge']['id'])
                self.assert_rejected()

    def test_unknown_missing_and_malformed_plan_fields_are_rejected(self):
        self.merge_ready(); original = self.state()
        variants = []
        for key in original['merge']:
            state = copy.deepcopy(original); del state['merge'][key]; variants.append((key, state))
        for key, value in [('extra', True), ('subject', []), ('source_subject', None), ('candidate', 1)]:
            state = copy.deepcopy(original); state['merge'][key] = value; variants.append((key, state))
        for key, state in variants:
            with self.subTest(field=key):
                self.store(state); self.assert_rejected()

    def test_exact_pending_receipt_reconciles_lost_response(self):
        plan = self.merge_ready(); receipt = self.pending_attempt(plan)
        self.assertIn(receipt['result'], {'pending', 'unknown'})
        g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', g.ref, plan['candidate'], plan['base']).returncode, 0)
        for attempted_result in ['pending', 'unknown']:
            with self.subTest(receipt_result=attempted_result):
                state = self.state(); state['receipts'][plan['id']]['result'] = attempted_result; self.store(state)
                before = (self.host / 'state.json').read_bytes()
                for command in ['merge-query', 'merge-execute']:
                    out = self.run_cli(command)
                    self.assertEqual(out['result'], 'confirmed')
                    self.assertEqual(out['receipt']['result'], attempted_result)
                self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_mismatched_or_empty_receipts_never_confirm_or_retry(self):
        plan = self.merge_ready(); receipt = self.pending_attempt(plan)
        original = self.state(); g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', g.ref, plan['candidate'], plan['base']).returncode, 0)
        variants = [('base', plan['head']), ('candidate', plan['head']),
                    ('operation', '1' * 64), ('repository', 'elsewhere'),
                    ('target_ref', 'refs/heads/other'), ('policy_sha', '2' * 64),
                    ('schema', 'merge-receipt/v3'), ('result', 'unrecognized'),
                    ('requested_at', 'not-a-time'), ('exit_code', True),
                    ('actual_target', '0' * 40), ('observed_at', '2000-01-01T00:00:00Z'),
                    ('diagnostics', {}), ('extra', True)]
        for field, value in variants:
            with self.subTest(field=field):
                state = copy.deepcopy(original); changed = copy.deepcopy(receipt); changed[field] = value
                state['receipts'][plan['id']] = changed; self.store(state)
                self.assert_rejected(('query', 'execute'))
        for changed in [{}, None, []]:
            with self.subTest(receipt=changed):
                state = copy.deepcopy(original); state['receipts'][plan['id']] = changed; self.store(state)
                self.assert_rejected(('query', 'execute'))
        for field in receipt:
            if field in {'diagnostics', 'exit_code', 'actual_target', 'observed_at'}:
                continue
            with self.subTest(missing=field):
                state = copy.deepcopy(original); changed = copy.deepcopy(receipt); del changed[field]
                state['receipts'][plan['id']] = changed; self.store(state)
                self.assert_rejected(('query', 'execute'))

    def test_git_replacement_objects_cannot_hide_candidate_parents(self):
        plan = self.merge_ready()
        wrong = self.candidate(plan, parents=[])
        g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', 'refs/replace/' + wrong, plan['candidate']).returncode, 0)
        plan['candidate'] = wrong; plan['subject']['head'] = wrong
        self.rehash(plan); self.authorize_combination(plan)
        self.assert_rejected()

    def test_observation_error_after_ref_update_retains_uncertain_intent(self):
        plan = self.merge_ready(); real = merge.LocalGit.run; updated = []
        def lost_observation(g, *args, **kwargs):
            if updated and args[0] == 'symbolic-ref':
                raise subprocess.TimeoutExpired(args, 10)
            result = real(g, *args, **kwargs)
            if args[0] == 'update-ref': updated.append(True)
            return result
        with Session(self.host, self.pin).locked() as session, patch.object(merge.LocalGit, 'run', lost_observation):
            with self.assertRaises(subprocess.TimeoutExpired): merge.execute(session)
        receipt = self.state()['receipts'][plan['id']]
        self.assertEqual(receipt['result'], 'unknown')
        self.assertIn('TimeoutExpired', receipt['diagnostics'])
        self.assertEqual(self.run_cli('merge-query')['result'], 'confirmed')

    def test_known_failed_receipt_is_not_confirmed_by_external_target_move(self):
        plan = self.merge_ready(); self.pending_attempt(plan)
        state = self.state(); state['receipts'][plan['id']]['result'] = 'failed'; self.store(state)
        g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', g.ref, plan['candidate'], plan['base']).returncode, 0)
        before = (self.host / 'state.json').read_bytes()
        for command in ['merge-query', 'merge-execute']:
            self.assertNotEqual(self.run_cli(command)['result'], 'confirmed')
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def same_candidate_cas_race(self, *, lose_observation=False):
        plan = self.merge_ready(); real = merge.LocalGit.run; completed = []
        def competing_writer(g, *args, **kwargs):
            if args[0] == 'update-ref':
                other = real(g, *args, **kwargs)
                self.assertEqual(other.returncode, 0, other.stderr)
                ours = real(g, *args, **kwargs)
                self.assertNotEqual(ours.returncode, 0)
                completed.append(ours.returncode)
                return ours
            if completed and lose_observation and args[0] == 'symbolic-ref':
                raise subprocess.TimeoutExpired(args, 10)
            return real(g, *args, **kwargs)
        with Session(self.host, self.pin).locked() as session, patch.object(merge.LocalGit, 'run', competing_writer):
            if lose_observation:
                with self.assertRaises(subprocess.TimeoutExpired): merge.execute(session)
            else:
                self.assertNotEqual(merge.execute(session)['result'], 'confirmed')
        receipt = self.state()['receipts'][plan['id']]
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['exit_code'], completed[0])
        self.assertEqual(merge.LocalGit(self.policy['merge']).target(), plan['candidate'])
        before = (self.host / 'state.json').read_bytes()
        for command in ['merge-query', 'merge-execute']:
            self.assertEqual(self.run_cli(command)['result'], 'unknown')
        self.assertEqual((self.host / 'state.json').read_bytes(), before)

    def test_failed_cas_same_candidate_never_confirms(self):
        self.same_candidate_cas_race()

    def test_failed_cas_with_lost_observation_stays_failed(self):
        self.same_candidate_cas_race(lose_observation=True)

    def test_uncertain_receipt_with_known_nonzero_exit_is_rejected(self):
        plan = self.merge_ready(); self.pending_attempt(plan)
        original = self.state(); g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', g.ref, plan['candidate'], plan['base']).returncode, 0)
        for result in ['pending', 'unknown']:
            with self.subTest(result=result):
                state = copy.deepcopy(original)
                state['receipts'][plan['id']].update(result=result, exit_code=128)
                self.store(state)
                self.assert_rejected(('query', 'execute'))

    def test_historical_query_survives_subject_authority_budget_changes(self):
        plan = self.merge_ready(); self.assertEqual(self.run_cli('merge-execute')['result'], 'confirmed')
        state = self.state(); state['subject'] = dict(self.subject, head='later-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']; self.store(state)
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        before = (self.host / 'state.json').read_bytes()
        self.assertEqual(self.run_cli('merge-query')['result'], 'confirmed')
        self.assertEqual(self.run_cli('merge-execute')['result'], 'confirmed')
        self.assertEqual((self.host / 'state.json').read_bytes(), before)
        g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', g.ref, plan['head'], plan['candidate']).returncode, 0)
        self.assertEqual(self.run_cli('merge-query')['result'], 'unknown')

    def test_missing_policy_target_and_symbolic_target_are_structured_holds(self):
        plan = self.merge_ready(); g = merge.LocalGit(self.policy['merge'])
        self.assertEqual(g.run('update-ref', '-d', g.ref).returncode, 0)
        for command in ['merge-preflight', 'merge-query', 'merge-execute']:
            self.assertEqual(self.run_cli(command, code=2)['status'], 'UNKNOWN')
        self.assertEqual(g.run('update-ref', 'refs/heads/other', plan['base']).returncode, 0)
        self.assertEqual(g.run('symbolic-ref', g.ref, 'refs/heads/other').returncode, 0)
        for command in ['merge-preflight', 'merge-query', 'merge-execute']:
            self.assertIn('symbolic target', self.run_cli(command, code=2)['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
