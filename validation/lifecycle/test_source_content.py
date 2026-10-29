"""Scoped source binding and exact historical recovery in self-owned fixtures.

No build, scanner, remote provider or real human decision is authenticated here.
New source capture has platform-specific mode models; historical validation stays pure.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import core, delivery, merge, review, source_content
from support import source_manifests as manifests
from support.git_runtime import git_environment
from validation.lifecycle.fixture import create, evidence, grant, write
from validation.test_support import temporary_directory


class LegacySourceCompatibility(unittest.TestCase):
    def test_source_capture_before_initialize_is_readonly_or_explicitly_unsupported(self):
        with temporary_directory() as temp:
            info = create(Path(temp) / 'fixture')
            host = Path(info['host'])
            source = Path(temp) / 'source'; source.mkdir()
            (source / 'source.txt').write_bytes(b'actual scoped bytes\r\n')
            policy = json.loads((host / 'policy.json').read_text(encoding='utf-8'))
            config = dict(mode='directory', scope=['.'], exclusions=[], max_files=8,
                          max_file_bytes=1024, max_total_bytes=4096, isolation='host-controlled')
            policy['source_content'] = {'schema': 'source-content-policy/v1', 'root': str(source),
                                         'config': config, 'required': True}
            write(host, 'policy.json', policy)
            pin = hashlib.sha256((host / 'policy.json').read_bytes()).hexdigest()
            handoff = json.loads((host / 'handoff.json').read_text(encoding='utf-8'))
            write(host, 'capture.json', {'schema': 'source-capture/v1', 'subject': handoff['subject']})
            before = {name: (host / name).read_bytes() for name in
                      ('policy.json', 'authority.json', 'handoff.json', 'sim-provider.json', 'capture.json')}
            result = subprocess.run([sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
                str(ROOT / 'interfaces/lifecycle/cli.py'), '--host-root', str(host),
                '--policy-sha', pin, '--record', 'capture.json', 'source-capture'],
                capture_output=True, text=True, timeout=15)
            output = json.loads(result.stdout)
            self.assertFalse(output['authorizes_action'])
            self.assertEqual({name: (host / name).read_bytes() for name in before}, before)
            self.assertFalse((host / 'state.json').exists())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output['current_source_status'], 'PASS')
            self.assertEqual(output['build_provenance'], 'UNKNOWN')
            for field in core.SUBJECT - {'worktree_digest'}:
                self.assertEqual(output['subject'][field], handoff['subject'][field])
            self.assertNotEqual(output['subject']['worktree_digest'], handoff['subject']['worktree_digest'])
            with patch.object(manifests, 'capture', side_effect=AssertionError('pure validation read source')):
                source_content.validate(output['source_content'], output['subject'])

    def test_legacy_initialize_and_review_do_not_collect_source(self):
        with temporary_directory() as temp:
            info = create(Path(temp) / 'fixture')
            host = Path(info['host'])
            handoff = json.loads((host / 'handoff.json').read_text(encoding='utf-8'))
            with core.Session(host, info['policy_sha']).locked() as s, \
                    patch.object(manifests, 'capture', side_effect=AssertionError('legacy source IO')):
                s.initialize(handoff)
                self.assertNotIn('source_content', s.state)
                self.assertEqual(set(s.state['subject']), core.SUBJECT)
                self.assertEqual(review.evaluate(s)['recommendation'], 'HOLD')


class SourceContent(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        info = create(Path(self.temp.name) / 'fixture')
        self.host, self.inputs = Path(info['host']), Path(info['inputs'])
        self.policy = self.read('policy.json')
        self.subject = self.read('handoff.json')['subject']
        self.pin = info['policy_sha']
        self.source = Path(self.temp.name) / 'source'
        self.source.mkdir()
        self.payload = self.source / 'note.txt'
        self.payload.write_bytes(b'self-owned candidate\n')
        self.config = dict(mode='directory', scope=['.'], exclusions=[], max_files=32,
                           max_file_bytes=1024, max_total_bytes=8192, isolation='host-controlled')

    def read(self, name='state.json'):
        return json.loads((self.host / name).read_text(encoding='utf-8'))

    def session(self):
        return core.Session(self.host, self.pin)

    def source_git(self, *args):
        result = subprocess.run([shutil.which('git'), '--git-dir', str(self.source / '.git'),
                                 '--work-tree', str(self.source), '-c', 'core.hooksPath=' + os.devnull,
                                 '-c', 'core.attributesFile=' + os.devnull, *args],
                                capture_output=True, env=git_environment(), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def git_source(self):
        shutil.copytree(Path(self.policy['merge']['repository']), self.source / '.git')
        config = self.source / '.git' / 'config'
        config.write_text(config.read_text(encoding='utf-8').replace('bare = true', 'bare = false'), encoding='utf-8')
        (self.source / '.git' / 'HEAD').write_text(self.subject['head'] + '\n', encoding='ascii')
        self.source_git('read-tree', '--reset', '-u', self.subject['head'])
        if os.name == 'nt': self.source_git('config', 'core.filemode', 'false')
        self.config['mode'] = 'git-worktree'

    def head_message_lifecycle(self, message):
        # A real valid Git commit can have non-UTF-8 message bytes. Hash the
        # complete raw commit object so Git does not recode the test payload.
        git = merge.LocalGit(self.policy['merge'])
        tree = git.run('rev-parse', '--verify', self.subject['head'] + '^{tree}').stdout.strip()
        headers = ('tree ' + tree + '\nparent ' + self.subject['base'] + '\n'
                   'author Local fixture <fixture@invalid> 1700000000 +0000\n'
                   'committer Local fixture <fixture@invalid> 1700000000 +0000\n\n').encode('ascii')
        result = subprocess.run([git.git, '--git-dir', str(git.root), '-c', 'core.hooksPath=' + os.devnull,
                                 'hash-object', '-t', 'commit', '-w', '--stdin'],
                                input=headers + message, capture_output=True, env=git_environment(), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        head = result.stdout.decode('ascii').strip()
        self.assertEqual(git.oid(head), head)
        self.subject = dict(self.subject, head=head)
        self.start(git=True)
        self.assertEqual(self.source_git('cat-file', 'commit', head), headers + message)
        proposal = self.prepare()
        try:
            with self.session().locked() as s:
                self.assertEqual(merge.validate_plan(s)[0], proposal)
                self.assertEqual(merge.preflight(s)['recommendation'], 'READY_FOR_NEXT')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
        except UnicodeDecodeError as exc:
            self.fail('valid source commit message bytes must not be decoded for a tree check: ' + str(exc))
        shutil.rmtree(self.source)
        state = self.read()
        state['subject'] = dict(state['subject'], head='later-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']
        state['receipts'][proposal['id']]['result'] = 'unknown'
        write(self.host, 'state.json', state)
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        def recover():
            with self.session().locked() as s, patch.object(manifests, 'capture', side_effect=AssertionError('message recovery source IO')):
                self.assertEqual(merge.validate_plan(s)[0], proposal)
                self.assertEqual(merge.query(s)['result'], 'confirmed')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
        self.no_action(recover, rejects=False)

    def test_non_utf8_head_message_prepares_validates_and_recovers(self):
        self.head_message_lifecycle(b'valid raw commit message \xff\n')

    def test_utf8_head_message_prepares_validates_and_recovers(self):
        self.head_message_lifecycle('valid UTF-8 commit message café\n'.encode('utf-8'))

    def proof(self):
        arguments = {'base': self.subject['base'], 'head': self.subject['head']} \
            if self.config['mode'] == 'git-worktree' else {}
        manifest = manifests.capture(self.source, self.config, **arguments)
        subject = dict(self.subject, worktree_digest=manifests.identity(manifest))
        return subject, {'schema': 'source-content/v1', 'manifest': manifest,
                         'source_subject_digest': core.digest(subject)}

    def start(self, git=False, required=True, supplied=True):
        if git:
            self.git_source()
        self.policy['source_content'] = {'schema': 'source-content-policy/v1', 'root': str(self.source),
                                         'config': copy.deepcopy(self.config), 'required': required}
        self.subject, content = self.proof()
        handoff = self.read('handoff.json')
        handoff['subject'] = self.subject
        if supplied:
            handoff['source_content'] = content
        write(self.host, 'policy.json', self.policy)
        self.pin = hashlib.sha256((self.host / 'policy.json').read_bytes()).hexdigest()
        with self.session().locked() as s:
            s.initialize(handoff)
        return content

    def ready(self):
        grant(self.host, self.pin, 'pr-approve', core.digest(self.subject))
        with self.session().locked() as s:
            source_content.current(s)
            s.charge(rounds=1, operation='begin-review')
            s.state['round_subject'] = core.digest(self.subject)
            s.persist()
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, self.subject, check)
                             for check in self.policy['checks'] if check['stage'] == 'pr']})
            self.assertEqual(review.evaluate(s)['recommendation'], 'READY_FOR_NEXT')

    def prepare(self, artifact=False):
        self.ready()
        grant(self.host, self.pin, 'merge-prepare', core.digest({
            'repository': self.policy['merge']['repository'], 'base': self.subject['base'],
            'head': self.subject['head'], 'policy_sha': self.pin}))
        with self.session().locked() as s:
            request = {'schema': 'merge-plan/v1', 'base': self.subject['base'],
                       'head': self.subject['head'], 'integration_artifact': 'source-only-integration-label'}
            if artifact:
                data = b'actual independent artifact\n'
                request['integration_artifact'] = 'sha256:' + hashlib.sha256(data).hexdigest()
                request['artifact_content'] = {'path': 'output.bin', 'algorithm': 'sha256',
                    'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
                    'build_config_digest': hashlib.sha256(b'host build label').hexdigest()}
            proposal = merge.prepare(s, request)['plan']
            check = next(check for check in self.policy['checks'] if check['stage'] == 'merge')
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, proposal['subject'], check)]})
        grant(self.host, self.pin, 'merge-approve', core.digest(proposal['subject']))
        grant(self.host, self.pin, 'merge-execute', proposal['id'])
        return proposal

    def snapshot(self):
        return {'files': {name: (self.host / name).read_bytes() for name in
                         ('state.json', 'authority.json', 'sim-provider.json')},
                'target': merge.LocalGit(self.policy['merge']).target()}

    def no_action(self, callback, rejects=True):
        before = self.snapshot()
        actual = merge.LocalGit.run
        calls = []
        def git(provider, *args, **kwargs):
            if args[0] == 'update-ref':
                calls.append(args)
            return actual(provider, *args, **kwargs)
        with patch.object(merge.LocalGit, 'run', git), \
                patch.object(delivery.LocalSimulation, 'apply', side_effect=AssertionError('unexpected apply')):
            if rejects:
                with self.assertRaises((ValueError, OSError)):
                    callback()
            else:
                try:
                    callback()
                except (ValueError, OSError) as exc:
                    self.fail('valid exact recovery unexpectedly required fresh eligibility: ' + str(exc))
        self.assertEqual(calls, [])
        self.assertEqual(self.snapshot(), before, 'source rejection/recovery must not charge, save or execute')

    def execute(self):
        with self.session().locked() as s:
            return merge.execute(s)

    def test_current_source_change_same_head_and_old_identity_is_unknown(self):
        content = self.start()
        self.ready()
        before = self.read()
        self.payload.write_bytes(b'same HEAD, different actual source\n')
        with self.session().locked() as s:
            result = review.evaluate(s)
            self.assertEqual(result['recommendation'], 'HOLD')
            self.assertEqual(next(c for c in result['checks'] if c['id'] == 'source-content')['status'], 'UNKNOWN')
            self.assertEqual(s.state['subject'], before['subject'])
            self.assertEqual(s.state['source_content'], content)
        self.assertEqual(self.read(), before)

    def test_missing_required_and_supplied_without_policy_reject_before_initialization(self):
        self.policy['source_content'] = {'schema': 'source-content-policy/v1', 'root': str(self.source),
                                         'config': self.config, 'required': True}
        write(self.host, 'policy.json', self.policy)
        self.pin = hashlib.sha256((self.host / 'policy.json').read_bytes()).hexdigest()
        with self.session().locked() as s, patch.object(manifests, 'capture', side_effect=AssertionError('missing proof read')):
            with self.assertRaisesRegex(ValueError, 'required source'):
                s.initialize(self.read('handoff.json'))
        self.assertFalse((self.host / 'state.json').exists())
        self.policy.pop('source_content')
        write(self.host, 'policy.json', self.policy)
        self.pin = hashlib.sha256((self.host / 'policy.json').read_bytes()).hexdigest()
        subject, content = self.proof()
        handoff = dict(self.read('handoff.json'), subject=subject, source_content=content)
        with self.session().locked() as s:
            with self.assertRaisesRegex(ValueError, 'source content policy missing'):
                s.initialize(handoff)

    def test_optional_missing_stays_legacy_and_invalid_supplied_is_not_ignored(self):
        self.start(required=False, supplied=False)
        self.assertNotIn('source_content', self.read())
        with self.session().locked() as s:
            self.assertIsNone(source_content.current(s))
            subject, content = self.proof()
            content['source_subject_digest'] = 'f' * 64
            with patch.object(manifests, 'capture', side_effect=AssertionError('bad binding read')):
                with self.assertRaises(ValueError):
                    source_content.fresh(s, content, subject)

    def test_pure_source_association_and_identity_are_required(self):
        content = self.start()
        with patch.object(manifests, 'capture', side_effect=AssertionError('pure source association IO')):
            self.assertEqual(source_content.validate(content, self.subject), self.subject['worktree_digest'])
            changed = copy.deepcopy(content)
            changed['source_subject_digest'] = 'f' * 64
            with self.assertRaisesRegex(ValueError, 'subject binding mismatch'):
                source_content.validate(changed, self.subject)
            subject = dict(self.subject, worktree_digest='old-opaque-source-label')
            changed['source_subject_digest'] = core.digest(subject)
            with self.assertRaisesRegex(ValueError, 'subject binding mismatch'):
                source_content.validate(changed, subject)

    def test_malformed_optional_policy_is_unknown_without_side_effect(self):
        self.start()
        self.ready()
        before = self.snapshot()
        with self.session().locked() as s, patch.object(manifests, 'capture', side_effect=AssertionError('bad policy IO')):
            good = copy.deepcopy(s.policy['source_content'])
            for invalid in (None, [], dict(good, required=None), dict(good, root='relative'),
                            dict(good, config=None), dict(good, config=dict(self.config, mode=[]))):
                with self.subTest(policy=invalid):
                    s.policy['source_content'] = invalid
                    result = review.evaluate(s)
                    self.assertEqual(result['recommendation'], 'HOLD')
                    self.assertEqual(next(c for c in result['checks'] if c['id'] == 'source-content')['status'], 'UNKNOWN')
        self.assertEqual(self.snapshot(), before)

    def test_begin_review_stale_source_refuses_before_charge(self):
        self.start()
        self.payload.write_bytes(b'changed before registration\n')
        before = self.snapshot()
        result = subprocess.run([sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
            str(ROOT / 'interfaces/lifecycle/cli.py'), '--host-root', str(self.host),
            '--policy-sha', self.pin, 'begin-review'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'UNKNOWN')
        self.assertEqual(self.snapshot(), before)

    def test_advance_rebinds_source_invalidates_round_and_keeps_consumed_budget(self):
        self.start()
        self.ready()
        before = self.read()
        self.payload.write_bytes(b'next source revision\n')
        new_subject, content = self.proof()
        self.assertNotEqual(new_subject['worktree_digest'], self.subject['worktree_digest'])
        with self.session().locked() as s:
            result = review.advance(s, {'schema': 'subject-change/v1', 'subject': new_subject,
                'impact': 'actual source bytes changed', 'reused_evidence': [], 'source_content': content})
            self.assertEqual(result['recommendation'], 'HOLD')
            self.assertIsNone(s.state['round_subject'])
            self.assertEqual(s.state['source_content'], content)
            self.assertEqual(s.state['budget']['operations'], before['budget']['operations'] + 1)
            self.assertEqual(s.state['budget']['rounds'], before['budget']['rounds'])
            self.assertEqual(s.state['evidence'], before['evidence'])

    def test_directory_source_does_not_authorize_committed_merge(self):
        self.start()
        self.ready()
        grant(self.host, self.pin, 'merge-prepare', core.digest({
            'repository': self.policy['merge']['repository'], 'base': self.subject['base'],
            'head': self.subject['head'], 'policy_sha': self.pin}))
        def directory():
            with self.session().locked() as s:
                merge.prepare(s, {'schema': 'merge-plan/v1', 'base': self.subject['base'],
                    'head': self.subject['head'], 'integration_artifact': 'label'})
        self.no_action(directory)

    def test_dirty_git_source_does_not_authorize_committed_merge(self):
        # Independently captured dirty input is valid scoped review evidence,
        # but it cannot pretend that Git's committed candidate contains it.
        self.git_source()
        self.payload.write_bytes(b'actual uncommitted source\n')
        self.start()
        self.ready()
        grant(self.host, self.pin, 'merge-prepare', core.digest({
            'repository': self.policy['merge']['repository'], 'base': self.subject['base'],
            'head': self.subject['head'], 'policy_sha': self.pin}))
        def dirty():
            with self.session().locked() as s:
                merge.prepare(s, {'schema': 'merge-plan/v1', 'base': self.subject['base'],
                    'head': self.subject['head'], 'integration_artifact': 'label'})
        self.no_action(dirty)

    def test_source_proposal_versions_and_actual_committed_tree_are_strict(self):
        self.start(git=True)
        original = self.prepare()
        with self.session().locked() as s, patch.object(manifests, 'capture', side_effect=AssertionError('stored validation IO')):
            for schema in (None, 'merge-proposal/v2', 'merge-proposal/v4'):
                changed = copy.deepcopy(original)
                if schema is None:
                    changed.pop('schema')
                else:
                    changed['schema'] = schema
                changed['id'] = core.digest({k:v for k,v in changed.items() if k != 'id'})
                with self.subTest(schema=schema), self.assertRaises(ValueError):
                    merge.validate_plan(s, changed)
            changed = copy.deepcopy(original)
            content = changed['source_content']
            content['manifest']['head_tree'] = '2' * 40
            source = dict(changed['source_subject'], worktree_digest=manifests.identity(content['manifest']))
            content['source_subject_digest'] = core.digest(source)
            changed['source_subject'] = source
            changed['id'] = core.digest({k:v for k,v in changed.items() if k != 'id'})
            with self.assertRaisesRegex(ValueError, 'committed tree mismatch'):
                merge.validate_plan(s, changed)

            # Coherent valid-shape proof changes must be rejected by the old
            # full plan ID, independently of source association or fresh gates.
            changed = copy.deepcopy(original)
            content = changed['source_content']
            content['manifest']['config']['max_files'] += 1
            source = dict(changed['source_subject'], worktree_digest=manifests.identity(content['manifest']))
            content['source_subject_digest'] = core.digest(source)
            changed['source_subject'] = source
            with self.assertRaisesRegex(ValueError, 'plan digest mismatch'):
                merge.validate_plan(s, changed)

    def test_source_and_single_file_artifact_remain_independent(self):
        files = self.host / 'artifact-files'; files.mkdir()
        (files / 'output.bin').write_bytes(b'actual independent artifact\n')
        self.policy['artifact_content'] = {'schema': 'artifact-content-policy/v1',
            'required_actions': ['merge-execute'], 'max_bytes': 1024, 'isolation': 'host-controlled'}
        self.start(git=True)
        proposal = self.prepare(artifact=True)
        self.assertEqual(proposal['schema'], 'merge-proposal/v3')
        self.assertIn('source_content', proposal)
        self.assertIn('artifact_content', proposal)
        self.assertNotEqual(proposal['source_subject']['worktree_digest'], proposal['subject']['artifact_digest'])
        self.assertEqual(self.execute()['result'], 'confirmed')

    def test_clean_git_merge_full_plan_binding_and_source_change_before_cas(self):
        content = self.start(git=True)
        proposal = self.prepare()
        self.assertEqual(proposal['schema'], 'merge-proposal/v3')
        self.assertEqual(proposal['source_content'], content)
        self.assertEqual(set(proposal['subject']), core.SUBJECT)
        original = self.read()
        tampered = copy.deepcopy(original)
        tampered['merge']['source_content']['source_subject_digest'] = 'f' * 64
        write(self.host, 'state.json', tampered)
        self.no_action(self.execute)
        write(self.host, 'state.json', original)
        self.payload.write_bytes(b'dirty after exact approval\n')
        self.no_action(self.execute)
        self.payload.write_bytes(b'self-owned candidate\n')
        self.assertEqual(self.execute()['result'], 'confirmed')

    def test_historical_validation_and_repeat_need_no_live_source_or_new_charge(self):
        self.start(git=True)
        proposal = self.prepare()
        self.assertEqual(self.execute()['result'], 'confirmed')
        # Existing exact delivery history must also bypass fresh source guards.
        with self.session().locked() as s:
            deployed = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                'parameters': {'artifact': proposal['subject']['artifact_digest']}})
        grant(self.host, self.pin, 'delivery-deploy', deployed['id'])
        with self.session().locked() as s:
            self.assertEqual(delivery.execute(s, deployed['id'])['result']['result'], 'confirmed')
        shutil.rmtree(self.source)
        state = self.read()
        state['subject'] = dict(state['subject'], head='later-source-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']
        state['receipts'][proposal['id']]['result'] = 'unknown'
        request = state['delivery']['requests'][deployed['id']]
        request['result'] = 'pending'
        request.pop('receipt_sha'); request.pop('observed_at')
        write(self.host, 'state.json', state)
        write(self.host, 'authority.json', {'schema': 'host-authority/v1', 'decisions': []})
        def recover():
            with self.session().locked() as s, patch.object(manifests, 'capture', side_effect=AssertionError('historical source IO')):
                # New policy semantics remain irrelevant to stored exact recovery.
                s.policy['source_content']['config']['mode'] = 'unavailable-now'
                self.assertEqual(merge.query(s)['result'], 'confirmed')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
                self.assertEqual(delivery.LocalSimulation(s).query(deployed['id'])['result']['result'], 'confirmed')
                self.assertEqual(delivery.execute(s, deployed['id'])['result']['result'], 'confirmed')
                self.assertEqual(merge.validate_plan(s)[0]['source_content'], proposal['source_content'])
        self.no_action(recover, rejects=False)

    def test_pending_before_effect_never_replays_after_source_removal(self):
        self.start(git=True)
        proposal = self.prepare()
        self.assertEqual(self.execute()['result'], 'confirmed')
        git = merge.LocalGit(self.policy['merge'])
        self.assertEqual(git.run('update-ref', git.ref, proposal['base'], proposal['candidate']).returncode, 0)
        state = self.read(); state['receipts'][proposal['id']]['result'] = 'pending'
        write(self.host, 'state.json', state)
        shutil.rmtree(self.source)
        def recover():
            with self.session().locked() as s, patch.object(manifests, 'capture', side_effect=AssertionError('pending source IO')):
                self.assertEqual(merge.query(s)['result'], 'not_applied')
                self.assertEqual(merge.execute(s)['result'], 'not_applied')
        self.no_action(recover, rejects=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
