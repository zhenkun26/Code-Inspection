"""Opt-in native API observations, never managed-recovery qualification.

Only a fresh retained fixture is used. No caller-supplied paths, lifecycle
records, privilege changes or recursive cleanup are accepted. Successful API
calls do not establish directory-entry or power-loss durability.

References: https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_disposition_info
https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info
https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntflushbuffersfileex
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import struct
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from support import bounded_process
from support.local_files import decode_json
from validation.test_support import temporary_directory

SCHEMA = 'windows-temporary-api-observation/v1'
TARGET = 'WindowsServer2025-x64-CPython3.13-local-fixed-NTFS'
TIMEOUT_SECONDS = 60
WORKER_TIMEOUT_SECONDS = 55
OUTPUT_LIMIT = 32768
PAYLOAD = b'qualification fixture\n'
ERRORS = {'UNAUTHORIZED', 'UNSUPPORTED_TARGET', 'WORKER_TIMEOUT_OR_OUTPUT_LIMIT',
          'WORKER_FAILED', 'WORKER_INVALID_REPORT', 'WORKER_DEADLINE', 'CANCELLED', 'NATIVE_PROBE_FAILED'}
LABELS = {'directory_write_open', 'directory_identity', 'root_close', 'fixture_retained',
          'create', 'write', 'rename', 'disposition', 'close_payload', 'absence',
          'no_delete_create', 'no_delete_disposition', 'no_delete_close',
          'no_delete_retry_open', 'no_delete_retry_disposition', 'no_delete_retry_close',
          'no_delete_absence', 'close_directory_write', 'handle_release', 'native_failure'}
for _phase in ('before', 'after_create', 'after_rename', 'after_disposition'):
    LABELS.update('flush_' + access + '_' + _phase for access in ('read', 'write'))
for _kind in ('reader', 'writer'):
    LABELS.update(_kind + '_' + action for action in
                  ('create', 'created_close', 'hold', 'blocked_open', 'hold_close',
                   'retry_open', 'retry_disposition', 'retry_close', 'absence'))

EXPECTED_LABELS = ['directory_write_open', 'directory_identity']
for _phase, _actions in (
        ('before', ['create', 'write']), ('after_create', ['rename']),
        ('after_rename', ['disposition', 'close_payload', 'absence']),
        ('after_disposition', [])):
    EXPECTED_LABELS.extend('flush_' + access + '_' + _phase for access in ('read', 'write'))
    EXPECTED_LABELS.extend(_actions)
EXPECTED_LABELS.extend(['no_delete_create', 'no_delete_disposition', 'no_delete_close',
                        'no_delete_retry_open', 'no_delete_retry_disposition',
                        'no_delete_retry_close', 'no_delete_absence'])
for _kind in ('reader', 'writer'):
    EXPECTED_LABELS.extend(_kind + '_' + action for action in
                          ('create', 'created_close', 'hold', 'blocked_open', 'hold_close',
                           'retry_open', 'retry_disposition', 'retry_close', 'absence'))
EXPECTED_LABELS.extend(['close_directory_write', 'root_close', 'fixture_retained'])


def envelope(error=None, *, observations=None, complete=False, started=False):
    return dict(schema=SCHEMA, target=TARGET, native_qualified=False,
                product_support='NOT_ENABLED', durability='UNKNOWN', complete=complete,
                native_execution='OBSERVED' if complete else 'INCOMPLETE' if started else 'NOT_RUN',
                cleanup='RETAINED_HANDLES_CLOSED' if complete else 'UNKNOWN' if started else 'NOT_STARTED',
                observations=list(observations or []), error=error)


def runtime_facts():
    version = sys.getwindowsversion() if os.name == 'nt' else None
    return dict(os_name=os.name, implementation=platform.python_implementation(),
                python=sys.version_info[:2], machine=platform.machine().lower(),
                pointer_bits=ctypes.sizeof(ctypes.c_void_p) * 8,
                windows=None if version is None else
                (version.major, version.minor, version.build, version.product_type))


def target_supported(facts):
    # Server 2025 uses build 26100; product_type 3 excludes desktop Windows.
    return (facts.get('os_name') == 'nt' and facts.get('implementation') == 'CPython'
            and facts.get('python') == (3, 13) and facts.get('machine') in {'amd64', 'x86_64'}
            and facts.get('pointer_bits') == 64 and facts.get('windows') == (10, 0, 26100, 3))


def parse_report(raw):
    """Strict, bounded, code-only worker protocol; never relay worker stderr."""
    if type(raw) is not bytes or len(raw) > OUTPUT_LIMIT:
        raise ValueError('invalid report')
    report = decode_json(raw)
    expected = set(envelope('UNAUTHORIZED'))
    if (type(report) is not dict or set(report) != expected or report['schema'] != SCHEMA
            or report['target'] != TARGET or report['native_qualified'] is not False
            or report['product_support'] != 'NOT_ENABLED' or report['durability'] != 'UNKNOWN'
            or type(report['complete']) is not bool
            or (report['error'] is not None and (type(report['error']) is not str or report['error'] not in ERRORS))
            or type(report['native_execution']) is not str
            or report['native_execution'] not in {'NOT_RUN', 'INCOMPLETE', 'OBSERVED'}
            or type(report['cleanup']) is not str
            or report['cleanup'] not in {'NOT_STARTED', 'UNKNOWN', 'RETAINED_HANDLES_CLOSED'}
            or type(report['observations']) is not list or len(report['observations']) > 64):
        raise ValueError('invalid report')
    if report['complete']:
        if (report['error'] is not None or report['native_execution'] != 'OBSERVED'
                or report['cleanup'] != 'RETAINED_HANDLES_CLOSED' or not report['observations']):
            raise ValueError('invalid completed report')
    elif report['error'] is None or (report['native_execution'], report['cleanup']) not in {
            ('NOT_RUN', 'NOT_STARTED'), ('INCOMPLETE', 'UNKNOWN')}:
        raise ValueError('invalid incomplete report')
    for observation in report['observations']:
        if (type(observation) is not dict or not {'label', 'status'} <= set(observation)
                or set(observation) - {'label', 'status', 'ntstatus', 'iosb_status',
                                       'iosb_information', 'iosb_written', 'winerror', 'phase'}
                or type(observation['label']) is not str or observation['label'] not in LABELS
                or type(observation['status']) is not str
                or observation['status'] not in {'OBSERVED', 'EXPECTED_FAILURE', 'ERROR'}):
            raise ValueError('invalid observation')
        for key in ('ntstatus', 'iosb_status'):
            if key in observation and not (key == 'iosb_status' and observation[key] is None) and (type(observation[key]) is not str
                                      or re.fullmatch('0x[0-9a-f]{8}', observation[key]) is None):
                raise ValueError('invalid status code')
        for key in ('iosb_information', 'winerror'):
            if key in observation and not (key == 'iosb_information' and observation[key] is None) and (type(observation[key]) is not int
                                      or not 0 <= observation[key] <= 0xffffffff):
                raise ValueError('invalid numeric code')
        if ('iosb_written' in observation and type(observation['iosb_written']) is not bool
                or 'phase' in observation and (type(observation['phase']) is not str or observation['phase'] not in LABELS)):
            raise ValueError('invalid observation metadata')
    if report['complete'] and ([o['label'] for o in report['observations']] != EXPECTED_LABELS
                               or any(o['status'] == 'ERROR' for o in report['observations'])):
        raise ValueError('incomplete observation sequence')
    if report['complete']:
        for observation in report['observations']:
            label = observation['label']
            native = (label.startswith('flush_') or label in {'directory_write_open', 'create', 'absence'}
                      or label.endswith(('_create', '_hold', '_blocked_open', '_retry_open', '_absence')))
            expected = ('0xc0000043' if label.endswith('_blocked_open') else
                        '0xc0000034' if label == 'absence' or label.endswith('_absence') else None)
            if native:
                if set(observation) != {'label', 'status', 'ntstatus', 'iosb_status', 'iosb_information', 'iosb_written'}:
                    raise ValueError('missing native result')
                if observation['iosb_written'] != (observation['iosb_status'] is not None):
                    raise ValueError('inconsistent IOSB')
                if not observation['iosb_written'] and observation['iosb_information'] is not None:
                    raise ValueError('unwritten IOSB information')
                if expected:
                    if observation['ntstatus'] != expected or observation['status'] != 'EXPECTED_FAILURE':
                        raise ValueError('unexpected negative control')
                elif label.startswith('flush_'):
                    if observation['ntstatus'] == '0x00000103' or observation['status'] != 'OBSERVED':
                        raise ValueError('incomplete flush')
                    if observation['ntstatus'] == '0x00000000' and (
                            observation['iosb_status'] != '0x00000000'
                            or type(observation['iosb_information']) is not int):
                        raise ValueError('incomplete flush IOSB')
                elif (observation['ntstatus'] != '0x00000000' or observation['status'] != 'OBSERVED'
                      or observation['iosb_status'] != '0x00000000'
                      or type(observation['iosb_information']) is not int):
                    raise ValueError('unsuccessful native acquisition')
            elif label == 'no_delete_disposition':
                if observation != dict(label=label, status='EXPECTED_FAILURE', winerror=5):
                    raise ValueError('unexpected access control')
            elif observation != dict(label=label, status='OBSERVED'):
                raise ValueError('unexpected observation')
    return report


def worker_command(optimize):
    if type(optimize) is not int or optimize not in {0, 1, 2}:
        raise ValueError('unsupported Python mode')
    return [sys.executable, *(['-' + 'O' * optimize] if optimize else []), '-B', str(Path(__file__).resolve()),
               '--authorize-owned-fixture', '--worker']


def rename_buffer(parent, name):
    raw = name.encode('utf-16-le')
    value = parent.value if hasattr(parent, 'value') else parent
    # x64 FILE_RENAME_INFO is 24 bytes, with FileName starting at offset20.
    # Allocate sizeof(struct)+name bytes, retaining WCHAR[1]/tail padding and
    # a complete UTF-16 terminator inside the size supplied to the native API.
    return struct.pack('<I4xQI', 0, value, len(raw)) + raw + b'\0' * 4


def supervise():
    command = worker_command(sys.flags.optimize)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               CODE_INSPECTION_KEEP_TEST_ARTIFACTS='1')
    try:
        child = bounded_process.run(command, env=env, stdout_limit=OUTPUT_LIMIT,
                                    stderr_limit=2048, timeout=TIMEOUT_SECONDS)
    except KeyboardInterrupt:
        return envelope('CANCELLED', started=True), 130
    except ValueError:
        return envelope('WORKER_TIMEOUT_OR_OUTPUT_LIMIT', started=True), 2
    except Exception:
        return envelope('WORKER_FAILED', started=True), 2
    if child.returncode == 124:
        return envelope('WORKER_DEADLINE', started=True), 2
    try:
        report = parse_report(child.stdout)
    except Exception:
        return envelope('WORKER_INVALID_REPORT', started=True), 2
    if child.stderr or (child.returncode == 0) != report['complete']:
        return envelope('WORKER_FAILED', started=True), 2
    return report, 0 if report['complete'] else 2


class ProbeFailure(Exception):
    pass


class NativeExperiment:
    """Private worker; every native name is fixed and below its fresh root."""
    def __init__(self):
        from support import windows_files as win
        self.w = win
        self.observations = []
        self.handles = []
        self.phase = 'directory_write_open'
        self.flush = win.bind(win.N, 'NtFlushBuffersFileEx', win.NTSTATUS, win.HANDLE,
                              win.ULONG, ctypes.c_void_p, win.ULONG, ctypes.POINTER(win.IoStatus))
        self.set_info = win.bind(win.K, 'SetFileInformationByHandle', win.w.BOOL,
                                 win.HANDLE, ctypes.c_int, ctypes.c_void_p, win.ULONG)
        self.write = win.bind(win.K, 'WriteFile', win.w.BOOL, win.HANDLE, ctypes.c_void_p,
                              win.ULONG, ctypes.POINTER(win.ULONG), ctypes.c_void_p)

    def record(self, label, status='OBSERVED', **codes):
        self.observations.append(dict(label=label, status=status, **codes))

    @staticmethod
    def hex_status(value):
        return '0x%08x' % (value & 0xffffffff)

    def io(self):
        result = self.w.IoStatus()
        result.StatusOrPointer = 0xffffffff  # Not a fabricated successful completion.
        result.Information = ctypes.c_size_t(-1).value
        return result

    def io_codes(self, result, io):
        written = io.StatusOrPointer != 0xffffffff
        return dict(ntstatus=self.hex_status(result), iosb_written=written,
                    iosb_status=self.hex_status(io.StatusOrPointer or 0) if written else None,
                    iosb_information=int(io.Information) if written and io.Information != ctypes.c_size_t(-1).value else None)

    @staticmethod
    def io_completed(io):
        # c_void_p returns None for a native zero STATUS_SUCCESS.
        return (io.StatusOrPointer != 0xffffffff and (io.StatusOrPointer or 0) == 0
                and io.Information != ctypes.c_size_t(-1).value)

    def nt_record(self, label, result, io, expected=0):
        self.record(label, 'OBSERVED' if result == 0 else 'EXPECTED_FAILURE' if
                    result & 0xffffffff == expected else 'ERROR',
                    **self.io_codes(result, io))

    def opened(self, parent, name, label, *, access, share=0, create=False,
               directory=False, expected=0):
        w = self.w
        self.phase = label
        w.component(name)
        raw = name.encode('utf-16-le'); buffer = ctypes.create_string_buffer(raw + b'\0\0')
        string = w.UnicodeString(len(raw), len(raw) + 2, ctypes.addressof(buffer))
        attrs = w.Attributes(ctypes.sizeof(w.Attributes), parent, ctypes.pointer(string),
                             w.OBJ_DONT_REPARSE, None, None)
        handle = w.HANDLE(); io = self.io()
        result = w.NtCreateFile(ctypes.byref(handle), access, ctypes.byref(attrs),
                                ctypes.byref(io), None, 0, share,
                                2 if create else 1, 0x20 | (1 if directory else 0x40), None, 0)
        self.nt_record(label, result, io, expected)
        if result != 0:
            if result & 0xffffffff == expected and not handle.value:
                return None
            raise ProbeFailure()
        self.handles.append((handle, label))
        if expected:
            raise ProbeFailure()  # Negative control unexpectedly acquired a handle.
        info = w.check(handle, directory)
        if not self.io_completed(io):
            raise ProbeFailure()
        if not directory and (info.NumberOfLinks != 1 or not 0 <= info.EndOfFile <= len(PAYLOAD)):
            raise ProbeFailure()
        return handle

    def close(self, handle, label):
        # Remove before attempting close. Never repeat a failed/uncertain close.
        self.phase = label
        self.handles = [(h, name) for h, name in self.handles if h is not handle]
        ok = self.w.CloseHandle(handle)
        self.record(label, 'OBSERVED' if ok else 'ERROR',
                    **({} if ok else {'winerror': ctypes.get_last_error()}))
        if not ok:
            raise ProbeFailure()

    def identity(self, handle):
        class FileId(ctypes.Structure):
            _fields_ = [('volume', ctypes.c_uint64), ('file', ctypes.c_ubyte * 16)]
        value = FileId()
        self.w.wincheck(self.w.GetInfo(handle, 18, ctypes.byref(value), ctypes.sizeof(value)))
        return bytes(value)

    def flush_directories(self, read, write, phase):
        for label, handle in (('read', read), ('write', write)):
            self.phase = 'flush_' + label + '_' + phase
            io = self.io(); result = self.flush(handle, 0, None, 0, ctypes.byref(io))
            # Failure is an observation, not a product success or persistence claim.
            self.record(self.phase, **self.io_codes(result, io))
            if result == 0x103 or result == 0 and not self.io_completed(io):
                raise ProbeFailure()

    def disposition(self, handle, label, expected_identity, *, denied=False):
        self.phase = label
        info = self.w.check(handle, False)
        if info.NumberOfLinks != 1 or self.identity(handle) != expected_identity:
            raise ProbeFailure()
        delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO uses one BOOLEAN byte.
        ok = self.set_info(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete))
        error = 0 if ok else ctypes.get_last_error()
        expected = not ok and denied and error == 5
        self.record(label, 'EXPECTED_FAILURE' if expected else 'OBSERVED' if ok else 'ERROR',
                    **({} if ok else {'winerror': error}))
        if denied != bool(expected) or (not denied and not ok):
            raise ProbeFailure()

    def absent(self, parent, name, label):
        self.opened(parent, name, label, access=self.w.FILE_READ, share=7, expected=0xc0000034)

    def run(self, root):
        w = self.w
        writable = self.opened(root.handles[-2], root.names[-1], 'directory_write_open',
                               access=w.DIR_ACCESS | 2, share=3, directory=True)
        self.phase = 'directory_identity'
        if self.identity(writable) != self.identity(root.handle):
            raise ProbeFailure()
        self.record('directory_identity')
        self.flush_directories(root.handle, writable, 'before')
        payload = self.opened(root.handle, 'rename-source', 'create',
                              access=w.FILE_READ | 2 | 0x10000, create=True)
        payload_identity = self.identity(payload)
        self.phase = 'write'
        count = w.ULONG(); data = ctypes.create_string_buffer(PAYLOAD)
        w.wincheck(self.write(payload, data, len(PAYLOAD), ctypes.byref(count), None))
        if count.value != len(PAYLOAD):
            raise ProbeFailure()
        self.record('write')
        self.flush_directories(root.handle, writable, 'after_create')
        self.phase = 'rename'
        raw = rename_buffer(root.handle, 'renamed-payload')
        rename = ctypes.create_string_buffer(raw, len(raw))
        ok = self.set_info(payload, 3, rename, len(raw))
        self.record('rename', 'OBSERVED' if ok else 'ERROR',
                    **({} if ok else {'winerror': ctypes.get_last_error()}))
        if not ok:
            raise ProbeFailure()
        self.flush_directories(root.handle, writable, 'after_rename')
        self.disposition(payload, 'disposition', payload_identity); self.close(payload, 'close_payload')
        self.absent(root.handle, 'renamed-payload', 'absence')
        self.flush_directories(root.handle, writable, 'after_disposition')
        no_delete = self.opened(root.handle, 'no-delete', 'no_delete_create',
                                access=w.FILE_READ | 2, create=True)
        no_delete_identity = self.identity(no_delete)
        self.disposition(no_delete, 'no_delete_disposition', no_delete_identity, denied=True)
        self.close(no_delete, 'no_delete_close')
        retry = self.opened(root.handle, 'no-delete', 'no_delete_retry_open', access=w.FILE_READ | 0x10000)
        self.disposition(retry, 'no_delete_retry_disposition', no_delete_identity); self.close(retry, 'no_delete_retry_close')
        self.absent(root.handle, 'no-delete', 'no_delete_absence')
        for kind, access, share in (('reader', w.FILE_READ, 1), ('writer', w.FILE_READ | 2, 3)):
            created = self.opened(root.handle, kind, kind + '_create', access=w.FILE_READ | 2, create=True)
            created_identity = self.identity(created)
            self.close(created, kind + '_created_close')
            holder = self.opened(root.handle, kind, kind + '_hold', access=access, share=share)
            if self.identity(holder) != created_identity:
                raise ProbeFailure()
            self.opened(root.handle, kind, kind + '_blocked_open', access=w.FILE_READ | 0x10000,
                        expected=0xc0000043)  # Sharing refusal: no deletion was attempted.
            self.close(holder, kind + '_hold_close')
            retry = self.opened(root.handle, kind, kind + '_retry_open', access=w.FILE_READ | 0x10000)
            self.disposition(retry, kind + '_retry_disposition', created_identity); self.close(retry, kind + '_retry_close')
            self.absent(root.handle, kind, kind + '_absence')
        self.close(writable, 'close_directory_write')

    def close_remaining(self):
        failures = False
        while self.handles:
            handle, _ = self.handles.pop()
            self.phase = 'handle_release'
            ok = self.w.CloseHandle(handle)
            self.record('handle_release', 'OBSERVED' if ok else 'ERROR',
                        **({} if ok else {'winerror': ctypes.get_last_error()}))
            if not ok:
                failures = True
        if failures:
            raise ProbeFailure()


def run_native():
    experiment = None
    try:
        experiment = NativeExperiment()
        # Hold the checked local NTFS temporary parent across fixture creation.
        base = os.environ.get('TMPDIR') or os.environ.get('TEMP')
        if not base:
            raise ProbeFailure()
        with experiment.w.Root(base, pin=True):
            # Always retain; no TemporaryDirectory finalizer may sweep uncertainty.
            os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = '1'
            fixture = temporary_directory()
            with experiment.w.Root(fixture.name, pin=True) as root:
                try:
                    experiment.run(root)
                except BaseException as primary:
                    phase = experiment.phase
                    try:
                        experiment.w.cleanup(experiment.close_remaining, primary)
                    finally:
                        experiment.phase = phase
                    raise
                else:
                    experiment.close_remaining()
                experiment.phase = 'root_close'
            experiment.record('root_close')
        experiment.record('fixture_retained')
        return envelope(observations=experiment.observations, complete=True), 0
    except KeyboardInterrupt:
        return envelope('CANCELLED', observations=experiment.observations if experiment else [], started=True), 130
    except Exception as error:
        if experiment is not None:
            code = getattr(error, 'winerror', None)
            experiment.record('native_failure', 'ERROR',
                              phase=experiment.phase,
                              **({'winerror': code} if type(code) is int and 0 <= code <= 0xffffffff else {}))
        return envelope('NATIVE_PROBE_FAILED', observations=experiment.observations if experiment else [], started=True), 2


def worker_entry():
    # Also bound accidental direct --worker use. Forced termination retains the
    # fresh fixture; neither finally nor known deletion is assumed to complete.
    def deadline():
        # No logging/flush may block the deadline. The supervisor maps this
        # exit code to an incomplete report with cleanup UNKNOWN.
        os._exit(124)
    timer = threading.Timer(WORKER_TIMEOUT_SECONDS, deadline)
    timer.daemon = True
    timer.start()
    try:
        return run_native()
    finally:
        timer.cancel()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='windows-temporary-qualification', description=__doc__)
    parser.add_argument('--authorize-owned-fixture', action='store_true',
                        help='authorize only this fresh retained native experiment fixture')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.authorize_owned_fixture:
        report, code = envelope('UNAUTHORIZED'), 2
    elif not target_supported(runtime_facts()):
        report, code = envelope('UNSUPPORTED_TARGET'), 2
    elif args.worker:
        report, code = worker_entry()
    else:
        report, code = supervise()
    print(json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
