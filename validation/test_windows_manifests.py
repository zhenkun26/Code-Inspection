"""Mandatory native manifest controls on Windows x64/local NTFS.

POSIX skips are N_A, never evidence that native enumeration or Git capture ran.
Real junction/symlink fixtures must succeed without changing host privileges.
"""
from contextlib import contextmanager
import hashlib
import importlib
import os
from pathlib import Path
import struct
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


@unittest.skipUnless(os.name == 'nt', 'N_A: native Windows manifest execution required')
class WindowsManifests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('support.source_manifests')
            self.native = importlib.import_module('support.windows_manifest_files')
        except ImportError:
            self.fail('native Windows source manifest capability missing')
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'source'; self.root.mkdir()
        self.win = self.native.win

    def git(self, *args):
        result = subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull,
                                 '-C', str(self.root), *args], env=git_environment(),
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def repo(self):
        self.git('init', '--template=')
        (self.root/'a').write_bytes(b'head\r\n')
        self.git('add', 'a'); self.git('commit', '-m', 'native fixture')
        return self.git('rev-parse', 'HEAD').strip().decode('ascii')

    def capture_git(self, head, **changes):
        return self.api.capture(self.root, config('git-worktree', **changes), base=head, head=head)

    def readonly(self, path, enabled=True):
        w = self.win
        get = w.bind(w.K, 'GetFileAttributesW', w.ULONG, w.w.LPCWSTR)
        put = w.bind(w.K, 'SetFileAttributesW', w.w.BOOL, w.w.LPCWSTR, w.ULONG)
        initial = get(str(path))
        self.assertNotEqual(initial, 0xffffffff)
        w.wincheck(put(str(path), initial | 1 if enabled else initial & ~1))

        def restore():
            if path.exists(): w.wincheck(put(str(path), initial))
        self.addCleanup(restore)

    def junction(self, path, target):
        result = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
                                 'mklink', '/J', str(path), str(target)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(path.is_junction(), 'required native junction was not created')
        self.addCleanup(lambda: path.rmdir() if path.is_junction() else None)

    @contextmanager
    def payload_observations(self):
        original = self.native.NativeSource.read
        calls = []
        def observed(source, path, *args):
            calls.append(path)
            return original(source, path, *args)
        with patch.object(self.native.NativeSource, 'read', observed), \
                patch.object(self.api, '_blobs', wraps=self.api._blobs) as blobs:
            yield calls, blobs

    def test_directory_bytes_empty_dirs_unicode_and_readonly_identity(self):
        payload = self.root/'中文 🧪 space.exe'; payload.write_bytes(b'\x00\xff\r\n')
        empty = self.root/'empty'; empty.mkdir()
        first = self.api.capture(self.root, config())
        self.assertEqual(first['mode_model'], 'windows-readonly/v1')
        self.assertEqual(first, self.api.capture(self.root, config()))
        entries = {entry['path']:entry for entry in first['entries']}
        self.assertEqual(entries[payload.name]['sha256'], hashlib.sha256(payload.read_bytes()).hexdigest())
        self.assertEqual(entries[payload.name]['mode'], 0, 'exe suffix does not prove an executable bit')
        self.assertEqual(entries['empty']['kind'], 'directory')
        self.readonly(payload)
        second = self.api.capture(self.root, config())
        self.assertNotEqual(self.api.identity(first), self.api.identity(second))
        self.assertEqual(next(e for e in second['entries'] if e['path']==payload.name)['mode'], 1)
        self.readonly(empty)
        third = self.api.capture(self.root, config())
        self.assertNotEqual(self.api.identity(second), self.api.identity(third))
        self.assertEqual(next(e for e in third['entries'] if e['path']=='empty')['mode'], 1)

    def test_git_executable_index_mode_and_readonly_are_independent(self):
        self.repo()
        self.git('update-index', '--chmod=+x', 'a'); self.git('commit', '-m', 'executable Git mode')
        head = self.git('rev-parse', 'HEAD').strip().decode('ascii')
        first = self.capture_git(head)
        entry = next(e for e in first['entries'] if e['path']=='a')
        self.assertEqual(entry['head']['mode'], 0o100755)
        self.assertEqual(entry['index']['mode'], 0o100755)
        self.assertEqual(entry['worktree']['mode'], 0)
        self.assertTrue(self.api.committed(first))
        self.readonly(self.root/'a')
        readonly = self.capture_git(head)
        self.assertTrue(self.api.committed(readonly))
        self.assertNotEqual(self.api.identity(first), self.api.identity(readonly))
        self.git('update-index', '--chmod=-x', 'a')
        staged = self.capture_git(head)
        self.assertFalse(self.api.committed(staged))
        current = next(e for e in staged['entries'] if e['path']=='a')
        self.assertEqual(current['worktree'], next(e for e in readonly['entries'] if e['path']=='a')['worktree'])
        self.assertEqual(current['index']['mode'], 0o100644)

    def test_git_ignored_paths_and_exclusion_precede_payload(self):
        head = self.repo()
        (self.root/'.gitignore').write_bytes(b'secret\n')
        (self.root/'secret').write_bytes(b'not authorized as source payload')
        with self.payload_observations() as (reads, blobs):
            with self.assertRaisesRegex(ValueError, 'ignored'):
                self.capture_git(head)
            self.assertEqual(reads, []); blobs.assert_not_called()
        with self.payload_observations() as (reads, _):
            result = self.capture_git(head, exclusions=['secret'])
        self.assertNotIn('secret', reads)
        self.assertNotIn('secret', [e['path'] for e in result['entries']])

    def test_limits_and_native_enumeration_fail_before_payload(self):
        (self.root/'a').write_bytes(b'abc'); (self.root/'b').write_bytes(b'def')
        for limits in ({'max_file_bytes':2}, {'max_total_bytes':5}, {'max_files':1}):
            with self.subTest(limits=limits), self.payload_observations() as (reads, blobs):
                with self.assertRaises(ValueError): self.api.capture(self.root, config(**limits))
                self.assertEqual(reads, []); blobs.assert_not_called()
        with patch.object(self.api, 'MAX_VISITED', 1), self.payload_observations() as (reads, _):
            with self.assertRaisesRegex(ValueError, 'enumeration limit'):
                self.api.capture(self.root, config())
            self.assertEqual(reads, [])
        (self.root/'nested').mkdir(); (self.root/'nested'/'a').write_bytes(b'x')
        with patch.object(self.api, 'MAX_DEPTH', 1), self.payload_observations() as (reads, _):
            with self.assertRaisesRegex(ValueError, 'depth limit'):
                self.api.capture(self.root, config())
            self.assertEqual(reads, [])

    def test_hardlink_refused_before_source_payload(self):
        (self.root/'a').write_bytes(b'private fixture')
        os.link(self.root/'a', self.root/'b')
        with self.payload_observations() as (reads, blobs):
            with self.assertRaisesRegex(ValueError, 'hardlink'):
                self.api.capture(self.root, config())
            self.assertEqual(reads, []); blobs.assert_not_called()

    def test_alternate_streams_on_file_directory_and_root_are_refused(self):
        leaf = self.root/'a'; leaf.write_bytes(b'ordinary')
        directory = self.root/'inside'; directory.mkdir()
        for target in (leaf, directory, self.root):
            with self.subTest(target=target):
                stream = str(target)+':fixture-hidden'
                with open(stream, 'wb') as output: output.write(b'not represented by an ordinary path')
                try:
                    with self.payload_observations() as (reads, blobs):
                        with self.assertRaisesRegex(ValueError, 'alternate data streams'):
                            self.api.capture(self.root, config())
                        self.assertEqual(reads, []); blobs.assert_not_called()
                finally: os.unlink(stream)

    def test_real_root_parent_nested_junction_and_symlink_are_refused(self):
        outside = self.base/'outside'; outside.mkdir(); (outside/'raw').write_bytes(b'OUTSIDE')
        alias = self.base/'root-alias'; self.junction(alias, self.root)
        nested = self.root/'junction'; self.junction(nested, outside)
        for selected in (alias, alias/'junction', self.root):
            with self.subTest(selected=selected), self.payload_observations() as (reads, _):
                with self.assertRaises((ValueError, OSError)):
                    self.api.capture(selected, config())
                self.assertEqual(reads, [])
        nested.rmdir()
        link = self.root/'file-link'
        link.symlink_to(outside/'raw')  # Failure to create the required native fixture is a failed test.
        try:
            with self.payload_observations() as (reads, _):
                with self.assertRaises((ValueError, OSError)):
                    self.api.capture(self.root, config())
                self.assertEqual(reads, [])
        finally: link.unlink()

    def test_synchronized_parent_replacement_never_captures_outside_bytes(self):
        inside = self.root/'inside'; inside.mkdir(); (inside/'a').write_bytes(b'INSIDE')
        outside = self.base/'outside'; outside.mkdir(); (outside/'a').write_bytes(b'OUTSIDE')
        original = self.native.NativeSource.read
        attempted = []
        def replaced(source, path, *args):
            if path == 'inside/a':
                attempted.append(True)
                try: inside.rename(self.root/'parked')
                except OSError: pass  # Held no-delete handles can themselves deny the attack.
                else: self.junction(inside, outside)
            return original(source, path, *args)
        with patch.object(self.native.NativeSource, 'read', replaced):
            try: result = self.api.capture(self.root, config())
            except (ValueError, OSError): result = None
        self.assertEqual(attempted, [True], 'the read-boundary mutation must actually be attempted')
        if result is not None:
            entry = next(e for e in result['entries'] if e['path']=='inside/a')
            self.assertEqual(entry['sha256'], hashlib.sha256(b'INSIDE').hexdigest())

    def test_observed_same_length_write_after_payload_is_refused(self):
        leaf = self.root/'a'; leaf.write_bytes(b'before')
        original = self.native.NativeSource.read
        changed = []
        def altered(source, path, *args):
            result = original(source, path, *args)
            if path == 'a':
                leaf.write_bytes(b'after!'); changed.append(True)
            return result
        with patch.object(self.native.NativeSource, 'read', altered):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.api.capture(self.root, config())
        self.assertEqual(changed, [True])

    def test_attribute_only_junction_race_does_not_read_target(self):
        gate = self.root/'gate'; gate.mkdir()
        outside = self.base/'outside'; outside.mkdir(); (outside/'a').write_bytes(b'OUTSIDE')
        w = self.win
        ioctl = w.bind(w.K, 'DeviceIoControl', w.w.BOOL, w.HANDLE, w.ULONG,
                       w.c.c_void_p, w.ULONG, w.c.c_void_p, w.ULONG,
                       w.c.POINTER(w.ULONG), w.c.c_void_p)
        opened = w.opened
        changed = []
        @contextmanager
        def intercept(parent, name, **options):
            with opened(parent, name, **options) as handle:
                if name == 'gate' and options.get('directory') and not changed:
                    attacker = w.CreateFile(str(gate), 0x100, 7, None, 3, 0x02200000, None)
                    if attacker == w.c.c_void_p(-1).value: raise w.c.WinError(w.c.get_last_error())
                    try:
                        substitute = ('\\??\\'+str(outside)).encode('utf-16-le')
                        printed = str(outside).encode('utf-16-le')
                        payload = struct.pack('<HHHH', 0, len(substitute), len(substitute)+2, len(printed)) \
                            + substitute + b'\0\0' + printed + b'\0\0'
                        raw = struct.pack('<IHH', 0xA0000003, len(payload), 0) + payload
                        buffer = w.c.create_string_buffer(raw); count = w.ULONG()
                        ok = ioctl(attacker, 0x900A4, buffer, len(raw), None, 0, w.c.byref(count), None)
                        self.assertTrue(ok, str(w.c.WinError(w.c.get_last_error())))
                        changed.append(True)
                    finally: w.close(attacker)
                yield handle
        try:
            with patch.object(w, 'opened', intercept), self.payload_observations() as (reads, _):
                with self.assertRaises((ValueError, OSError)):
                    self.api.capture(self.root, config())
                self.assertEqual(reads, [])
            self.assertEqual(changed, [True], 'the real reparse mutation must succeed')
        finally:
            if gate.is_junction(): gate.rmdir()

    def test_ancestor_git_marker_refuses_before_walk(self):
        (self.root/'a').write_bytes(b'private fixture')
        (self.base/'.git').mkdir()
        with patch.object(self.native.NativeSource, 'walk') as walk, self.payload_observations() as (reads, _):
            with self.assertRaisesRegex(ValueError, 'requires git-worktree'):
                self.api.capture(self.root, config())
            walk.assert_not_called(); self.assertEqual(reads, [])

    def test_changed_leaf_before_read_refuses_before_readfile(self):
        leaf = self.root/'a'; leaf.write_bytes(b'before')
        with self.native.NativeSource(self.root) as source:
            observed = source.walk(config())
            leaf.write_bytes(b'after!')
            with patch.object(self.win, 'ReadFile', wraps=self.win.ReadFile) as read:
                with self.assertRaisesRegex(ValueError, 'changed'):
                    source.read('a', observed['a'], 32)
                read.assert_not_called()

    def test_git_requires_explicit_disabled_native_filemode(self):
        head = self.repo()
        self.assertEqual(self.git('config', 'core.filemode').strip(), b'false')
        self.git('config', 'core.filemode', 'true')
        with self.payload_observations() as (reads, blobs):
            with self.assertRaisesRegex(ValueError, 'filemode'):
                self.capture_git(head)
            self.assertEqual(reads, []); blobs.assert_not_called()
        self.git('config', '--unset', 'core.filemode')
        with self.assertRaisesRegex(ValueError, 'filemode'):
            self.capture_git(head)


if __name__ == '__main__':
    unittest.main(verbosity=2)
