"""Pure protocol/guard tests. No Windows API acceptance is simulated here.

Native observations are NOT_RUN on this host; the experiment is never invoked
by this suite, including on Windows. Its authorization remains separate.
"""
import contextlib
import copy
import ctypes
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation import windows_temporary_qualification as q


def protocol_fixture():
    """Synthetic bytes for parser tests, never native execution evidence."""
    observations = []
    for label in q.EXPECTED_LABELS:
        value = dict(label=label, status='OBSERVED')
        native = (label.startswith('flush_') or label in {'directory_write_open', 'create', 'absence'}
                  or label.endswith(('_create', '_hold', '_blocked_open', '_retry_open', '_absence')))
        if native:
            negative = ('0xc0000043' if label.endswith('_blocked_open') else
                        '0xc0000034' if label == 'absence' or label.endswith('_absence') else None)
            read_flush = label.startswith('flush_read_')
            failed = negative is not None or read_flush
            value.update(ntstatus=negative or ('0xc0000022' if read_flush else '0x00000000'),
                         iosb_written=not failed, iosb_status=None if failed else '0x00000000',
                         iosb_information=None if failed else 0)
            if negative:
                value['status'] = 'EXPECTED_FAILURE'
        elif label == 'no_delete_disposition':
            value.update(status='EXPECTED_FAILURE', winerror=5)
        observations.append(value)
    return q.envelope(observations=observations, complete=True)


def encode(value):
    return json.dumps(value).encode('utf-8')


