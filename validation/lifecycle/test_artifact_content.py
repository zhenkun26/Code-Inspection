"""Actual single-file bytes, immutable snapshots, and historical compatibility.

All Git repositories, policy/authority ledgers and artifacts are self-owned local
fixtures. Delivery remains a simulation; source association is host-declared and
does not authenticate a build. No network or production provider is involved.
"""
import copy
from dataclasses import FrozenInstanceError
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
from support import local_files
from validation.lifecycle.fixture import create, evidence, grant, write
from validation.test_support import temporary_directory

DATA = b'approved artifact A\r\n'
CHANGED = b'approved artifact B\r\n'
BUILD_CONFIG = hashlib.sha256(b'self-owned build configuration').hexdigest()
TREE = '1' * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ArtifactContent(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        info = create(Path(self.temp.name) / 'fixture')
        self.host, self.inputs = Path(info['host']), Path(info['inputs'])
        self.policy = self.read('policy.json')
        self.policy.pop('artifact_content', None)
        self.subject = self.read('handoff.json')['subject']
        self.files = self.host / 'artifact-files'
        self.files.mkdir()
        self.artifact = self.files / 'artifact.bin'
        self.artifact.write_bytes(DATA)
        self.pin = info['policy_sha']
        self.initialized = False
        self.review_started = False

    def read(self, name='state.json'):
        return json.loads((self.host / name).read_text(encoding='utf-8'))

    def start(self, required=None, max_bytes=artifacts.MAX_BYTES, isolation='host-controlled', legacy=False):
        if not legacy:
            self.policy['artifact_content'] = {
                'schema': 'artifact-content-policy/v1',
                'required_actions': list(artifacts.ACTIONS) if required is None else required,
                'max_bytes': max_bytes, 'isolation': isolation}
        self.policy['delivery']['rollback_compatibility']['sha256:' + sha(DATA)] = 'schema-1'
        write(self.host, 'policy.json', self.policy)
        self.pin = sha((self.host / 'policy.json').read_bytes())
        with self.session().locked() as s:
            s.initialize(self.read('handoff.json'))
        self.initialized = True

    def session(self):
        return core.Session(self.host, self.pin)

    def descriptor(self, data=DATA, path='artifact.bin'):
        return {'path': path, 'algorithm': 'sha256', 'sha256': sha(data),
                'size_bytes': len(data), 'build_config_digest': BUILD_CONFIG}

    def capture(self, s, descriptor=None):
        return artifacts.capture(s, descriptor or self.descriptor(), self.subject, TREE)

    def ready_pr(self):
        if self.review_started:
            return
        grant(self.host, self.pin, 'pr-approve', core.digest(self.subject))
        with self.session().locked() as s:
            s.charge(rounds=1, operation='begin-review')
            s.state['round_subject'] = core.digest(self.subject)
            s.persist()
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, self.subject, c) for c in self.policy['checks']
                             if c['stage'] == 'pr']})
            self.assertEqual(review.evaluate(s)['recommendation'], 'READY_FOR_NEXT')
        self.review_started = True

    def prepare_merge(self, content=True, descriptor=None, approve_execute=True):
        self.ready_pr()
        binding = core.digest({'repository': self.policy['merge']['repository'],
            'base': self.subject['base'], 'head': self.subject['head'], 'policy_sha': self.pin})
        grant(self.host, self.pin, 'merge-prepare', binding)
        request = {'schema': 'merge-plan/v1', 'base': self.subject['base'], 'head': self.subject['head'],
                   'integration_artifact': 'legacy-combination-artifact'}
        if content:
            descriptor = descriptor or self.descriptor()
            request.update(artifact_content=descriptor, integration_artifact='sha256:' + descriptor['sha256'])
        with self.session().locked() as s:
            plan = merge.prepare(s, request)['plan']
            check = next(c for c in self.policy['checks'] if c['stage'] == 'merge')
            review.ingest(s, self.inputs, {'schema': 'review-batch/v1', 'findings': [],
                'evidence': [evidence(self.inputs, plan['subject'], check, 'integration-' + plan['id'])]})
        grant(self.host, self.pin, 'merge-approve', core.digest(plan['subject']))
        if approve_execute:
            grant(self.host, self.pin, 'merge-execute', plan['id'])
        return plan

    def merged(self, content=True):
        plan = self.prepare_merge(content=content)
        with self.session().locked() as s:
            self.assertEqual(merge.execute(s)['result'], 'confirmed')
        return plan

    def rollback(self, source, descriptor=None):
        request = {'schema': 'delivery-plan/v1', 'action': 'rollback',
                   'parameters': {'artifact': 'sha256:' + sha(DATA)},
                   'artifact_content': descriptor or self.descriptor(), 'artifact_source_merge': source['id']}
        with self.session().locked() as s:
            proposal = delivery.plan(s, request)
        grant(self.host, self.pin, 'delivery-rollback', proposal['id'])
        return proposal

    def snapshot(self):
        state = self.read()
        return {'files': {name: (self.host / name).read_bytes() for name in
                         ('state.json', 'authority.json', 'sim-provider.json')},
                'budget': copy.deepcopy(state['budget']),
                'target': merge.LocalGit(self.policy['merge']).target()}

    def assert_no_action(self, function, expected_exception=True):
        before = self.snapshot()
        real = merge.LocalGit.run
        real_apply = delivery.LocalSimulation.apply
        calls = []
        def run(g, *args, **kwargs):
            if args[0] == 'update-ref':
                calls.append(args)
            return real(g, *args, **kwargs)
        def apply(provider, *args, **kwargs):
            calls.append(('apply',))
            return real_apply(provider, *args, **kwargs)
        with patch.object(merge.LocalGit, 'run', run), patch.object(delivery.LocalSimulation, 'apply', apply):
            if expected_exception:
                with self.assertRaises((ValueError, OSError)):
                    function()
            else:
                function()
            self.assertEqual(calls, [], 'a rejected/repeated action must not perform Git CAS or apply')
        self.assertEqual(self.snapshot(), before, 'no charge, receipt, provider, approval or ref mutation')

    def execute_merge(self):
        with self.session().locked() as s:
            return merge.execute(s)

    def execute_delivery(self, operation):
        with self.session().locked() as s:
            return delivery.execute(s, operation)

    def test_capture_is_one_bounded_immutable_snapshot_with_declared_association(self):
        self.start()
        with self.session().locked() as s, patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader:
            container, snapshot = self.capture(s)
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(reader.call_args.kwargs, {'limit': artifacts.MAX_BYTES, 'single_link': True})
            self.assertEqual((snapshot.data, snapshot.sha256, snapshot.size_bytes), (DATA, sha(DATA), len(DATA)))
            self.assertIs(type(snapshot.data), bytes)
            with self.assertRaises(FrozenInstanceError):
                snapshot.data = CHANGED
            self.assertEqual(container, {'locator': 'artifact.bin', 'proof': {
                'schema': 'artifact-content/v1', 'algorithm': 'sha256', 'sha256': sha(DATA),
                'size_bytes': len(DATA), 'source_subject_digest': core.digest(self.subject),
                'candidate_tree': TREE, 'ci_config_digest': self.subject['ci_config_digest'],
                'build_config_digest': BUILD_CONFIG, 'provenance': 'host-declared-association',
                'scope': 'single-file-snapshot'}})
            self.assertEqual(artifacts.identity(container), 'sha256:' + sha(DATA))
            artifacts.check_snapshot(snapshot, container)

    def test_missing_optional_and_source_only_are_not_product_failures(self):
        self.start(required=[])
        with self.session().locked() as s:
            self.assertIsNone(artifacts.fresh(s, 'merge-execute', None))
            self.assertFalse(artifacts.required(s, 'merge-execute'))
            s.policy.pop('artifact_content')
            self.assertIsNone(artifacts.policy_config(s))
            self.assertIsNone(artifacts.fresh(s, 'delivery-rollback', None))
            s.policy['artifact_content'] = dict(schema='artifact-content-policy/v1',
                required_actions=['merge-execute'], max_bytes=32, isolation='host-controlled')
            with self.assertRaisesRegex(ValueError, 'UNKNOWN:.*proof missing'):
                artifacts.fresh(s, 'merge-execute', None)

    def test_policy_invalid_and_unconfirmed_isolation_refuse_before_payload_read(self):
        self.start()
        with self.session().locked() as s:
            container, _ = self.capture(s)
            good = copy.deepcopy(s.policy['artifact_content'])
            changes = [('schema', 'unknown'), ('required_actions', ['promote']),
                       ('required_actions', ['merge-execute', 'merge-execute']),
                       ('max_bytes', True), ('max_bytes', 0), ('max_bytes', artifacts.MAX_BYTES + 1),
                       ('isolation', 'authenticated-by-artifact')]
            for field, value in changes:
                with self.subTest(field=field, value=value):
                    s.policy['artifact_content'] = dict(good, **{field: value})
                    with patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader, self.assertRaises((ValueError, OSError)):
                        artifacts.fresh(s, 'merge-execute', container)
                    reader.assert_not_called()
            s.policy['artifact_content'] = dict(good, isolation='unconfirmed')
            with patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader, self.assertRaisesRegex(ValueError, 'isolation'):
                self.capture(s)
            reader.assert_not_called()

    def test_full_proof_source_tree_config_validation_is_pure_and_strict(self):
        self.start()
        with self.session().locked() as s:
            container, _ = self.capture(s)
            with patch.object(artifacts, 'read_bytes', side_effect=AssertionError('historical validation did IO')):
                artifacts.validate(container, self.subject, TREE, artifacts.identity(container))
                mutations = {'schema': 'unknown', 'algorithm': 'SHA256', 'sha256': 'f' * 64,
                    'size_bytes': True, 'source_subject_digest': 'f' * 64, 'candidate_tree': '2' * 40,
                    'ci_config_digest': 'other', 'build_config_digest': 'not-a-hash',
                    'provenance': 'authenticated-build', 'scope': 'directory', 'authenticated': True}
                for field, value in mutations.items():
                    with self.subTest(field=field):
                        changed = copy.deepcopy(container)
                        changed['proof'][field] = value
                        with self.assertRaises(ValueError):
                            artifacts.validate(changed, self.subject, TREE, artifacts.identity(container))
                for field in self.subject:
                    with self.subTest(source_field=field), self.assertRaises(ValueError):
                        artifacts.validate(container, dict(self.subject, **{field: 'changed'}), TREE, artifacts.identity(container))
                for field in container['proof']:
                    changed = copy.deepcopy(container)
                    del changed['proof'][field]
                    with self.subTest(missing=field), self.assertRaises(ValueError):
                        artifacts.validate(changed, self.subject, TREE, artifacts.identity(container))

    def test_descriptor_raw_paths_and_unknown_fields_are_rejected_before_read(self):
        self.start()
        paths = ['', '.', '../outside', '/absolute', 'nested/../artifact.bin', './artifact.bin',
                 'nested//artifact.bin', 'nested\\artifact.bin', 'C:/artifact.bin', 'CON.txt', 'artifact.bin ', 'a\x00b']
        with self.session().locked() as s:
            for path in paths:
                with self.subTest(path=path), patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader:
                    with self.assertRaises((ValueError, OSError)):
                        self.capture(s, self.descriptor(path=path))
                    reader.assert_not_called()
            for change in ({'size_bytes': True}, {'size_bytes': -1}, {'sha256': 'A' * 64},
                           {'build_config_digest': ''}, {'authenticated': True}):
                with self.subTest(change=change), patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader:
                    with self.assertRaises((ValueError, OSError)):
                        self.capture(s, dict(self.descriptor(), **change))
                    reader.assert_not_called()

    def test_artifact_reader_refuses_links_nonregular_files_and_intermediate_escape(self):
        self.start()
        with self.session().locked() as s:
            self.assertEqual(self.capture(s)[1].data, DATA)
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'artifact.bin').write_bytes(DATA)
        (self.files / 'leaf').symlink_to(outside / 'artifact.bin')
        (self.files / 'parent').symlink_to(outside, target_is_directory=True)
        (self.files / 'directory').mkdir()
        os.link(self.artifact, self.files / 'hardlink')
        paths = ['leaf', 'parent/artifact.bin', 'directory', 'hardlink']
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.files / 'fifo')
            paths.append('fifo')
        if os.name == 'nt':
            # Real junction traversal is mandatory on Windows, not simulated on POSIX.
            junction = self.files / 'junction'
            run = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                 capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
            paths.append('junction/artifact.bin')
        payload_module, payload_name = local_files.os, 'read'
        if os.name == 'nt':
            from support import windows_files
            payload_module, payload_name = windows_files, 'ReadFile'
        payload_read = getattr(payload_module, payload_name)
        with self.session().locked() as s:
            for path in paths:
                with self.subTest(path=path), patch.object(payload_module, payload_name, wraps=payload_read) as reader:
                    with self.assertRaises((ValueError, OSError)):
                        self.capture(s, self.descriptor(path=path))
                    reader.assert_not_called()
            preserved = self.host / 'owned-artifact-files'
            self.files.rename(preserved)
            try:
                self.files.symlink_to(outside, target_is_directory=True)
                with patch.object(payload_module, payload_name, wraps=payload_read) as reader:
                    with self.assertRaises((ValueError, OSError)):
                        self.capture(s)
                    reader.assert_not_called()
            finally:
                if self.files.is_symlink():
                    self.files.unlink()
                preserved.rename(self.files)

    def test_unknown_algorithm_and_actual_digest_mismatch_have_distinct_read_boundaries(self):
        self.start()
        with self.session().locked() as s:
            self.assertEqual(self.capture(s)[1].data, DATA)
            with patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader:
                with self.assertRaises(ValueError):
                    self.capture(s, dict(self.descriptor(), algorithm='sha512'))
                reader.assert_not_called()
            for claimed in (sha(b'other real bytes'), sha(('sha256:' + sha(DATA)).encode())):
                with self.subTest(claimed=claimed), patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader:
                    with self.assertRaisesRegex(ValueError, 'snapshot bytes mismatch'):
                        self.capture(s, dict(self.descriptor(), sha256=claimed))
                    self.assertEqual(reader.call_count, 1, 'valid-format claims must be compared to actual bytes')

    def test_exact_byte_limit_empty_file_and_growth_detection(self):
        self.start(max_bytes=32)
        with self.session().locked() as s:
            for data in (b'', b'x' * 32):
                self.artifact.write_bytes(data)
                with patch.object(artifacts, 'read_bytes', wraps=artifacts.read_bytes) as reader:
                    self.assertEqual(self.capture(s, self.descriptor(data))[1].data, data)
                    self.assertEqual(reader.call_args.kwargs['limit'], 32)
            self.artifact.write_bytes(b'x' * 33)
            with self.assertRaises(ValueError):
                self.capture(s, self.descriptor(b'x' * 33))
            # A small claimed size must not bypass actual metadata/payload bounds.
            with self.assertRaises((ValueError, OSError)):
                self.capture(s, self.descriptor(b'x' * 32))
            self.artifact.write_bytes(b'x' * 32)
            consumed = []
            if os.name != 'nt':
                original = local_files.os.read
                def grow(fd, count):
                    data = original(fd, count)
                    consumed.append(len(data))
                    if len(consumed) == 1:
                        with self.artifact.open('ab') as stream:
                            stream.write(b'z')
                    return data
                with patch.object(local_files.os, 'read', grow), self.assertRaises(ValueError):
                    self.capture(s, self.descriptor(b'x' * 32))
                self.assertLessEqual(sum(consumed), 33, 'at most one extra detection byte')
                self.artifact.write_bytes(b'x' * 32)
                initial = self.artifact.stat()
                rewritten = []
                def rewrite_same_bytes(fd, count):
                    data = original(fd, count)
                    if not rewritten:
                        self.artifact.write_bytes(b'x' * 32)
                        os.utime(self.artifact, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1000000))
                        rewritten.append(True)
                    return data
                # Hash and size still match. Only the real post-read metadata
                # guard detects this observed write; this is no atomicity claim.
                with patch.object(local_files.os, 'read', rewrite_same_bytes), self.assertRaisesRegex(ValueError, 'changed during snapshot'):
                    self.capture(s, self.descriptor(b'x' * 32))
                self.assertEqual(rewritten, [True])
            else:
                from support import windows_files
                original = windows_files.ReadFile
                def grow(*args):
                    result = original(*args)
                    consumed.append(True)
                    if len(consumed) == 1:
                        with self.artifact.open('ab') as stream:
                            stream.write(b'z')
                    return result
                with patch.object(windows_files, 'ReadFile', grow), self.assertRaises((ValueError, OSError)):
                    self.capture(s, self.descriptor(b'x' * 32))
                self.assertTrue(consumed, 'native Windows read/growth boundary was exercised')

    def test_snapshot_check_recomputes_actual_bytes_and_requires_bytes_type(self):
        self.start()
        with self.session().locked() as s:
            container, snapshot = self.capture(s)
            for bad in (None, artifacts.Snapshot(bytearray(DATA), sha(DATA), len(DATA)),
                        artifacts.Snapshot(CHANGED, sha(DATA), len(DATA)),
                        artifacts.Snapshot(DATA, 'f' * 64, len(DATA)),
                        artifacts.Snapshot(DATA, sha(DATA), True)):
                with self.subTest(snapshot=bad), self.assertRaises(ValueError):
                    artifacts.check_snapshot(bad, container)
            self.artifact.write_bytes(CHANGED)
            artifacts.check_snapshot(snapshot, container)
            with self.assertRaises(ValueError):
                artifacts.acquire(s, container)

    def test_required_merge_missing_descriptor_is_unknown_without_charge(self):
        self.start()
        self.ready_pr()
        def prepare_missing():
            with self.session().locked() as s:
                merge.prepare(s, {'schema': 'merge-plan/v1', 'base': self.subject['base'],
                    'head': self.subject['head'], 'integration_artifact': 'legacy-only-label'})
        self.assert_no_action(prepare_missing)

    def test_merge_byte_change_after_approval_holds_before_cas_and_charge(self):
        self.start()
        plan = self.prepare_merge()
        self.assertEqual(plan['schema'], 'merge-proposal/v2')
        self.assertEqual(plan['subject']['artifact_digest'], 'sha256:' + sha(DATA))
        self.artifact.write_bytes(CHANGED)
        with self.session().locked() as s:
            result = merge.preflight(s)
        self.assertEqual(result['recommendation'], 'HOLD')
        self.assertEqual(next(c for c in result['checks'] if c['id'] == 'artifact-content')['status'], 'UNKNOWN')
        self.assert_no_action(self.execute_merge)
        self.artifact.write_bytes(DATA)
        self.assertEqual(self.execute_merge()['result'], 'confirmed')

    def test_replanned_bytes_change_full_plan_identity_and_require_exact_new_approval(self):
        self.start()
        old = self.prepare_merge()
        self.artifact.write_bytes(CHANGED)
        new = self.prepare_merge(descriptor=self.descriptor(CHANGED), approve_execute=False)
        self.assertNotEqual(old['id'], new['id'])
        self.assertNotEqual(old['artifact_content'], new['artifact_content'])
        self.assert_no_action(self.execute_merge)
        grant(self.host, self.pin, 'merge-execute', new['id'])
        self.assertEqual(self.execute_merge()['result'], 'confirmed')

    def test_every_proof_field_is_part_of_merge_plan_identity(self):
        self.start()
        self.prepare_merge()
        original = self.read()
        for field in original['merge']['artifact_content']['proof']:
            with self.subTest(field=field):
                state = copy.deepcopy(original)
                value = state['merge']['artifact_content']['proof'][field]
                state['merge']['artifact_content']['proof'][field] = value + 1 if type(value) is int else str(value) + '-changed'
                write(self.host, 'state.json', state)
                self.assert_no_action(self.execute_merge)
        alternate = self.files / 'alternate.bin'
        alternate.write_bytes(DATA)
        variants = [('build_config_digest', 'f' * 64), ('locator', 'alternate.bin')]
        for field, value in variants:
            with self.subTest(valid_shape=field):
                state = copy.deepcopy(original)
                container = state['merge']['artifact_content']
                if field == 'locator':
                    container[field] = value
                else:
                    container['proof'][field] = value
                write(self.host, 'state.json', state)
                try:
                    # Every other validation/authority condition still passes.
                    # Only the unchanged canonical plan ID must reject this.
                    self.assert_no_action(self.execute_merge)
                finally:
                    # A guard-removal copy can perform its isolated CAS. Restore
                    # owned fixture state so the next oracle/control stays valid.
                    g = merge.LocalGit(self.policy['merge'])
                    if g.target() != original['merge']['base']:
                        self.assertEqual(g.run('update-ref', g.ref, original['merge']['base']).returncode, 0)
                    write(self.host, 'state.json', original)
        write(self.host, 'state.json', original)
        self.assertEqual(self.execute_merge()['result'], 'confirmed')

    def test_legacy_source_only_and_exact_attempts_remain_readable_without_upgrade(self):
        self.start(legacy=True)
        plan = self.merged(content=False)
        self.assertNotIn('artifact_content', plan)
        with self.session().locked() as s:
            rolled = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'rollback',
                                      'parameters': {'artifact': 'old-artifact'}})
        grant(self.host, self.pin, 'delivery-rollback', rolled['id'])
        self.assertEqual(self.execute_delivery(rolled['id'])['result']['result'], 'confirmed')
        self.artifact.unlink()
        grant(self.host, self.pin, 'merge-execute', plan['id'], decision='revoke')
        grant(self.host, self.pin, 'delivery-rollback', rolled['id'], decision='revoke')
        state = self.read()
        state['subject'] = dict(self.subject, head='later-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']
        write(self.host, 'state.json', state)
        def query_and_repeat():
            with self.session().locked() as s:
                queried = merge.query(s)
                self.assertEqual(queried['result'], 'confirmed')
                self.assertEqual(queried['content_binding']['record_kind'], 'legacy-label-only')
                self.assertEqual(queried['content_binding']['current_file_status'], 'NOT_RUN')
                self.assertEqual(queried['content_binding']['build_provenance'], 'UNKNOWN')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
                self.assertNotIn('artifact_content', s.state['merge'])
                queried = delivery.LocalSimulation(s).query(rolled['id'])
                self.assertEqual(queried['result']['result'], 'confirmed')
                self.assertEqual(queried['content_binding']['record_kind'], 'legacy-label-only')
                self.assertEqual(delivery.execute(s, rolled['id'])['result']['result'], 'confirmed')
                self.assertNotIn('artifact_content', s.state['delivery']['plans'][rolled['id']])
        self.assert_no_action(query_and_repeat, expected_exception=False)

    def test_required_rollback_missing_proof_is_structured_unknown_without_charge(self):
        self.start()
        write(self.host, 'action.json', {'schema': 'delivery-plan/v1', 'action': 'rollback',
                                       'parameters': {'artifact': 'old-artifact'}})
        before = self.snapshot()
        command = [sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
                   str(ROOT / 'interfaces/lifecycle/cli.py'), '--host-root', str(self.host),
                   '--policy-sha', self.pin, '--record', 'action.json', 'delivery-plan']
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output['status'], output['recommendation']), ('UNKNOWN', 'HOLD'))
        self.assertFalse(output['authorizes_action'])
        self.assertEqual(self.snapshot(), before)

    def test_rollback_uses_independent_historical_target_proof_despite_current_blocker(self):
        self.start()
        source = self.merged()
        state = self.read()
        state['merge'] = {}
        state['subject'] = dict(self.subject, head='later-current-subject')
        state['findings']['current-blocker'] = {'id': 'current-blocker', 'blocking': True, 'alias': None}
        write(self.host, 'state.json', state)
        proposal = self.rollback(source)
        self.assertEqual(proposal['schema'], 'delivery-proposal/v3')
        self.assertEqual(proposal['source'], {'merge_plan': source['id']})
        self.assertEqual(proposal['artifact_content']['proof']['source_subject_digest'], core.digest(source['source_subject']))
        self.artifact.write_bytes(CHANGED)
        self.assert_no_action(lambda: self.execute_delivery(proposal['id']))
        self.artifact.write_bytes(DATA)
        result = self.execute_delivery(proposal['id'])
        self.assertTrue(result['simulation'])
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertEqual(result['state']['artifact'], 'sha256:' + sha(DATA))
        self.assertEqual(result['result']['limitation'], delivery.LIMITATION)
        self.assertEqual(result['result']['consumed_content'], {'sha256': sha(DATA), 'size_bytes': len(DATA)})

    def test_deploy_inherits_proof_and_apply_consumes_acquired_snapshot_without_reopen(self):
        self.start()
        source = self.merged()
        with self.session().locked() as s:
            proposal = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                'parameters': {'artifact': source['subject']['artifact_digest']}})
        self.assertEqual(proposal['artifact_content'], source['artifact_content'])
        grant(self.host, self.pin, 'delivery-deploy', proposal['id'])
        real_apply = delivery.LocalSimulation.apply
        snapshots = []
        def replace_after_acquire(provider, planned, snapshot=None):
            self.assertIsNotNone(snapshot)
            snapshots.append(snapshot)
            self.artifact.write_bytes(CHANGED)
            with patch.object(artifacts, 'read_bytes', side_effect=AssertionError('apply reopened a mutable path')):
                return real_apply(provider, planned, snapshot=snapshot)
        with patch.object(delivery.LocalSimulation, 'apply', replace_after_acquire):
            result = self.execute_delivery(proposal['id'])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].data, DATA)
        self.assertEqual(self.artifact.read_bytes(), CHANGED)
        self.assertEqual(result['result']['result'], 'confirmed')
        self.assertTrue(result['simulation'])
        self.assertEqual(result['result']['limitation'], delivery.LIMITATION)
        self.assertEqual(result['result']['consumed_content'], {'sha256': sha(DATA), 'size_bytes': len(DATA)})
        # Exposure changes carry the deployment association without consuming a file again.
        with self.session().locked() as s, patch.object(artifacts, 'read_bytes', side_effect=AssertionError('enable reopened bytes')):
            enabled = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'flag-set',
                                       'parameters': {'enabled': True, 'cohort': 'internal'}})
            self.assertEqual(enabled['artifact_content'], proposal['artifact_content'])
            grant(self.host, self.pin, 'delivery-flag-set', enabled['id'])
            result = delivery.execute(s, enabled['id'])
            self.assertEqual(result['result']['result'], 'confirmed')
            self.assertNotIn('consumed_content', result['result'])

    def test_apply_rejects_forged_snapshot_and_query_rejects_forged_consumption_receipt(self):
        self.start()
        source = self.merged()
        with self.session().locked() as s:
            proposal = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'deploy',
                'parameters': {'artifact': source['subject']['artifact_digest']}})
        grant(self.host, self.pin, 'delivery-deploy', proposal['id'])
        provider_before = (self.host / 'sim-provider.json').read_bytes()
        real_apply = delivery.LocalSimulation.apply
        called = []
        def forged(provider, planned, snapshot=None):
            self.assertEqual(snapshot.data, DATA)
            called.append(True)
            fake = artifacts.Snapshot(CHANGED, sha(DATA), len(DATA))
            return real_apply(provider, planned, snapshot=fake)
        with patch.object(delivery.LocalSimulation, 'apply', forged), self.assertRaises(ValueError):
            self.execute_delivery(proposal['id'])
        self.assertEqual(called, [True], 'the real consumer boundary must inspect the forged data')
        self.assertEqual((self.host / 'sim-provider.json').read_bytes(), provider_before)
        # The started failed attempt is retained. Use a fresh exact plan for the
        # valid control rather than clearing or replaying that pending attempt.
        with self.session().locked() as s:
            valid = delivery.plan(s, {'schema': 'delivery-plan/v1', 'action': 'rollback',
                'parameters': {'artifact': 'sha256:' + sha(DATA)}, 'artifact_content': self.descriptor(),
                'artifact_source_merge': source['id']})
        grant(self.host, self.pin, 'delivery-rollback', valid['id'])
        self.assertEqual(self.execute_delivery(valid['id'])['result']['result'], 'confirmed')
        state = self.read()
        provider = self.read('sim-provider.json')
        receipt = provider['operations'][valid['id']]
        receipt['consumed_content']['sha256'] = sha(CHANGED)
        # Rebind the request to the corrupted receipt so only consumption
        # semantics, not its outer digest, can reject this record.
        state['delivery']['requests'][valid['id']]['receipt_sha'] = core.digest(receipt)
        write(self.host, 'state.json', state)
        write(self.host, 'sim-provider.json', provider)
        def query():
            with self.session().locked() as s:
                delivery.LocalSimulation(s).query(valid['id'])
        self.assert_no_action(query)

    def test_new_attempt_recovery_needs_no_current_artifact_or_fresh_authority(self):
        self.start()
        source = self.merged()
        proposal = self.rollback(source)
        self.assertEqual(self.execute_delivery(proposal['id'])['result']['result'], 'confirmed')
        self.artifact.unlink()
        grant(self.host, self.pin, 'delivery-rollback', proposal['id'], decision='revoke')
        grant(self.host, self.pin, 'merge-execute', source['id'], decision='revoke')
        state = self.read()
        state['subject'] = dict(self.subject, head='later-subject')
        state['budget']['operations'] = self.policy['budget']['max_operations']
        state['receipts'][source['id']]['result'] = 'unknown'
        request = state['delivery']['requests'][proposal['id']]
        request['result'] = 'pending'
        request.pop('observed_at')
        request.pop('receipt_sha')
        write(self.host, 'state.json', state)
        def recover():
            with self.session().locked() as s, patch.object(artifacts, 'read_bytes', side_effect=AssertionError('recovery read current artifact')):
                self.assertEqual(merge.query(s)['result'], 'confirmed')
                self.assertEqual(merge.execute(s)['result'], 'confirmed')
                self.assertEqual(delivery.LocalSimulation(s).query(proposal['id'])['result']['result'], 'confirmed')
                self.assertEqual(delivery.execute(s, proposal['id'])['result']['result'], 'confirmed')
        self.assert_no_action(recover, expected_exception=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
