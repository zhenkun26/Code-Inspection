"""Bounded stderr diagnostics for single-threaded, scoped fixture use only.

Observe the one real merge preflight; never replay it or inspect fixture payloads.
The cached version binds listed files on disk at observer start, not loaded code
or edits made during its lifetime. Missing or oversized sources remain unknown.
"""
from concurrent.futures import CancelledError
from contextlib import ExitStack
from datetime import datetime, timezone
import errno
import hashlib
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from unittest.mock import patch

from interfaces.lifecycle import artifacts, core, merge, review

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'MERGE_PREFLIGHT_DIAGNOSTIC '
MAX_EVENTS = 64
MAX_ITEMS = 16
MAX_BYTES = 8192  # Entire UTF-8 line, including prefix and newline.
MAX_SOURCE_BYTES = 256 * 1024
SOURCE_FILES = {
    'merge': 'interfaces/lifecycle/merge.py',
    'review': 'interfaces/lifecycle/review.py',
    'core': 'interfaces/lifecycle/core.py',
    'artifacts': 'interfaces/lifecycle/artifacts.py',
    'provenance': 'interfaces/lifecycle/provenance.py',
    'source_content': 'interfaces/lifecycle/source_content.py',
    'observer': 'validation/lifecycle/preflight_diagnostics.py',
    'persistence_tests': 'validation/lifecycle/test_persistence_contract.py',
}
STATUSES = {'PASS', 'FAIL', 'NOT_RUN', 'BLOCKED', 'UNKNOWN', 'N_A'}
RECOMMENDATIONS = {'READY_FOR_NEXT', 'HOLD', 'REPLAN'}
CHECK_IDS = {'ci', 'semantic', 'security', 'integration', 'source-content',
             'artifact-content', 'build-provenance'}
ACTIONS = {'merge-approve', 'pr-approve', 'merge-execute'}
REASONS = {
    'PR gate no longer satisfied': 'PR_GATE',
    'target drift': 'TARGET_DRIFT',
    'candidate stale': 'CANDIDATE_STALE',
    'source content relation changed': 'SOURCE_RELATION',
    'artifact content unavailable or inconsistent': 'ARTIFACT_CONTENT',
    'build provenance unavailable or rejected': 'BUILD_PROVENANCE',
    'merge execution authority missing': 'EXECUTION_AUTHORITY',
    'operation budget exhausted': 'OPERATION_BUDGET',
    'source content unavailable or inconsistent': 'SOURCE_CONTENT',
    'current subject review round not registered': 'ROUND_UNREGISTERED',
    'valid human decision missing': 'HUMAN_DECISION',
    'high-risk expert coverage missing': 'EXPERT_COVERAGE',
    'legacy na_reason requires explicit host reattestation': 'LEGACY_APPLICABILITY',
}
EXCEPTION_TYPES = {kind: kind.__name__ for kind in (
    ValueError, TypeError, KeyError, RuntimeError, AssertionError, OSError,
    PermissionError, FileNotFoundError, NotADirectoryError, IsADirectoryError,
    TimeoutError, CancelledError, KeyboardInterrupt, SystemExit,
    subprocess.TimeoutExpired)}
ARTIFACT_ERRORS = {
    'artifact changed during snapshot read': 'SNAPSHOT_CHANGED',
    'UNKNOWN: artifact snapshot bytes mismatch': 'SNAPSHOT_MISMATCH',
    'not a bounded regular file': 'READ_BOUNDARY',
    'file exceeds read limit': 'READ_BOUNDARY',
    'artifact hardlinks refused': 'READ_BOUNDARY',
}
UNAVAILABLE = PREFIX + ('{"schema":"merge-preflight-diagnostic/v1",'
                        '"source_version":null,"outcome":"UNAVAILABLE",'
                        '"complete":false,"truncated":false,"unavailable":true}\n')


def source_version(root):
    files = {}
    for label, relative in SOURCE_FILES.items():
        value = None
        try:
            with (root / relative).open('rb') as stream:
                data = stream.read(MAX_SOURCE_BYTES + 1)
            if len(data) <= MAX_SOURCE_BYTES:
                value = hashlib.sha256(data).hexdigest()
        except BaseException:
            pass  # Diagnostics must not interrupt the fixture, even on failure.
        files[label] = value
    return {'scope': 'disk-at-observer-start', 'loaded_code_attested': False,
            'complete': all(value is not None for value in files.values()),
            'files': files}