class QualificationProtocol(unittest.TestCase):
    def main_report(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = q.main(argv)
        return code, q.parse_report(output.getvalue().encode())

    def test_authorization_precedes_runtime_native_import_or_fixture(self):
        with patch.object(q, 'runtime_facts', side_effect=AssertionError('runtime before authorization')), \
             patch.object(q, 'NativeExperiment') as native, patch.object(q, 'temporary_directory') as fixture, \
             patch.object(q, 'supervise') as supervisor, patch.object(q, 'worker_entry') as worker:
            for argv in ([], ['--worker']):
                code, report = self.main_report(argv)
                self.assertEqual(code, 2)
                self.assertEqual(report['error'], 'UNAUTHORIZED')
                self.assertEqual(report['native_execution'], 'NOT_RUN')
                self.assertEqual(report['cleanup'], 'NOT_STARTED')
            native.assert_not_called(); fixture.assert_not_called()
            supervisor.assert_not_called(); worker.assert_not_called()

    def test_exact_target_gate(self):
        facts = dict(os_name='nt', implementation='CPython', python=(3, 13),
                     machine='amd64', pointer_bits=64, windows=(10, 0, 26100, 3))
        self.assertTrue(q.target_supported(facts))
        for field, wrong in (('os_name', 'posix'), ('implementation', 'PyPy'),
                             ('python', (3, 12)), ('python', (3, 14)), ('machine', 'arm64'),
                             ('pointer_bits', 32), ('windows', (10, 0, 26100, 1)),
                             ('windows', (10, 0, 20348, 3)), ('windows', None)):
            with self.subTest(field=field, value=wrong):
                self.assertFalse(q.target_supported(dict(facts, **{field: wrong})))

    def test_unsupported_host_never_creates_native_fixture(self):
        with patch.object(q, 'runtime_facts', return_value={'os_name': 'posix'}), \
             patch.object(q, 'NativeExperiment') as native, patch.object(q, 'temporary_directory') as fixture, \
             patch.object(q, 'supervise') as supervisor, patch.object(q, 'worker_entry') as worker:
            code, report = self.main_report(['--authorize-owned-fixture'])
            self.assertEqual(code, 2); self.assertEqual(report['error'], 'UNSUPPORTED_TARGET')
            self.assertEqual(report['native_execution'], 'NOT_RUN')
            native.assert_not_called(); fixture.assert_not_called()
            supervisor.assert_not_called(); worker.assert_not_called()

    def test_cli_has_no_selected_path_or_cleanup_arguments(self):
        for argv in (['--root', 'private'], ['--path', 'private'], ['--delete'], ['--timeout', '0']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    q.main(argv)

    def test_completed_observations_never_qualify_product_or_durability(self):
        report = q.parse_report(encode(protocol_fixture()))
        self.assertTrue(report['complete'])
        self.assertFalse(report['native_qualified'])
        self.assertEqual(report['durability'], 'UNKNOWN')
        self.assertEqual(report['product_support'], 'NOT_ENABLED')
        self.assertEqual(len(report['observations']), len(q.EXPECTED_LABELS))
        # A returned flush failure is an API observation, never a durability pass.
        self.assertEqual(report['observations'][2]['ntstatus'], '0xc0000022')

    def test_parser_rejects_claims_extensions_and_personal_data(self):
        for field, value in (('native_qualified', True), ('native_qualified', 0),
                             ('durability', 'PASS'), ('product_support', 'ENABLED'),
                             ('complete', 1), ('target', 'another target'),
                             ('path', '/private/example'), ('schema', 'other')):
            report = protocol_fixture(); report[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                q.parse_report(encode(report))
        for field in ('path', 'contents', 'identity', 'message'):
            report = protocol_fixture(); report['observations'][0][field] = 'private'
            with self.subTest(field=field), self.assertRaises(ValueError):
                q.parse_report(encode(report))

    def test_parser_requires_exact_complete_sequence(self):
        original = protocol_fixture()
        variants = [[], original['observations'][:-1], original['observations'] * 2,
                    original['observations'][::-1]]
        for observations in variants:
            report = copy.deepcopy(original); report['observations'] = observations
            with self.subTest(length=len(observations)), self.assertRaises(ValueError):
                q.parse_report(encode(report))
        report = copy.deepcopy(original); report['observations'][0]['label'] = 'unrecognized'
        with self.assertRaises(ValueError): q.parse_report(encode(report))

    def test_parser_requires_results_and_expected_negative_codes(self):
        for label, field, value in (
                ('directory_write_open', 'ntstatus', '0xc0000022'),
                ('directory_write_open', 'iosb_information', None),
                ('reader_blocked_open', 'ntstatus', '0xc0000001'),
                ('writer_absence', 'ntstatus', '0x00000000'),
                ('no_delete_disposition', 'winerror', 32),
                ('close_payload', 'status', 'ERROR'),
                ('flush_write_before', 'ntstatus', '0x00000103'),
                ('flush_write_before', 'iosb_status', None),
                ('flush_write_before', 'iosb_status', '0xc0000022'),
                ('flush_write_before', 'iosb_information', None),
                ('flush_read_before', 'iosb_information', 0),
                ('directory_write_open', 'iosb_written', False)):
            report = protocol_fixture()
            observation = next(o for o in report['observations'] if o['label'] == label)
            observation[field] = value
            with self.subTest(label=label, field=field), self.assertRaises(ValueError):
                q.parse_report(encode(report))
        report = protocol_fixture(); del report['observations'][0]['iosb_status']
        with self.assertRaises(ValueError): q.parse_report(encode(report))

    def test_unwritten_iosb_has_no_fabricated_zero_completion(self):
        experiment = q.NativeExperiment.__new__(q.NativeExperiment)
        raw = SimpleNamespace(StatusOrPointer=0xffffffff, Information=ctypes.c_size_t(-1).value)
        codes = experiment.io_codes(-1073741790, raw)
        self.assertFalse(codes['iosb_written'])
        self.assertIsNone(codes['iosb_status']); self.assertIsNone(codes['iosb_information'])
        raw.StatusOrPointer = None; raw.Information = 0
        self.assertEqual(experiment.io_codes(0, raw), dict(ntstatus='0x00000000',
                         iosb_written=True, iosb_status='0x00000000', iosb_information=0))

    def test_iosb_zero_pointer_is_completed_but_sentinel_or_error_is_not(self):
        # Actual ctypes representation, only the predicate used by both native
        # acquisition and flush; no native operation or success is simulated.
        class IoStatus(ctypes.Structure):
            _fields_ = [('StatusOrPointer', ctypes.c_void_p), ('Information', ctypes.c_size_t)]
        raw = IoStatus()
        self.assertIsNone(raw.StatusOrPointer)
        self.assertTrue(q.NativeExperiment.io_completed(raw))
        for status, information in ((0xffffffff, 0), (0xc0000022, 0),
                                    (0x103, 0), (None, ctypes.c_size_t(-1).value)):
            raw.StatusOrPointer, raw.Information = status, information
            self.assertFalse(q.NativeExperiment.io_completed(raw))

    def test_parser_bounds_and_json_strictness(self):
        for raw in (b'x' * (q.OUTPUT_LIMIT + 1), b'{', b'[]', b'\xff',
                    b'{"schema":1,"schema":2}', b'{"x":NaN}', 'not bytes'):
            with self.subTest(raw=str(raw)[:30]), self.assertRaises(ValueError):
                q.parse_report(raw)

    def test_supervisor_timeout_cancellation_and_failure_are_uncertain(self):
        for error, expected in ((ValueError('private timeout details'), 'WORKER_TIMEOUT_OR_OUTPUT_LIMIT'),
                                (KeyboardInterrupt(), 'CANCELLED'),
                                (OSError('private path details'), 'WORKER_FAILED')):
            with self.subTest(error=type(error).__name__), \
                 patch.object(q.bounded_process, 'run', side_effect=error):
                report, code = q.supervise()
            self.assertNotEqual(code, 0); self.assertEqual(report['error'], expected)
            self.assertFalse(report['complete']); self.assertEqual(report['cleanup'], 'UNKNOWN')
            self.assertEqual(report['native_execution'], 'INCOMPLETE')
            self.assertNotIn('private', json.dumps(report))

    def test_supervisor_uses_bounded_runner_and_preserves_worker_python_mode(self):
        for mode, flag in ((0, None), (1, '-O'), (2, '-OO')):
            command = q.worker_command(mode)
            self.assertEqual('-O' in command, flag == '-O')
            self.assertEqual('-OO' in command, flag == '-OO')
            self.assertIn('-B', command)
        with patch.object(q.bounded_process, 'run', return_value=subprocess.CompletedProcess(
                [], 0, encode(protocol_fixture()), b'')) as runner:
            report, code = q.supervise()
        self.assertEqual(code, 0); self.assertFalse(report['native_qualified'])
        call = runner.call_args
        self.assertEqual(call.args[0], q.worker_command(sys.flags.optimize))
        self.assertEqual(call.kwargs['timeout'], 60)
        self.assertEqual(call.kwargs['stdout_limit'], q.OUTPUT_LIMIT)
        self.assertEqual(call.kwargs['stderr_limit'], 2048)
        self.assertNotIn('shell', call.kwargs)
        self.assertEqual(call.kwargs['env']['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'], '1')

    def test_supervisor_rejects_invalid_partial_or_stderr_reports(self):
        failure = q.envelope('NATIVE_PROBE_FAILED', started=True)
        for code, stdout, stderr in ((0, b'broken', b''), (0, encode(failure), b''),
                                     (2, encode(protocol_fixture()), b''),
                                     (0, encode(protocol_fixture()), b'private path')):
            with self.subTest(code=code, stderr=bool(stderr)), patch.object(
                    q.bounded_process, 'run', return_value=subprocess.CompletedProcess([], code, stdout, stderr)):
                report, result = q.supervise()
            self.assertNotEqual(result, 0); self.assertFalse(report['complete'])
            self.assertEqual(report['cleanup'], 'UNKNOWN')
            self.assertNotIn('private', json.dumps(report))
        with patch.object(q.bounded_process, 'run', return_value=subprocess.CompletedProcess([], 2, encode(failure), b'')):
            report, result = q.supervise()
        self.assertEqual(report, failure); self.assertNotEqual(result, 0)
        with patch.object(q.bounded_process, 'run', return_value=subprocess.CompletedProcess([], 124, b'', b'')):
            report, result = q.supervise()
        self.assertEqual(report['error'], 'WORKER_DEADLINE')
        self.assertEqual(report['cleanup'], 'UNKNOWN'); self.assertNotEqual(result, 0)

    def test_direct_worker_has_its_own_deadline_and_cancels_watchdog(self):
        failure = (q.envelope('NATIVE_PROBE_FAILED', started=True), 2)
        with patch.object(q.threading, 'Timer') as timer, patch.object(q, 'run_native', return_value=failure):
            self.assertEqual(q.worker_entry(), failure)
        self.assertEqual(timer.call_args.args[0], 55)
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()
        timer.return_value.cancel.assert_called_once_with()
        with patch.object(q.os, '_exit') as terminate, patch('builtins.print') as output:
            timer.call_args.args[1]()
        terminate.assert_called_once_with(124)
        output.assert_not_called()

    def test_rename_buffer_uses_x64_offsets_and_numeric_handle(self):
        # Pure ABI serialization check; no handle is passed to any Windows API.
        handle = ctypes.c_void_p(0x1234); raw = 'fixed-name'.encode('utf-16-le')
        try:
            buffer = q.rename_buffer(handle, 'fixed-name')
        except (TypeError, struct.error):
            self.fail('rename helper must serialize the numeric ctypes HANDLE value')
        self.assertEqual(struct.unpack_from('<Q', buffer, 8)[0], 0x1234)
        self.assertEqual(struct.unpack_from('<I', buffer, 16)[0], len(raw))
        self.assertEqual(buffer[20:], raw)

    def test_retry_identity_mismatch_precedes_any_disposition(self):
        # Guard ordering only. This does not simulate successful native deletion.
        experiment = q.NativeExperiment.__new__(q.NativeExperiment)
        experiment.observations = []
        experiment.w = SimpleNamespace(check=Mock(return_value=SimpleNamespace(NumberOfLinks=1)))
        experiment.identity = Mock(return_value=b'replacement identity')
        experiment.set_info = Mock(return_value=True)
        with self.assertRaises(q.ProbeFailure):
            experiment.disposition(object(), 'reader_retry_disposition', b'created identity')
        experiment.set_info.assert_not_called()

    def test_failed_close_is_not_retried(self):
        experiment = q.NativeExperiment.__new__(q.NativeExperiment)
        handle = object(); experiment.handles = [(handle, 'create')]; experiment.observations = []
        experiment.w = SimpleNamespace(CloseHandle=Mock(return_value=False))
        with patch.object(q.ctypes, 'get_last_error', return_value=6, create=True):
            with self.assertRaises(q.ProbeFailure): experiment.close(handle, 'close_payload')
            experiment.close_remaining()
        experiment.w.CloseHandle.assert_called_once_with(handle)
        self.assertEqual(experiment.observations[-1]['status'], 'ERROR')

    def test_production_managed_windows_capability_remains_disabled(self):
        from interfaces.lifecycle import owned_temporary as owned
        with patch.object(owned.os, 'name', 'nt'), patch.object(owned.os, 'open') as opening:
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                owned.ManagedTemporaryNamespace('unused', '0' * 64, exclusive_namespace=True)
            opening.assert_not_called()

    def test_native_workflow_keeps_modes_bounded_and_failures_visible(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        steps = workflow.split('      - name: ')[1:]
        native = [step for step in steps if 'windows_temporary_qualification.py' in step]
        self.assertEqual(len(native), 2)
        for step, mode in zip(native, ['-B', '-O -B']):
            self.assertIn("if: ${{ !cancelled() && runner.os == 'Windows' }}", step)
            self.assertIn('timeout-minutes: 2\n', step)
            self.assertIn('run: python ' + mode + ' validation/windows_temporary_qualification.py --authorize-owned-fixture\n', step)
            self.assertNotIn('continue-on-error', step)
        self.assertLess(workflow.index('run: python -B validation/mutation_check.py'),
                        workflow.index(native[0]))
        self.assertIn('permissions:\n  contents: read\n', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertNotIn('pull_request_target:', workflow)


if __name__ == '__main__':
    unittest.main(verbosity=2)
