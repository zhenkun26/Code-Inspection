"""Distribution checks use synthetic paths only, without staging secret files."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import subprocess
import hashlib

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
