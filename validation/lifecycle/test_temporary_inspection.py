"""Self-owned crash fixtures; inspection never adopts or removes residue."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import core
from validation.test_support import temporary_directory


class TemporaryInspection(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        self.host = Path(self.tmp.name) / 'host'
        self.host.mkdir()
        # Inspection binds raw host policy bytes; it never authorizes a lifecycle action.
        self.policy = b'{"purpose":"self-owned inspection fixture"}\n'
        (self.host / 'policy.json').write_bytes(self.policy)
        self.pin = hashlib.sha256(self.policy).hexdigest()
        self.original = b'{"budget":{"operations":7},"receipt":"preserve"}\n'
        (self.host / 'state.json').write_bytes(self.original)

    def command(self):
        return subprocess.run([sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
            str(ROOT / 'interfaces/lifecycle/cli.py'), '--host-root', str(self.host),
            '--policy-sha', self.pin, 'temp-report'], capture_output=True, text=True,
            timeout=15, cwd=ROOT)

    def test_hard_exit_residue_is_reported_without_adoption(self):
        # os._exit models the no-finally boundary, including SIGKILL/power loss.
        code = """import os,sys
from pathlib import Path
from interfaces.lifecycle import core
from support.local_files import root_lock
def stop(*args): os._exit(73)
core.os.replace=stop
with root_lock(Path(sys.argv[1])) as root:
    core.save(root,'state.json',{'uncommitted':'never adopt'})
