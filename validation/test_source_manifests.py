"""Capability and failure-boundary tests for optional source manifests."""
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

    def test_directory_actual_bytes_and_mode(self):
        (self.root/'a').write_bytes(b'one')
        if os.name == 'nt':
            with self.assertRaisesRegex(ValueError, 'unsupported'): self.api.capture(self.root, config())
            return
        first = self.api.capture(self.root, config())
        self.assertEqual(first, self.api.capture(self.root, config()))
        (self.root/'a').write_bytes(b'two')
        second = self.api.capture(self.root, config())
        self.assertNotEqual(self.api.identity(first), self.api.identity(second))
        (self.root/'a').chmod(0o755)
        self.assertNotEqual(self.api.identity(second), self.api.identity(self.api.capture(self.root, config())))

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only; refusal tested separately')
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

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
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

    def test_platform_refusal_and_pure_validator(self):
        sample = {'schema':'source-manifest/v1','mode_model':'posix-permissions/v1',
                  'config':config(), 'entries':[]}
        self.assertEqual(len(self.api.validate(sample)), 64)
        with patch.object(self.api.os, 'name', 'nt'):
            with self.assertRaisesRegex(ValueError, 'unsupported'): self.api.capture(self.root, config())
        sample['mode_model']='invented'
        with self.assertRaises(ValueError): self.api.validate(sample)
        sample['mode_model']='posix-permissions/v1'
        sample['config']['max_total_bytes']=False
        with self.assertRaises(ValueError): self.api.validate(sample)
        sample['config']=config(exclusions=['secret'])
        sample['entries']=[{'path':'secret','kind':'directory','mode':0o755}]
        with self.assertRaises(ValueError): self.api.validate(sample)

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
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

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_ignored_payload_never_read_and_limits_preflight(self):
        head = self.repo(); (self.root/'.gitignore').write_text('secret\n'); (self.root/'secret').write_bytes(b'private')
        with patch.object(self.api, '_read', wraps=self.api._read) as read, patch.object(self.api, '_blobs', wraps=self.api._blobs) as blobs:
            with self.assertRaisesRegex(ValueError, 'ignored'): self.api.capture(self.root, config('git-worktree'), base=head, head=head)
            read.assert_not_called(); blobs.assert_not_called()
        with patch.object(self.api, '_read', wraps=self.api._read) as read:
            with self.assertRaises(ValueError): self.api.capture(self.root, config(max_files=1))
            read.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
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

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_deleted_ignored_index_and_head_payload_not_read(self):
        head=self.repo(); (self.root/'a').unlink(); (self.root/'.gitignore').write_text('a\n')
        with patch.object(self.api, '_blobs', wraps=self.api._blobs) as blobs:
            with self.assertRaisesRegex(ValueError, 'ignored'):
                self.api.capture(self.root, config('git-worktree'), base=head,head=head)
            blobs.assert_not_called()
        result=self.api.capture(self.root,config('git-worktree',exclusions=['a']),base=head,head=head)
        self.assertNotIn('a',[entry['path'] for entry in result['entries']])

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
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

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
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

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
    def test_git_metrics_and_logical_aggregate_bound(self):
        from interfaces.lifecycle.accounting import InvocationMetrics
        head=self.repo(); metrics=InvocationMetrics()
        self.api.capture(self.root, config('git-worktree'), base=head,head=head,metrics=metrics)
        self.assertGreater(metrics.git_subprocess_attempts,0); self.assertEqual(metrics.repository_scan_attempts,2)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            self.api.capture(self.root, config('git-worktree',max_total_bytes=10),base=head,head=head)

    @unittest.skipIf(os.name == 'nt', 'POSIX manifest backend only')
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