class _Trace:
    def __init__(self, version):
        self.record = {'schema': 'merge-preflight-diagnostic/v1',
                       'source_version': version, 'plan_id': None, 'policy_id': None,
                       'outcome': 'UNAVAILABLE', 'report': None, 'events': [],
                       'complete': bool(version and version['complete']),
                       'truncated': False, 'unavailable': False}

    def safe(self, action):
        try:
            action()
        except BaseException:
            self.record.update(complete=False, unavailable=True)

    def choice(self, value, allowed):
        if type(value) is str and len(value) <= 160 and value in allowed:
            return value
        self.record['complete'] = False
        return 'REDACTED'

    def hex_id(self, value, width):
        if type(value) is str and len(value) == width and re.fullmatch('[0-9a-f]+', value):
            return value
        self.record['complete'] = False
        return None

    def reason(self, value):
        if type(value) is str and value.startswith('unresolved finding '):
            return 'UNRESOLVED_FINDING'
        selected = self.choice(value, REASONS)
        return REASONS.get(selected, 'REDACTED')

    def items(self, value):
        if type(value) is not list:
            self.record['complete'] = False
            return []
        if len(value) > MAX_ITEMS:
            self.record.update(complete=False, truncated=True)
        return value[:MAX_ITEMS]

    def report(self, value):
        if type(value) is not dict:
            self.record['complete'] = False
            return None
        checks = []
        for check in self.items(value.get('checks')):
            if type(check) is not dict:
                self.record['complete'] = False
                continue
            selected = {'id': self.choice(check.get('id'), CHECK_IDS),
                        'status': self.choice(check.get('status'), STATUSES)}
            if 'reason' in check:
                selected['reason'] = self.reason(check['reason'])
            checks.append(selected)
        return {'recommendation': self.choice(value.get('recommendation'), RECOMMENDATIONS),
                'checks': checks, 'reasons': [self.reason(r) for r in self.items(value.get('reasons'))]}

    def exception(self, error):
        kind = type(error)
        name = EXCEPTION_TYPES.get(kind, 'REDACTED')
        if name == 'REDACTED':
            self.record['complete'] = False
        number = getattr(error, 'errno', None) if kind in EXCEPTION_TYPES else None
        if type(number) is not int or not 0 <= number <= 65535:
            number = None
        category = ('PERMISSION' if number in {errno.EACCES, errno.EPERM} else
                    'MISSING' if number in {errno.ENOENT, errno.ENOTDIR} else
                    'TIMEOUT' if kind in {TimeoutError, subprocess.TimeoutExpired} else
                    'CANCELLED' if kind in {CancelledError, KeyboardInterrupt, SystemExit} else
                    'IO' if kind in {OSError, PermissionError, FileNotFoundError,
                                     NotADirectoryError, IsADirectoryError} else 'REDACTED')
        if kind is ValueError:
            message = error.args[0] if error.args else None
            category = (ARTIFACT_ERRORS.get(message, 'VALIDATION_REJECTION')
                        if type(message) is str and len(message) <= 160 else 'VALIDATION_REJECTION')
        return {'type': name, 'errno': number, 'category': category}

    def event(self, kind, args, kwargs, result=None, error=None):
        if len(self.record['events']) >= MAX_EVENTS:
            self.record.update(complete=False, truncated=True)
            return
        event = {'kind': kind, 'sequence': len(self.record['events']) + 1,
                 'monotonic_ns': time.monotonic_ns()}
        if kind == 'evaluate':
            stage = args[1] if len(args) > 1 else kwargs.get('stage', 'pr')
            event['stage'] = self.choice(stage, {'pr', 'merge'})
        elif kind == 'authorized':
            event['action'] = self.choice(args[1] if len(args) > 1 else kwargs.get('action'), ACTIONS)
        if error is not None:
            event['exception'] = self.exception(error)
        elif kind in {'review-clock', 'core-clock'}:
            if type(result) is datetime and (result.tzinfo is None or type(result.tzinfo) is timezone):
                event['at'] = result.isoformat(timespec='microseconds')
            else:
                event['at'] = None
                self.record['complete'] = False
        elif kind == 'evaluate':
            event['report'] = self.report(result)
        elif kind == 'target':
            event['oid'] = self.hex_id(result, 40)
        elif kind == 'authorized':
            event['allowed'] = result if type(result) is bool else None
            if type(result) is not bool:
                self.record['complete'] = False
        elif kind == 'artifact-fresh':
            event['result'] = 'NONE' if result is None else 'RETURNED'
        self.record['events'].append(event)


