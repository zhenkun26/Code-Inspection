"""Capability and failure-boundary tests for optional source manifests."""
import copy
import hashlib
import importlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from support.git_runtime import git_environment
from validation.test_support import temporary_directory


def config(mode='directory', **changes):
    result = dict(mode=mode, scope=['.'], exclusions=[], max_files=1024,
                  max_file_bytes=1048576, max_total_bytes=8388608,
                  isolation='host-controlled')
    result.update(changes)
    return result


def transition_sample(mode_model='posix-permissions/v1', **limits):
    h = dict(oid='1'*40, sha256=hashlib.sha256(b'bytes').hexdigest(), size_bytes=5, mode=0o100755)
    mode = 0 if mode_model == 'windows-readonly/v1' else 0o755
    return dict(schema='source-manifest/v1', mode_model=mode_model,
                config=config('git-worktree', **limits), base='1'*40, head='2'*40, head_tree='3'*40,
                directories=[dict(path=path, kind='directory', mode=mode) for path in ('.', 'a')],
                entries=[dict(path='a', head=h, index=dict(h), worktree=None)])


class SourceManifestTests(unittest.TestCase):
    def setUp(self):
        try: self.api = importlib.import_module('support.source_manifests')
        except ImportError: self.fail('source manifest capture capability missing')
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def git(self, *args):
        return subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull, '-C', str(self.root), *args],
                              env=git_environment(), capture_output=True, check=True, timeout=10).stdout.strip().decode()

    def repo(self):
        self.git('init', '--template=')
        (self.root/'a').write_bytes(b'head\n')
        self.git('add', 'a'); self.git('commit', '-m', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def capture_transition(self, head, **limits):
        try:
            result = self.api.capture(self.root, config('git-worktree', **limits), base=head, head=head)
        except ValueError as exc:
            self.fail('supported Git type transition refused: '+str(exc))
        self.assertEqual(result, self.api.capture(self.root, config('git-worktree', **limits), base=head, head=head))
        self.assertFalse(self.api.committed(result))
        return result

    def test_file_to_directory_staged_and_unstaged(self):
        head = self.repo()
        clean = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        original = clean['entries'][0]
        self.assertTrue(self.api.committed(clean))
        (self.root/'a').unlink(); (self.root/'a').mkdir()
        snapshots = []
        for populated in (False, True):
            if populated:
                (self.root/'a'/'new').write_bytes(b'new\n')
                self.git('read-tree', head)
            for staged in (False, True):
                with self.subTest(populated=populated, staged=staged):
                    if staged: self.git('add', '-A')
                    result = self.capture_transition(head)
                    entries = {entry['path']:entry for entry in result['entries']}
                    self.assertEqual([entry['path'] for entry in result['directories']], ['.', 'a'])
                    self.assertEqual(entries['a']['head'], original['head'])
                    self.assertEqual(entries['a']['index'], None if staged else original['index'])
                    self.assertIsNone(entries['a']['worktree'])
                    if populated:
                        self.assertIsNone(entries['a/new']['head'])
                        self.assertEqual(entries['a/new']['index'] is not None, staged)
                        self.assertIsNotNone(entries['a/new']['worktree'])
                    snapshots.append(result)
        self.assertEqual(len({self.api.identity(result) for result in snapshots}), 4)
        (self.root/'a'/'new').write_bytes(b'changed\n')
        changed = self.capture_transition(head)
        self.assertNotEqual(self.api.identity(snapshots[-1]), self.api.identity(changed))
        self.assertEqual(snapshots[-1]['entries'][1]['index'], changed['entries'][1]['index'])

    def test_directory_to_file_staged_and_unstaged(self):
        self.repo(); (self.root/'a').unlink(); (self.root/'a').mkdir()
        (self.root/'a'/'old').write_bytes(b'old\n')
        self.git('add', '-A'); self.git('commit', '-m', 'directory fixture')
        head = self.git('rev-parse', 'HEAD')
        clean = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        self.assertTrue(self.api.committed(clean))
        old = clean['entries'][0]
        (self.root/'a'/'old').unlink(); (self.root/'a').rmdir()
        (self.root/'a').write_bytes(b'replacement\n')
        unstaged = self.capture_transition(head)
        self.git('add', '-A'); staged = self.capture_transition(head)
        for result, is_staged in ((unstaged, False), (staged, True)):
            entries = {entry['path']:entry for entry in result['entries']}
            self.assertEqual([entry['path'] for entry in result['directories']], ['.'])
            self.assertEqual(entries['a/old']['head'], old['head'])
            self.assertEqual(entries['a/old']['index'], None if is_staged else old['index'])
            self.assertIsNone(entries['a/old']['worktree'])
            self.assertIsNone(entries['a']['head'])
            self.assertEqual(entries['a']['index'] is not None, is_staged)
            self.assertIsNotNone(entries['a']['worktree'])
        self.assertNotEqual(self.api.identity(unstaged), self.api.identity(staged))
        self.assertEqual(unstaged['entries'][0]['worktree'], staged['entries'][0]['worktree'])

    def test_index_only_file_with_worktree_directory_and_reverse(self):
        head = self.repo()
        (self.root/'a').unlink(); (self.root/'a').mkdir()
        (self.root/'a'/'old').write_bytes(b'old\n')
        self.git('add', '-A'); self.git('commit', '-m', 'directory fixture')
        head = self.git('rev-parse', 'HEAD')
        (self.root/'a'/'old').unlink(); (self.root/'a').rmdir()
        (self.root/'a').write_bytes(b'index only\n'); self.git('add', '-A')
        (self.root/'a').unlink(); (self.root/'a').mkdir()
        (self.root/'a'/'new').write_bytes(b'current\n')
        directory = self.capture_transition(head)
        entries = {entry['path']:entry for entry in directory['entries']}
        self.assertIsNone(entries['a']['head']); self.assertIsNotNone(entries['a']['index'])
        self.assertIsNone(entries['a']['worktree'])
        self.assertIsNotNone(entries['a/old']['head']); self.assertIsNone(entries['a/old']['index'])
        self.assertIsNone(entries['a/new']['index']); self.assertIsNotNone(entries['a/new']['worktree'])
        self.assertEqual([entry['path'] for entry in directory['directories']], ['.', 'a'])
        self.git('add', '-A')
        (self.root/'a'/'new').unlink(); (self.root/'a').rmdir()
        (self.root/'a').write_bytes(b'current file\n')
        reverse = self.capture_transition(head)
        entries = {entry['path']:entry for entry in reverse['entries']}
        self.assertIsNone(entries['a']['index']); self.assertIsNotNone(entries['a']['worktree'])
        self.assertIsNotNone(entries['a/new']['index']); self.assertIsNone(entries['a/new']['worktree'])
        self.assertEqual([entry['path'] for entry in reverse['directories']], ['.'])
        self.assertNotEqual(self.api.identity(directory), self.api.identity(reverse))

    def test_transition_unique_path_count_and_portable_mode_models(self):
        for model in ('posix-permissions/v1', 'windows-readonly/v1'):
            with self.subTest(model=model):
                sample = transition_sample(model, max_files=2)
                try: identity = self.api.identity(sample)
                except ValueError as exc: self.fail('two selected paths refused: '+str(exc))
                self.assertEqual(identity, self.api.identity(copy.deepcopy(sample)))
                self.assertFalse(self.api.committed(sample))
                over = copy.deepcopy(sample)
                h = over['entries'][0]['head']
                over['entries'].append(dict(path='b', head=dict(h), index=None, worktree=None))
                with self.assertRaisesRegex(ValueError, 'directory coverage'): self.api.validate(over)
                over = copy.deepcopy(sample); over['config']['max_files'] = 1
                with self.assertRaises(ValueError): self.api.validate(over)

    def test_transition_cannot_claim_current_file_and_directory(self):
        for model in ('posix-permissions/v1', 'windows-readonly/v1'):
            with self.subTest(model=model):
                sample = transition_sample(model)
                h = sample['entries'][0]['head']
                sample['entries'][0]['worktree'] = dict(mode=sample['directories'][0]['mode'],
                                                       size_bytes=5, sha256=h['sha256'])
                with self.assertRaisesRegex(ValueError, 'directory coverage'): self.api.validate(sample)

    def test_transition_path_coverage_collision_and_projection_limits(self):
        sample = transition_sample()
        for name in ('A', 'A/child', 'é', 'e\u0301/child'):
            bad = copy.deepcopy(sample)
            if name.startswith(('é', 'e\u0301')):
                bad['entries'][0]['path'] = 'é'
                bad['directories'][1]['path'] = 'é'
                if name == 'é': name = 'e\u0301'
            bad['directories'].append(dict(path=name, kind='directory', mode=0o755))
            bad['directories'].sort(key=lambda entry:entry['path'].encode())
            with self.subTest(collision=name), self.assertRaisesRegex(ValueError, 'collision'):
                self.api.validate(bad)
        for change in ('duplicate-directory', 'directory-order', 'duplicate-entry', 'missing-scope',
                       'outside-scope', 'reserved', 'git-marker', 'directory-mode', 'file-bytes', 'total-bytes'):
            bad = copy.deepcopy(sample)
            if change == 'duplicate-directory': bad['directories'].append(dict(bad['directories'][1]))
            elif change == 'directory-order': bad['directories'].reverse()
            elif change == 'duplicate-entry': bad['entries'].append(copy.deepcopy(bad['entries'][0]))
            elif change == 'missing-scope':
                bad['config']['scope'] = ['a', 'missing']; bad['directories'].pop(0)
            elif change == 'outside-scope': bad['config']['scope'] = ['missing']
            elif change in ('reserved', 'git-marker'):
                path = 'NUL' if change == 'reserved' else '.git/private'
                bad['entries'][0]['path'] = path; bad['directories'][1]['path'] = path
            elif change == 'directory-mode': bad['directories'][1]['mode'] = -1
            elif change == 'file-bytes': bad['config']['max_file_bytes'] = 4
            else: bad['config']['max_total_bytes'] = 9
            with self.subTest(change=change), self.assertRaises(ValueError): self.api.validate(bad)

    def test_transition_capture_selected_limit_precedes_payload(self):
        self.repo(); (self.root/'b').write_bytes(b'other\n')
        self.git('add', 'b'); self.git('commit', '-m', 'historical second file')
        head = self.git('rev-parse', 'HEAD')
        (self.root/'b').unlink(); (self.root/'a').unlink(); (self.root/'a').mkdir()
        for populated, count in ((False, 3), (True, 4)):
            with self.subTest(populated=populated):
                if populated: (self.root/'a'/'new').write_bytes(b'new\n')
                result = self.capture_transition(head, max_files=count)
                paths = {entry['path'] for entry in result['entries']+result['directories']}
                self.assertEqual(len(paths), count)
                with patch.object(self.api, '_blobs', wraps=self.api._blobs) as blobs, \
                        patch.object(self.api, '_read', wraps=self.api._read) as read:
                    with self.assertRaisesRegex(ValueError, 'manifest entry limit'):
                        self.api.capture(self.root, config('git-worktree', max_files=count-1), base=head, head=head)
                    blobs.assert_not_called(); read.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX replacement control; native handles prevent ordinary replacement')
    def test_transition_observed_directory_replacement_is_refused(self):
        head = self.repo(); (self.root/'a').unlink(); (self.root/'a').mkdir()
        leaf = self.root/'a'/'new'; leaf.write_bytes(b'current\n')
        original = self.api._read; attempts = []
        def changed(*args):
            result = original(*args)
            leaf.unlink(); (self.root/'a').rmdir(); (self.root/'a').write_bytes(b'replaced\n')
            attempts.append(True)
            return result
        with patch.object(self.api, '_read', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        self.assertEqual(attempts, [True])

    def test_historical_git_identity_is_unchanged_and_validation_is_pure(self):
        expected = {'posix-permissions/v1':'048c3f67287889fa65182808bebac5519d49911ce0dc4c1b26baae81a63952d1',
                    'windows-readonly/v1':'20ca4d7ca564e008ffe738cd9010d0924e9c0a16814e117876821b3a4ea8b50d'}
        for model, digest in expected.items():
            sample = transition_sample(model); sample['directories'] = []
            h = sample['entries'][0]['head']
            sample['entries'][0]['worktree'] = dict(mode=0 if model == 'windows-readonly/v1' else 0o755,
                                                   size_bytes=5, sha256=h['sha256'])
            with self.subTest(model=model), patch.object(self.api, '_source') as source, \
                    patch.object(self.api, '_metadata') as metadata:
                self.assertEqual(self.api.identity(sample), 'source-manifest-sha256:'+digest)
                self.assertTrue(self.api.committed(sample))
                source.assert_not_called(); metadata.assert_not_called()

    def test_directory_actual_bytes_and_mode(self):
        (self.root/'a').write_bytes(b'one')
        first = self.api.capture(self.root, config())
        self.assertEqual(first, self.api.capture(self.root, config()))
        (self.root/'a').write_bytes(b'two')
        second = self.api.capture(self.root, config())
        self.assertNotEqual(self.api.identity(first), self.api.identity(second))
        (self.root/'a').chmod(0o444 if os.name == 'nt' else 0o755)
        try: self.assertNotEqual(self.api.identity(second), self.api.identity(self.api.capture(self.root, config())))
        finally: (self.root/'a').chmod(0o666)


    def test_staged_unstaged_deletion_and_untracked(self):
        head = self.repo()
        clean = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        (self.root/'a').write_bytes(b'staged\n'); self.git('add', 'a')
        (self.root/'a').write_bytes(b'unstaged\n'); (self.root/'new').write_bytes(b'new')
        dirty = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        self.assertNotEqual(self.api.identity(clean), self.api.identity(dirty))
        entry = next(x for x in dirty['entries'] if x['path']=='a')
        self.assertEqual(len({entry[k]['sha256'] for k in ('head','index','worktree')}), 3)
        self.assertIsNone(next(x for x in dirty['entries'] if x['path']=='new')['index'])
        (self.root/'a').unlink()
        deleted = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        self.assertIsNone(next(x for x in deleted['entries'] if x['path']=='a')['worktree'])


    def test_ignored_requires_explicit_exclusion(self):
        head = self.repo(); (self.root/'.gitignore').write_text('secret\n'); (self.root/'secret').write_bytes(b'private')
        with self.assertRaisesRegex(ValueError, 'ignored'): self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        result = self.api.capture(self.root, config('git-worktree', exclusions=['secret']), base=head, head=head)
        self.assertNotIn('secret', [x['path'] for x in result['entries']])

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_links_limits_collision_and_pure_validation(self):
        (self.root/'a').write_bytes(b'abc')
        with self.assertRaises(ValueError): self.api.capture(self.root, config(max_file_bytes=2))
        (self.root/'link').symlink_to('a')
        with self.assertRaises(ValueError): self.api.capture(self.root, config())
        (self.root/'link').unlink(); snapshot = self.api.capture(self.root, config())
        # Case-insensitive filesystems cannot physically hold both spellings.
        import copy
        collision = copy.deepcopy(snapshot)
        collision['entries'].append(dict(next(x for x in snapshot['entries'] if x['path']=='a'),path='A'))
        collision['entries'].sort(key=lambda x:x['path'].encode())
        with self.assertRaises(ValueError): self.api.validate(collision)
        (self.root/'a').unlink()
        self.assertEqual(self.api.identity(snapshot), 'source-manifest-sha256:'+self.api.validate(snapshot))
        snapshot['entries'][0]['sha256']='not-a-hash'
        with self.assertRaises(ValueError): self.api.validate(snapshot)

    def test_windows_mode_model_is_pure_and_distinct(self):
        import copy, hashlib
        payload = b'bytes'
        h = dict(oid='1'*40, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=5, mode=0o100755)
        sample = dict(schema='source-manifest/v1', mode_model='windows-readonly/v1',
                      config=config('git-worktree'), base='1'*40, head='2'*40, head_tree='3'*40,
                      directories=[], entries=[dict(path='a', head=h, index=dict(h),
                      worktree=dict(mode=0, size_bytes=5, sha256=h['sha256']))])
        try: identity = self.api.identity(sample)
        except ValueError as exc: self.fail('Windows stored mode model is unsupported: '+str(exc))
        self.assertTrue(self.api.committed(sample))
        readonly = copy.deepcopy(sample); readonly['entries'][0]['worktree']['mode']=1
        self.assertNotEqual(identity, self.api.identity(readonly))
        self.assertTrue(self.api.committed(readonly))
        staged = copy.deepcopy(sample); staged['entries'][0]['index']['mode']=0o100644
        self.assertFalse(self.api.committed(staged))
        bad = copy.deepcopy(sample); bad['entries'][0]['worktree']['mode']=0o666
        with self.assertRaises(ValueError): self.api.validate(bad)
        bad = copy.deepcopy(sample); bad['directories']=[dict(path='.',kind='directory',mode=2)]
        with self.assertRaises(ValueError): self.api.validate(bad)

    def test_platform_refusal_and_pure_validator(self):
        sample = {'schema':'source-manifest/v1','mode_model':'posix-permissions/v1',
                  'config':config(), 'entries':[]}
        self.assertEqual(len(self.api.validate(sample)), 64)
        sample['mode_model']='invented'
        with self.assertRaises(ValueError): self.api.validate(sample)
        sample['mode_model']='posix-permissions/v1'
        sample['config']['max_total_bytes']=False
        with self.assertRaises(ValueError): self.api.validate(sample)
        sample['config']=config(exclusions=['secret'])
        sample['entries']=[{'path':'secret','kind':'directory','mode':0o755}]
        with self.assertRaises(ValueError): self.api.validate(sample)


    def test_scope_empty_directory_exclusion_and_nested_repo(self):
        (self.root/'empty').mkdir(); (self.root/'secret').write_bytes(b'private')
        result = self.api.capture(self.root, config(exclusions=['secret']))
        self.assertIn('empty', [x['path'] for x in result['entries']])
        self.assertNotIn('secret', [x['path'] for x in result['entries']])
        with self.assertRaisesRegex(ValueError, 'scope unavailable'):
            self.api.capture(self.root, config(scope=['missing']))
        (self.root/'empty'/'.git').mkdir()
        with self.assertRaisesRegex(ValueError, 'nested Git'): self.api.capture(self.root, config())

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_nonregular_and_hardlink_preflight_before_payload(self):
        (self.root/'a').write_bytes(b'bytes')
        os.link(self.root/'a', self.root/'b')
        with patch.object(self.api, '_read', wraps=self.api._read) as read:
            with self.assertRaisesRegex(ValueError, 'hardlink'): self.api.capture(self.root, config())
            read.assert_not_called()
        (self.root/'b').unlink(); os.mkfifo(self.root/'fifo')
        with patch.object(self.api, '_read', wraps=self.api._read) as read:
            with self.assertRaisesRegex(ValueError, 'unsupported'): self.api.capture(self.root, config())
            read.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_changed_file_and_swapped_parent_fail_closed(self):
        directory = self.root/'inside'; directory.mkdir(); (directory/'a').write_bytes(b'before')
        original = self.api._read
        def changed(*args):
            result = original(*args); (directory/'a').write_bytes(b'after!'); return result
        with patch.object(self.api, '_read', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed'): self.api.capture(self.root, config())
        outside = self.root/'outside'; outside.mkdir(); (outside/'a').write_bytes(b'not selected')
        def swapped(*args):
            directory.rename(self.root/'old'); directory.symlink_to(outside, target_is_directory=True)
            return original(*args)
        with patch.object(self.api, '_read', side_effect=swapped):
            with self.assertRaises((OSError, ValueError)): self.api.capture(self.root, config(scope=['inside']))


    def test_ignored_payload_never_read_and_limits_preflight(self):
        head = self.repo(); (self.root/'.gitignore').write_text('secret\n'); (self.root/'secret').write_bytes(b'private')
        with patch.object(self.api, '_read', wraps=self.api._read) as read, patch.object(self.api, '_blobs', wraps=self.api._blobs) as blobs:
            with self.assertRaisesRegex(ValueError, 'ignored'): self.api.capture(self.root, config('git-worktree'), base=head, head=head)
            read.assert_not_called(); blobs.assert_not_called()
        with patch.object(self.api, '_read', wraps=self.api._read) as read:
            with self.assertRaises(ValueError): self.api.capture(self.root, config(max_files=1))
            read.assert_not_called()


    def test_blob_size_preflight_before_batch_payload(self):
        head = self.repo(); (self.root/'a').write_bytes(b'x'*100); self.git('add','a'); self.git('commit','-m','large')
        head = self.git('rev-parse','HEAD'); (self.root/'a').write_bytes(b'x')
        calls=[]; original=self.api._Git.run
        def observed(instance, *args, **kwargs):
            calls.append(args); return original(instance, *args, **kwargs)
        with patch.object(self.api._Git, 'run', observed):
            with self.assertRaisesRegex(ValueError, 'blob byte limit'):
                self.api.capture(self.root, config('git-worktree', max_file_bytes=16), base=head, head=head)
        self.assertIn(('cat-file','--batch-check'), calls)
        self.assertNotIn(('cat-file','--batch'), calls)


    def test_deleted_ignored_index_and_head_payload_not_read(self):
        head=self.repo(); (self.root/'a').unlink(); (self.root/'.gitignore').write_text('a\n')
        with patch.object(self.api, '_blobs', wraps=self.api._blobs) as blobs:
            with self.assertRaisesRegex(ValueError, 'ignored'):
                self.api.capture(self.root, config('git-worktree'), base=head,head=head)
            blobs.assert_not_called()
        result=self.api.capture(self.root,config('git-worktree',exclusions=['a']),base=head,head=head)
        self.assertNotIn('a',[entry['path'] for entry in result['entries']])


    def test_git_flags_layout_config_and_lfs_fail(self):
        head = self.repo(); self.git('update-index','--assume-unchanged','a')
        with self.assertRaisesRegex(ValueError, 'stage/flags'): self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        self.git('update-index','--no-assume-unchanged','a')
        self.git('config','core.fsmonitor','unsafe-helper')
        with patch.object(self.api._Git, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'config'): self.api.capture(self.root, config('git-worktree'), base=head, head=head)
            run.assert_not_called()
        self.git('config','--unset','core.fsmonitor')
        (self.root/'a').write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n')
        with self.assertRaisesRegex(ValueError, 'LFS pointer'): self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        (self.root/'.git').rename(self.root/'metadata'); (self.root/'.git').write_text('gitdir: metadata\n')
        with self.assertRaises(ValueError): self.api.capture(self.root, config('git-worktree', exclusions=['metadata']), base=head, head=head)


    def test_git_identity_dirty_states_and_bounded_output(self):
        head = self.repo()
        snapshot = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        self.assertTrue(self.api.committed(snapshot))
        with self.assertRaises(ValueError): self.api.capture(self.root, config('git-worktree'), base='0'*40, head=head)
        self.git('rm','a'); (self.root/'a').write_bytes(b'recreated')
        snapshot = self.api.capture(self.root, config('git-worktree'), base=head, head=head)
        entry=next(x for x in snapshot['entries'] if x['path']=='a')
        self.assertIsNone(entry['index']); self.assertIsNotNone(entry['head']); self.assertIsNotNone(entry['worktree'])
        self.assertFalse(self.api.committed(snapshot))
        reader=self.api._Git(self.root,None)
        with self.assertRaisesRegex(ValueError, 'output limit'): reader.run('cat-file','-p',head,limit=1)


    def test_git_metrics_and_logical_aggregate_bound(self):
        from interfaces.lifecycle.accounting import InvocationMetrics
        head=self.repo(); metrics=InvocationMetrics()
        self.api.capture(self.root, config('git-worktree'), base=head,head=head,metrics=metrics)
        self.assertGreater(metrics.git_subprocess_attempts,0); self.assertEqual(metrics.repository_scan_attempts,2)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            self.api.capture(self.root, config('git-worktree',max_total_bytes=10),base=head,head=head)


    def test_index_and_worktree_changes_are_separate(self):
        head=self.repo()
        def entry(): return next(x for x in self.api.capture(self.root,config('git-worktree'),base=head,head=head)['entries'] if x['path']=='a')
        original=entry(); (self.root/'a').write_bytes(b'edited')
        unstaged=entry()
        self.assertEqual(original['head'],unstaged['head']); self.assertEqual(original['index'],unstaged['index'])
        self.assertNotEqual(original['worktree'],unstaged['worktree'])
        self.git('add','a'); staged=entry()
        self.assertEqual(unstaged['head'],staged['head']); self.assertEqual(unstaged['worktree'],staged['worktree'])
        self.assertNotEqual(unstaged['index'],staged['index'])
        self.git('update-index','--chmod=+x','a'); mode_staged=entry()
        self.assertEqual(mode_staged['worktree'],staged['worktree'])
        self.assertNotEqual(mode_staged['index']['mode'],staged['index']['mode'])

    def test_pure_git_mode_projection_zero_ids_and_unicode(self):
        import copy, hashlib
        data=b'bytes'; h={'oid':'1'*40,'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data),'mode':0o100755}
        w={key:value for key,value in h.items() if key!='oid'}; w['mode']=0o755
        snapshot={'schema':'source-manifest/v1','mode_model':'posix-permissions/v1','config':config('git-worktree'),
                  'base':'1'*40,'head':'2'*40,'head_tree':'3'*40,'directories':[],
                  'entries':[{'path':'a','head':dict(h),'index':dict(h),'worktree':w}]}
        self.assertTrue(self.api.committed(snapshot))
        w['mode']=0o645; self.assertFalse(self.api.committed(snapshot))
        snapshot['entries'][0]['head']['mode']=snapshot['entries'][0]['index']['mode']=0o100644
        self.assertTrue(self.api.committed(snapshot))
        w['mode']=0o744; self.assertFalse(self.api.committed(snapshot))
        for key in ('base','head','head_tree'):
            invalid=copy.deepcopy(snapshot); invalid[key]='0'*40
            with self.assertRaises(ValueError): self.api.validate(invalid)
        directory={'schema':'source-manifest/v1','mode_model':'posix-permissions/v1','config':config(),
                   'entries':[{'path':'e\u0301','kind':'directory','mode':0o755},{'path':'é','kind':'directory','mode':0o755}]}
        with self.assertRaisesRegex(ValueError,'collision'): self.api.validate(directory)

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_metadata_caps_fail_before_payload(self):
        (self.root/'a').write_bytes(b'abc'); (self.root/'b').write_bytes(b'def')
        for limits in ({'max_file_bytes':2},{'max_total_bytes':5},{'max_files':1}):
            with self.subTest(limits=limits), patch.object(self.api,'_read',wraps=self.api._read) as read:
                with self.assertRaises(ValueError): self.api.capture(self.root,config(**limits))
                read.assert_not_called()
        with patch.object(self.api,'MAX_VISITED',1):
            with self.assertRaisesRegex(ValueError,'enumeration limit'): self.api.capture(self.root,config())
        (self.root/'nested').mkdir(); (self.root/'nested'/'a').write_bytes(b'x')
        with patch.object(self.api,'MAX_DEPTH',1):
            with self.assertRaisesRegex(ValueError,'depth limit'): self.api.capture(self.root,config())

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_reader_never_traverses_swapped_parent_link(self):
        parent=self.root/'parent'; parent.mkdir(); leaf=parent/'a'; leaf.write_bytes(b'read boundary')
        stamp=self.api._stamp(leaf.stat()); parent.rename(self.root/'original')
        parent.symlink_to(self.root/'original',target_is_directory=True)
        fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
        try:
            with self.assertRaises((OSError,ValueError)): self.api._read(fd,'parent/a',stamp,100)
        finally: os.close(fd)

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_exclusion_precedes_payload_and_unicode_order(self):
        (self.root/'secret').write_bytes(b'never read'); (self.root/'中文').write_bytes(b'utf8 name'); (self.root/'z').write_bytes(b'z')
        original=self.api._read; selected=[]
        def observe(fd,path,*args): selected.append(path); return original(fd,path,*args)
        with patch.object(self.api,'_read',side_effect=observe): result=self.api.capture(self.root,config(exclusions=['secret']))
        self.assertNotIn('secret',selected)
        self.assertEqual([x['path'] for x in result['entries']],sorted([x['path'] for x in result['entries']],key=lambda p:p.encode()))
        self.assertIn('中文',selected)

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_directory_mode_rejects_git_marker_before_enumeration(self):
        (self.root/'a').write_bytes(b'ordinary directory payload')
        self.assertIn('a',[x['path'] for x in self.api.capture(self.root,config())['entries']])
        marker=self.root/'.git'
        for kind in ('directory','file','link'):
            with self.subTest(kind=kind):
                if kind=='directory': marker.mkdir()
                elif kind=='file': marker.write_text('gitdir: unavailable\n')
                else: marker.symlink_to('missing')
                try:
                    with patch.object(self.api,'_walk',wraps=self.api._walk) as walk, patch.object(self.api,'_read',wraps=self.api._read) as read:
                        with self.assertRaisesRegex(ValueError,'requires git-worktree'):
                            self.api.capture(self.root,config())
                        walk.assert_not_called(); read.assert_not_called()
                finally:
                    if kind=='directory': marker.rmdir()
                    else: marker.unlink()
        marker.mkdir(); child=self.root/'subtree'; child.mkdir(); (child/'secret').write_bytes(b'ignored-by-ancestor')
        with patch.object(self.api,'_walk',wraps=self.api._walk) as walk:
            with self.assertRaisesRegex(ValueError,'requires git-worktree'):
                self.api.capture(child,config())
            walk.assert_not_called()


if __name__ == '__main__': unittest.main()
