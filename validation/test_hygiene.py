"""Distribution checks use synthetic paths only, without staging secret files."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import subprocess
import hashlib
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation import hygiene_check
from validation.test_support import temporary_directory


class Hygiene(unittest.TestCase):
    def test_manifest_checks_bytes_and_exact_file_coverage(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root / 'README.md').write_bytes(b'original\n')
            sha = hashlib.sha256(b'original\n').hexdigest()
            (root / 'SHA256SUMS').write_bytes((sha + '  README.md\n').encode('utf-8'))
            names = ['README.md', 'SHA256SUMS']
            self.assertEqual(hygiene_check.check_manifest(root, names), [])
            (root / 'README.md').write_bytes(b'changed\n')
            self.assertIn('mismatch', hygiene_check.check_manifest(root, names)[0]['reason'])
            (root / 'README.md').write_bytes(b'original\n')
            (root / 'extra.py').write_bytes(b'')
            self.assertIn('coverage', hygiene_check.check_manifest(root, names + ['extra.py'])[0]['reason'])
            self.assertIn('coverage', hygiene_check.check_manifest(root, ['SHA256SUMS'])[0]['reason'])

    def test_manifest_rejects_unsafe_duplicate_and_self_reference(self):
        with temporary_directory() as directory:
            root = Path(directory)
            for body in ['bad line', '0'*64+'  ../escape\n', '0'*64+'  /absolute\n',
                         '0'*64+'  SHA256SUMS\n', ('0'*64+'  duplicate\n')*2]:
                with self.subTest(body=body):
                    (root / 'SHA256SUMS').write_bytes((body).encode('utf-8'))
                    self.assertTrue(hygiene_check.check_manifest(root, ['SHA256SUMS']))

    def test_manifest_rejects_symlink_payload_and_missing_manifest(self):
        with temporary_directory() as directory:
            root = Path(directory)
            self.assertTrue(hygiene_check.check_manifest(root, []))
            (root / 'data.txt').write_bytes(b'file')
            (root / 'link.txt').symlink_to(root / 'data.txt')
            sha = hashlib.sha256(b'file').hexdigest()
            (root / 'SHA256SUMS').write_bytes((sha+'  data.txt\n'+sha+'  link.txt\n').encode('utf-8'))
            self.assertTrue(hygiene_check.check_manifest(root, ['data.txt', 'link.txt', 'SHA256SUMS']))

    def test_ignore_rules_cover_tracked_paths_and_template_exceptions(self):
        names=['.env','credentials.json','private/record.json','signing.key','debug.log',
               '.env.example','.env.sample','.env.template','README.md']
        self.assertEqual(hygiene_check.ignored_candidates(names),set(names[:5]))

    def test_force_staged_ignored_paths_fail_distribution(self):
        real=subprocess.run
        def simulated_index(command,**kwargs):
            if command[1]=='ls-files':
                return subprocess.CompletedProcess(command,0,b'.env\0credentials.json\0',b'')
            return real(command,**kwargs)
        with patch.object(hygiene_check.subprocess,'run',simulated_index):
            output=hygiene_check.check()
        self.assertEqual(output['result'],'FAIL')
        self.assertEqual({e['path'] for e in output['errors']},{'.env','credentials.json'})


class ReadBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'selected'
        self.root.mkdir()

    def files(self, payloads):
        for name, data in payloads.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        body = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                       for name, data in sorted(payloads.items()))
        (self.root / 'SHA256SUMS').write_bytes(body.encode('utf-8'))
        return sorted([*payloads, 'SHA256SUMS'])

    def inspect(self, names, ignored=(), manifest=False, before_read=None, after_read=None):
        reads = []
        raw = []
        self.contents_read = []
        bounded = hygiene_check.read_bytes
        real_run = subprocess.run
        read_bytes = Path.read_bytes
        read_text = Path.read_text
        def safe_read(root, name, *args, **kwargs):
            if before_read is not None and not reads:
                before_read()
            reads.append((str(name), kwargs.get('limit', args[0] if args else 1024 * 1024)))
            data = bounded(root, name, *args, **kwargs)
            self.contents_read.append(data)
            if after_read is not None:
                after_read(str(name), data)
            return data
        def bytes_read(path, *args, **kwargs):
            raw.append(str(path))
            data = read_bytes(path, *args, **kwargs)
            self.contents_read.append(data)
            return data
        def text_read(path, *args, **kwargs):
            raw.append(str(path))
            data = read_text(path, *args, **kwargs)
            self.contents_read.append(data.encode('utf-8'))
            return data
        def git_list(command, **kwargs):
            if os.name == 'nt' and command[:5] == ['cmd.exe', '/d', '/c', 'mklink', '/J']:
                return real_run(command, **kwargs)
            if command[1] != 'ls-files':
                raise AssertionError('Unexpected Git command in controlled selection fixture')
            return subprocess.CompletedProcess(command, 0, ('\0'.join(names) + '\0').encode(), b'')
        with patch.object(hygiene_check, 'ROOT', self.root), \
                patch.object(hygiene_check, 'ignored_candidates', return_value=set(ignored)), \
                patch.object(hygiene_check.subprocess, 'run', side_effect=git_list), \
                patch.object(hygiene_check, 'read_bytes', side_effect=safe_read), \
                patch.object(Path, 'read_bytes', bytes_read), patch.object(Path, 'read_text', text_read):
            result = hygiene_check.check_manifest(self.root, names) if manifest else hygiene_check.check()
        errors = result if manifest else result['errors']
        return errors, reads, raw

    def directory_link(self, link, target):
        if os.name == 'nt':
            created = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                     capture_output=True, timeout=10)
            self.assertEqual(created.returncode, 0, created.stderr)
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_private_and_ignored_selections_read_no_payload_in_both_entries(self):
        for name, ignored in [('private/secret.md', ()), ('force-selected.md', ('force-selected.md',)),
                              ('.git/config', ())]:
            names = self.files({name: b'SYNTHETIC private input; never a real secret\n'})
            for manifest in [False, True]:
                with self.subTest(name=name, manifest=manifest):
                    errors, reads, raw = self.inspect(names, ignored, manifest)
                    self.assertTrue(errors)
                    self.assertEqual(reads + raw, [], 'Rejected selections must not reach any payload API')

    def test_markdown_and_eight_character_files_use_bounded_io(self):
        names = self.files({'README.md': b'[target](target.txt)\n', 'target.txt': b'allowed',
                            'abcdefgh': b'blatX'})
        errors, reads, raw = self.inspect(names)
        self.assertEqual(errors, [])
        self.assertEqual(raw, [], 'No Path.read_bytes/read_text payload reads')
        self.assertTrue(reads)
        self.assertTrue(all(type(limit) is int and 0 < limit <= 1024 * 1024 for _, limit in reads))
        self.assertEqual(sorted(name for name, _ in reads), names, 'Payloads are cached rather than read again')

    def test_probe_is_exactly_four_bytes_and_has_its_own_read_cap(self):
        names = self.files({'abcdefgh': b'blat'})
        errors, reads, raw = self.inspect(names)
        self.assertTrue(any('probe residue' in error['reason'] for error in errors))
        self.assertEqual(raw, [])
        self.assertIn(('abcdefgh', 4), reads)
        self.assertEqual(sum(name == 'abcdefgh' for name, _ in reads), 1)

    def test_oversized_markdown_and_probe_fail_before_any_payload(self):
        for name in ['large.md', 'abcdefgh']:
            with self.subTest(name=name):
                names = self.files({name: b'x' * (1024 * 1024 + 1)})
                errors, reads, raw = self.inspect(names)
                self.assertTrue(errors)
                self.assertEqual(reads + raw, [])
                self.assertFalse(any('probe residue' in error['reason'] for error in errors))

    def test_intermediate_link_or_junction_is_rejected_before_payload(self):
        outside = self.parent / 'outside'
        outside.mkdir()
        data = b'EXTERNAL SYNTHETIC PAYLOAD'
        (outside / 'secret.md').write_bytes(data)
        gate = self.root / 'gate'
        self.directory_link(gate, outside)
        (self.root / 'SHA256SUMS').write_bytes((hashlib.sha256(data).hexdigest() + '  gate/secret.md\n').encode())
        names = ['SHA256SUMS', 'gate/secret.md']
        for manifest in [False, True]:
            with self.subTest(manifest=manifest):
                errors, reads, raw = self.inspect(names, manifest=manifest)
                self.assertTrue(errors)
                self.assertEqual(reads + raw, [], 'Unsafe components must fail before the manifest or external payload is read')
                self.assertFalse(any(data in content for content in self.contents_read))

    def test_parent_swap_after_preflight_never_reads_external_content(self):
        data = b'EXTERNAL SYNTHETIC RACE PAYLOAD'
        outside = self.parent / 'outside'
        outside.mkdir()
        (outside / 'secret.md').write_bytes(data)
        names = self.files({'gate/secret.md': b'INSIDE'})
        gate = self.root / 'gate'
        def swap():
            gate.rename(self.root / 'parked')
            self.directory_link(gate, outside)
        errors, reads, raw = self.inspect(names, before_read=swap)
        self.assertTrue(errors)
        self.assertEqual(raw, [])
        self.assertFalse(any(data in content for content in self.contents_read))

    def test_selected_path_aliases_do_not_bypass_preflight(self):
        for name in ['./private/secret.md', 'docs/../guide.md', './.git/config']:
            with self.subTest(name=name):
                names = self.files({name: b'SYNTHETIC alias content'})
                errors, reads, raw = self.inspect(names, manifest=True)
                self.assertTrue(errors)
                self.assertEqual(reads + raw, [])

    def test_file_count_and_aggregate_preflight_are_bounded(self):
        names = self.files({'one.md': b'x' * 100, 'two.md': b'y' * 100})
        for overrides in [{'MAX_SELECTED_FILES': 2}, {'MAX_SCAN_BYTES': 250}]:
            with self.subTest(overrides=overrides):
                patches = [patch.object(hygiene_check, key, value, create=True) for key, value in overrides.items()]
                with patches[0]:
                    errors, reads, raw = self.inspect(names)
                self.assertTrue(errors)
                self.assertEqual(reads + raw, [])

    def test_exact_limits_and_normal_contained_links_are_positive_controls(self):
        names = self.files({'docs/readme.md': b'[guide](../guide.md)\n', 'guide.md': b'normal\n',
                            'abcdefgh': b'ordinary binary'})
        total = sum((self.root / name).stat().st_size for name in names)
        with patch.object(hygiene_check, 'MAX_SCAN_BYTES', total, create=True), \
                patch.object(hygiene_check, 'MAX_SELECTED_FILES', len(names), create=True):
            errors, reads, raw = self.inspect(names)
        self.assertEqual(errors, [])
        self.assertEqual(raw, [])
        self.assertEqual(len(reads), len(names))

    def test_exact_per_file_limit_is_accepted(self):
        names = self.files({'limit.bin': b'x' * (1024 * 1024)})
        errors, reads, raw = self.inspect(names)
        self.assertEqual(errors, [])
        self.assertEqual(raw, [])
        self.assertIn(('limit.bin', 1024 * 1024), reads)

    def test_growth_after_preflight_cannot_exceed_actual_scan_budget(self):
        names = self.files({'a.bin': b'aaaa', 'b.bin': b'bbbb'})
        grown = b'bbbbbbbbbbbb'
        manifest = (hashlib.sha256(b'aaaa').hexdigest() + '  a.bin\n' +
                    hashlib.sha256(grown).hexdigest() + '  b.bin\n').encode()
        (self.root / 'SHA256SUMS').write_bytes(manifest)
        limit = len(manifest) + 8
        def grow():
            (self.root / 'b.bin').write_bytes(grown)
        with patch.object(hygiene_check, 'MAX_SCAN_BYTES', limit, create=True):
            errors, reads, raw = self.inspect(names, before_read=grow)
        self.assertTrue(errors)
        self.assertEqual(raw, [])
        self.assertLessEqual(sum(map(len, self.contents_read)), limit)
        self.assertIn(('b.bin', 4), reads)

    def test_missing_and_nonregular_selections_fail_before_payload(self):
        names = self.files({'ok.md': b'allowed'})
        (self.root / 'directory').mkdir()
        cases = [names + ['missing.md'], names + ['directory'], names + ['ok.md']]
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.root / 'pipe')
            cases.append(names + ['pipe'])
        for selected in cases:
            with self.subTest(selected=selected):
                errors, reads, raw = self.inspect(selected)
                self.assertTrue(errors)
                self.assertEqual(reads + raw, [])

    def test_manifest_coverage_failure_does_not_dispatch_extra_private_entry(self):
        names = self.files({'ok.md': b'allowed'})
        extra = hashlib.sha256(b'never read').hexdigest() + '  private/unselected.md\n'
        with (self.root / 'SHA256SUMS').open('ab') as stream:
            stream.write(extra.encode())
        for manifest in [False, True]:
            with self.subTest(manifest=manifest):
                errors, reads, raw = self.inspect(names, manifest=manifest)
                self.assertTrue(errors)
                self.assertEqual(raw, [])
                self.assertEqual([name for name, _ in reads], ['SHA256SUMS'])

    def test_link_target_rechecks_metadata_without_reading_external_payload(self):
        external = self.parent / 'external.md'
        outside = b'EXTERNAL LINK TARGET CONTENT'
        external.write_bytes(outside)
        names = self.files({'README.md': b'[guide](guide.md)\n', 'guide.md': b'allowed'})
        def swap(name, data):
            if name == 'guide.md':
                (self.root / 'guide.md').rename(self.root / 'parked.md')
                (self.root / 'guide.md').symlink_to(external)
        errors, reads, raw = self.inspect(names, after_read=swap)
        self.assertTrue(errors)
        self.assertEqual(raw, [])
        self.assertFalse(any(outside in content for content in self.contents_read))


if __name__ == '__main__':
    unittest.main(verbosity=2)