def _observed(trace, kind, original):
    def call(*args, **kwargs):
        try:
            result = original(*args, **kwargs)
        except BaseException as error:
            trace.safe(lambda: trace.event(kind, args, kwargs, error=error))
            raise
        trace.safe(lambda: trace.event(kind, args, kwargs, result=result))
        return result
    return call


def _method(descriptor):
    # Preserve descriptor binding, including an existing non-descriptor Mock.
    def call(instance, *args, **kwargs):
        bind = getattr(type(descriptor), '__get__', None)
        original = bind(descriptor, instance, type(instance)) if bind else descriptor
        return original(*args, **kwargs)
    return call


class PreflightObserver:
    """Use start/stop (addCleanup) or with; no concurrent or nested observers.

    The sink accepts a complete prefixed JSON line. Product clocks are observed,
    never advanced or frozen. Only diagnostic ordering uses a monotonic clock.
    """
    def __init__(self, sink=None, source_root=ROOT):
        self.sink = sink
        self.source_root = source_root
        self._patch = None

    def start(self):
        if self._patch is not None:
            raise RuntimeError('preflight observer already started')
        try:
            self.version = source_version(self.source_root)
        except BaseException:
            self.version = None
        self.original = merge._preflight
        self._patch = patch.object(merge, '_preflight', self._call)
        self._patch.start()
        return self

    def stop(self):
        if self._patch is not None:
            self._patch.stop()
            self._patch = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()

    def _emit(self, trace):
        try:
            record = trace.record if trace is not None else None
            if record is None:
                (self.sink or sys.stderr.write)(UNAVAILABLE)
                return
            payload = PREFIX + json.dumps(record, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n'
            if len(payload.encode('utf-8')) > MAX_BYTES:
                record.update(events=[], report=None, complete=False, truncated=True)
                payload = PREFIX + json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n'
            if len(payload.encode('utf-8')) > MAX_BYTES:
                payload = UNAVAILABLE
            (self.sink or sys.stderr.write)(payload)
        except BaseException:
            try:
                sys.stderr.write(UNAVAILABLE)
            except BaseException:
                pass

    def _call(self, *args, **kwargs):
        trace = None
        stack = ExitStack()
        try:
            try:
                trace = _Trace(self.version)
                session = args[0] if args else kwargs.get('s')
                plan = args[1] if len(args) > 1 else kwargs.get('plan')
                trace.record['policy_id'] = trace.hex_id(getattr(session, 'pin', None), 64)
                trace.record['plan_id'] = trace.hex_id(plan.get('id') if type(plan) is dict else None, 64)
                for owner, name, kind in (
                        (merge, 'evaluate', 'evaluate'), (review, 'now', 'review-clock'),
                        (core, 'now', 'core-clock'), (artifacts, 'fresh', 'artifact-fresh'),
                        (core.Session, 'authorized', 'authorized'), (merge.LocalGit, 'target', 'target')):
                    current = (inspect.getattr_static(owner, name) if isinstance(owner, type)
                               else getattr(owner, name))
                    original = _method(current) if isinstance(owner, type) else current
                    stack.enter_context(patch.object(owner, name, _observed(trace, kind, original)))
            except BaseException:
                try:
                    stack.close()
                except BaseException:
                    pass
                if trace is not None:
                    trace.record.update(complete=False, unavailable=True)
            try:
                result = self.original(*args, **kwargs)
            except BaseException as error:
                if trace is not None:
                    trace.record['outcome'] = 'ERROR'
                    trace.safe(lambda: trace.record.update(exception=trace.exception(error)))
                raise
            if trace is not None:
                trace.safe(lambda: trace.record.update(report=trace.report(result),
                    outcome=trace.choice(result.get('recommendation') if type(result) is dict else None,
                                         RECOMMENDATIONS)))
            return result
        finally:
            try:
                stack.close()
            except BaseException:
                if trace is not None:
                    trace.record.update(complete=False, unavailable=True)
            try:
                self._emit(trace)
            except BaseException:
                try:
                    sys.stderr.write(UNAVAILABLE)
                except BaseException:
                    pass