"""
        killed = subprocess.run([sys.executable, '-B', '-c', code, str(self.host)],
                                cwd=ROOT, capture_output=True, timeout=15)
        self.assertEqual(killed.returncode, 73)
        residue = list(self.host.glob('.lifecycle-*'))
        self.assertEqual(len(residue), 1)
        before = residue[0].read_bytes()  # test-owned oracle, not production inspection
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'UNKNOWN')
        self.assertEqual(report['entries'][0]['ownership'], 'UNKNOWN')
        self.assertEqual(report['entries'][0]['action'], 'RETAIN')
        self.assertFalse(report['authorizes_action'])
        self.assertEqual(residue[0].read_bytes(), before)
        self.assertEqual((self.host / 'state.json').read_bytes(), self.original)

    def test_empty_report_is_not_a_lifecycle_decision(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['entries'], [])
        self.assertFalse(report['authorizes_action'])
        self.assertNotIn('recommendation', report)
        self.assertNotIn('budget_report', report)

    def test_ordinary_save_controls(self):
        core.save(self.host, 'state.json', {'committed': True})
        saved = (self.host / 'state.json').read_bytes()
        self.assertEqual(json.loads(saved), {'committed': True})
        with patch.object(core.os, 'replace', side_effect=PermissionError('fixture denied')):
            with self.assertRaises(PermissionError): core.save(self.host, 'state.json', {'lost': True})
        self.assertEqual((self.host / 'state.json').read_bytes(), saved)
        self.assertEqual(list(self.host.glob('.lifecycle-*')), [])

    def test_corrupt_or_missing_ledger_does_not_block_inspection(self):
        for content in (b'not-json\xff', None):
            if content is None: (self.host / 'state.json').unlink()
            else: (self.host / 'state.json').write_bytes(content)
            result = self.command()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['entries'], [])
            self.assertEqual((self.host / 'state.json').read_bytes() if content else None, content)

    def test_pin_and_lock_refuse_before_enumeration(self):
        from interfaces.lifecycle import temporary
        from support.local_files import root_lock
        with patch.object(temporary, '_entries') as scan:
            with self.assertRaises(ValueError): temporary.report(self.host, '0' * 64)
            scan.assert_not_called()
            with root_lock(self.host):
                with self.assertRaises((OSError, ValueError)): temporary.report(self.host, self.pin)
            scan.assert_not_called()
        self.assertEqual(temporary.report(self.host, self.pin)['entries'], [])

    def test_metadata_only_retains_every_kind_and_never_reads_residue(self):
        from interfaces.lifecycle import temporary
        path = self.host / '.lifecycle-123-old'
        path.write_bytes(b'not a ledger; private fixture payload')
        os.utime(path, (1, 1))
        (self.host / '.lifecycle-directory').mkdir()
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir(); target = outside / 'target'; target.write_bytes(b'untouched')
        os.link(path, self.host / '.lifecycle-hardlink')
        if os.name == 'nt':
            subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(self.host / '.lifecycle-junction'),
                            str(outside)], capture_output=True, check=True, timeout=10)
        else:
            (self.host / '.lifecycle-link').symlink_to(target)
            (self.host / '.lifecycle-dangling').symlink_to(outside / 'missing')
            os.mkfifo(self.host / '.lifecycle-fifo')
        authority = self.host / 'authority.json'; authority.write_bytes(b'unchanged authority')
        provider = self.host / 'sim-provider.json'; provider.write_bytes(b'unchanged provider')
        original_read = temporary.read_bytes
        reads = []
        def policy_only(root, name, *args, **kwargs):
            reads.append(name)
            self.assertEqual(name, 'policy.json', 'residue/ledger payload must not be read')
            return original_read(root, name, *args, **kwargs)
        with patch.object(temporary, 'read_bytes', side_effect=policy_only), \
             patch.object(os, 'unlink', side_effect=AssertionError('inspection must not delete')), \
             patch.object(os, 'replace', side_effect=AssertionError('inspection must not replace')), \
             patch.object(core, 'save', side_effect=AssertionError('inspection must not save')):
            report = temporary.report(self.host, self.pin)
        self.assertEqual(reads, ['policy.json', 'policy.json'])
        self.assertTrue(report['complete']); self.assertFalse(report['atomic_snapshot'])
        self.assertEqual(report['status'], 'UNKNOWN')
        self.assertEqual({r['ownership'] for r in report['entries']}, {'UNKNOWN'})
        self.assertEqual({r['action'] for r in report['entries']}, {'RETAIN'})
        self.assertIn('link-or-reparse', {r['kind'] for r in report['entries']})
        if os.name == 'nt':
            file_rows = [r for r in report['entries'] if r['size_bytes'] is not None]
            self.assertEqual({r['kind'] for r in file_rows}, {'regular'})
            self.assertEqual({r['link_count'] for r in file_rows}, {None})
        else: self.assertIn('hardlinked-regular', {r['kind'] for r in report['entries']})
        self.assertEqual((self.host / 'state.json').read_bytes(), self.original)
        self.assertEqual(authority.read_bytes(), b'unchanged authority')
        self.assertEqual(provider.read_bytes(), b'unchanged provider')
        self.assertEqual(target.read_bytes(), b'untouched')

    def fake_entries(self, entries):
        @contextmanager
        def selected(root): yield iter(entries)
        return selected

    def entry(self, name, failure=False, links=1):
        def metadata(*, follow_symlinks):
            self.assertIs(follow_symlinks, False, 'must not follow a residue link')
            if failure: raise FileNotFoundError('entry disappeared')
            return SimpleNamespace(st_mode=0o100600, st_nlink=links, st_size=11)
        return SimpleNamespace(name=name, stat=metadata)

    def test_enumeration_and_output_caps_are_incomplete(self):
        from interfaces.lifecycle import temporary
        for entries, reason, expected_count in (
            ([self.entry('unrelated') for _ in range(1024)] + [self.entry('.lifecycle-late')],
             'directory-entry-limit', 0),
            ([self.entry('.lifecycle-' + str(i)) for i in range(65)], 'reported-entry-limit', 64)):
            with patch.object(temporary, '_entries', self.fake_entries(entries)):
                report = temporary.report(self.host, self.pin)
            self.assertFalse(report['complete'])
            self.assertEqual(report['status'], 'UNKNOWN')
            self.assertIn(reason, report['reasons'])
            self.assertEqual(len(report['entries']), expected_count)
            self.assertLessEqual(report['scanned_entries'], 1024)

    def test_disappearing_entry_and_changed_policy_fail_closed(self):
        from interfaces.lifecycle import temporary
        entries = [self.entry('.lifecycle-vanished', failure=True)]
        with patch.object(temporary, '_entries', self.fake_entries(entries)):
            report = temporary.report(self.host, self.pin)
        self.assertFalse(report['complete'])
        self.assertEqual(report['entries'][0]['kind'], 'UNKNOWN')
        self.assertEqual(report['entries'][0]['action'], 'RETAIN')
        with patch.object(temporary, 'read_bytes', side_effect=[self.policy, b'changed policy']):
            with self.assertRaises(ValueError): temporary.report(self.host, self.pin)

    def test_each_entry_uses_nonfollowing_metadata(self):
        from interfaces.lifecycle import temporary
        with patch.object(temporary, '_entries', self.fake_entries([self.entry('.lifecycle-one')])):
            report = temporary.report(self.host, self.pin)
        self.assertEqual(report['entries'][0]['kind'], 'regular')

    def test_root_link_and_scan_error_refuse_without_payload(self):
        from interfaces.lifecycle import temporary
        alias = Path(self.tmp.name) / 'alias'
        if os.name == 'nt':
            subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(self.host)],
                           capture_output=True, check=True, timeout=10)
        else: alias.symlink_to(self.host, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)): temporary.report(alias, self.pin)
        with patch.object(temporary, '_entries', side_effect=OSError('fixture scan denied')):
            with self.assertRaises(OSError): temporary.report(self.host, self.pin)
        self.assertEqual((self.host / 'state.json').read_bytes(), self.original)

    def test_unknown_link_count_is_not_a_hardlink_claim(self):
        from interfaces.lifecycle import temporary
        with patch.object(temporary, '_entries', self.fake_entries([self.entry('.lifecycle-one', links=0)])):
            row = temporary.report(self.host, self.pin)['entries'][0]
        self.assertEqual(row['kind'], 'regular')
        self.assertIsNone(row['link_count'])
        self.assertEqual(row['ownership'], 'UNKNOWN')

    def test_cli_escapes_surrogate_names_without_losing_identity(self):
        # Synthetic metadata, not an attempted raw-byte filesystem-name operation.
        from interfaces.lifecycle import cli, temporary
        name = '.lifecycle-\udcff'
        stream = io.StringIO()
        with patch.object(temporary, '_entries', self.fake_entries([self.entry(name)])), \
             patch.object(sys, 'argv', ['cli', '--host-root', str(self.host), '--policy-sha', self.pin, 'temp-report']), \
             redirect_stdout(stream):
            result = cli.main()
        self.assertEqual(result, 0)
        try: wire = stream.getvalue().encode('utf-8', errors='strict')
        except UnicodeError: self.fail('report must be UTF-8-safe JSON, including surrogate-escaped names')
        self.assertEqual(json.loads(wire)['entries'][0]['name'], name)


if __name__ == '__main__': unittest.main()
