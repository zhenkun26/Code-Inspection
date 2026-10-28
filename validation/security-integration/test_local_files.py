"""Meaningful file/JSON negative controls with otherwise valid security records."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from support.local_files import decode_json, read_bytes
from validation.test_support import temporary_directory
from test_interface import samples

CLI = ROOT / 'interfaces/security-tools/normalize_result.py'


class LocalFileSafetyTests(unittest.TestCase):
    def invoke(self, root, optimized=False):
        result = subprocess.run(
            [sys.executable, *(['-O'] if optimized else []), '-B', str(CLI),
             '--root', str(root), '--request', 'request.json', '--result', 'result.json'],
            capture_output=True, text=True, timeout=10)
        return result.returncode, json.loads(result.stdout)

    def fixture(self, root):
        request, result, raw = samples()
        (root / 'request.json').write_bytes((json.dumps(request)).encode('utf-8'))
        (root / 'result.json').write_bytes((json.dumps(result)).encode('utf-8'))
        (root / 'raw.txt').write_bytes(raw)
        return request, result, raw

    def test_duplicate_keys_in_otherwise_valid_records(self):
        for optimized in (False, True):
            for filename, key in [('request.json', 'schema'), ('result.json', 'schema')]:
                with self.subTest(optimized=optimized, file=filename), temporary_directory() as directory:
                    root = Path(directory)
                    self.fixture(root)
                    code, output = self.invoke(root, optimized)
                    self.assertEqual((code, output['status']), (0, 'PASS'))
                    path = root / filename
                    # A last-key-wins parser would accept this exact record and
                    # return PASS, so schema validation cannot mask this guard.
                    duplicate = '{' + json.dumps(key) + ':"ignored",' + path.read_text(encoding='utf-8')[1:]
                    self.assertEqual(json.loads(duplicate)['schema'],
                                     'security-request/v1' if filename == 'request.json' else 'security-result/v1')
                    (path).write_bytes((duplicate).encode('utf-8'))
                    code, output = self.invoke(root, optimized)
                    self.assertEqual((code, output['status']), (2, 'UNKNOWN'))
                    self.assertEqual(output['reasons'], ['duplicate JSON key'])

    def test_symlink_evidence_rejected_even_with_matching_digest(self):
        for optimized in (False, True):
            for kind in ['file', 'directory']:
                with self.subTest(optimized=optimized, kind=kind), temporary_directory() as directory:
                    parent = Path(directory)
                    root = parent / 'inputs'; root.mkdir()
                    _, result, raw = self.fixture(root)
                    self.assertEqual(self.invoke(root, optimized)[1]['status'], 'PASS')
                    outside = parent / 'outside'; outside.mkdir()
                    (outside / 'raw.txt').write_bytes(raw)
                    if kind == 'file':
                        (root / 'linked-raw.txt').symlink_to(outside / 'raw.txt')
                        path = 'linked-raw.txt'
                    else:
                        (root / 'linked').symlink_to(outside, target_is_directory=True)
                        path = 'linked/raw.txt'
                    result['raw_evidence'] = [{'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}]
                    (root / 'result.json').write_bytes((json.dumps(result)).encode('utf-8'))
                    code, output = self.invoke(root, optimized)
                    self.assertEqual((code, output['status']), (2, 'UNKNOWN'))
                    self.assertNotIn('raw evidence digest mismatch', output['reasons'])

    def test_raw_digest_mismatch_is_the_rejection_reason(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized), temporary_directory() as directory:
                root = Path(directory)
                self.fixture(root)
                self.assertEqual(self.invoke(root, optimized)[1]['status'], 'PASS')
                (root / 'raw.txt').write_bytes(b'Changed after evidence digest was recorded.\n')
                code, output = self.invoke(root, optimized)
                self.assertEqual((code, output['status']), (2, 'UNKNOWN'))
                self.assertEqual(output['reasons'], ['raw evidence digest mismatch'])

    def test_bounded_regular_file_reads(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root / 'exact.txt').write_bytes(b'a' * 32)
            self.assertEqual(read_bytes(root, 'exact.txt', limit=32), b'a' * 32)
            with self.assertRaisesRegex(ValueError, 'bounded regular file'):
                read_bytes(root, 'exact.txt', limit=31)
            for path in ['../outside', str(root / 'exact.txt')]:
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'relative bounded path'):
                    read_bytes(root, path)
            (root / 'directory').mkdir()
            with self.assertRaisesRegex(ValueError, 'bounded regular file'):
                read_bytes(root, 'directory')
            if hasattr(os, 'mkfifo'):
                os.mkfifo(root / 'fifo')
                with self.assertRaisesRegex(ValueError, 'bounded regular file'):
                    read_bytes(root, 'fifo')

    def test_nested_duplicate_keys_and_nonfinite_json(self):
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            decode_json(b'{"subject":{"head":"old","head":"current"}}')
        for value in [b'NaN', b'Infinity', b'-Infinity']:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'non-finite JSON'):
                decode_json(b'{"value":' + value + b'}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
