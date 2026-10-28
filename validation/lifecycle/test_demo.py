"""Exercise the documented CLI journey and refusal paths, with retained artifacts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.lifecycle_demo import run_demo
from validation.test_support import temporary_directory


class Demo(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)

    def test_requires_explicit_simulation_before_writes(self):
        output = self.parent / 'no-permission'
        with self.assertRaisesRegex(ValueError, 'simulate-approvals'):
            run_demo(output)
        self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        sentinel = self.parent / 'keep.txt'
        (sentinel).write_bytes(('original').encode('utf-8'))
        with self.assertRaisesRegex(ValueError, 'NEW output'):
            run_demo(self.parent, simulate_approvals=True)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'original')
        self.assertEqual(list(self.parent.iterdir()), [sentinel])

    def test_checkout_output_is_refused(self):
        output = ROOT / ('demo-refusal-' + self.parent.name)
        with self.assertRaisesRegex(ValueError, 'outside checkout'):
            run_demo(output, simulate_approvals=True)
        self.assertFalse(output.exists())

    def test_timeout_retains_partial_output_and_does_not_retry(self):
        output = self.parent / 'interrupted'
        actual_run = subprocess.run
        calls = []

        def injected(argv, **kwargs):
            if str(ROOT / 'interfaces/lifecycle/cli.py') in argv:
                calls.append(argv)
                raise subprocess.TimeoutExpired(argv, 20, output=b'partial output\n', stderr=b'partial error\n')
            return actual_run(argv, **kwargs)

        with patch('examples.lifecycle_demo.subprocess.run', side_effect=injected):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_demo(output, simulate_approvals=True)
        self.assertEqual(len(calls), 1)
        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['result'], 'FAIL')
        self.assertEqual(summary['steps'][0]['status'], 'UNKNOWN')
        log = json.loads((output / summary['steps'][0]['log']).read_text(encoding='utf-8'))
        self.assertEqual(log['stdout'], 'partial output\n')
        self.assertEqual(log['stderr'], 'partial error\n')
        self.assertIsNone(log['exit_code'])

    def journey(self, delivery):
        output = self.parent / 'demo'
        argv = [sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
                str(ROOT / 'examples/lifecycle_demo.py'), '--output', str(output), '--simulate-approvals']
        if delivery:
            argv.append('--with-delivery')
        result = subprocess.run(argv, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['result'], 'PASS')
        self.assertEqual(summary, json.loads((output / 'summary.json').read_text(encoding='utf-8')))
        self.assertGreater(summary['budget']['rounds'], 1)
        self.assertGreater(summary['budget']['operations'], 2)
        self.assertIn('SYNTHETIC', summary['review_and_approvals'])
        for source, sha in summary['source_hashes'].items():
            self.assertEqual(hashlib.sha256((ROOT / source).read_bytes()).hexdigest(), sha)
        pre = json.loads((output / 'pre-pr-record.json').read_text(encoding='utf-8'))
        note = (output / 'pre-pr-workspace/note.txt').read_bytes()
        self.assertEqual(note, b'self-owned candidate\n')
        self.assertEqual(pre['uncommitted_snapshot_manifest']['note.txt'], hashlib.sha256(note).hexdigest())
        logs = [json.loads((output / s['log']).read_text(encoding='utf-8')) for s in summary['steps']]
        self.assertTrue(all(x['exit_code'] == 0 for x in logs))
        records = [json.loads(x['stdout']) for x in logs]
        decisions = [r['recommendation'] for r in records if 'recommendation' in r]
        self.assertIn('HOLD', decisions)
        self.assertIn('READY_FOR_NEXT', decisions)
        self.assertTrue(any(r.get('result') == 'confirmed' and r.get('isolated_reference') for r in records))
        handoff = json.loads((output / 'handoff.json').read_text(encoding='utf-8'))
        self.assertEqual(handoff['publication'], 'NOT_RUN')
        self.assertEqual(handoff['state']['budget'], summary['budget'])
        state = handoff['state']
        if delivery:
            self.assertTrue(any(r.get('status') == 'UNKNOWN' for r in records))
            self.assertTrue(state['delivery']['plans'])
            self.assertEqual(summary['delivery'], 'LOCAL SIMULATION')
        else:
            self.assertEqual(state['delivery'], {})
            self.assertEqual(records[-2]['checks'][0]['status'], 'NOT_RUN')
            self.assertEqual(records[-2]['recommendation'], 'HOLD')

    def test_cli_journey_without_delivery(self):
        self.journey(False)

    def test_cli_journey_with_simulated_delivery(self):
        self.journey(True)


if __name__ == '__main__':
    unittest.main()
