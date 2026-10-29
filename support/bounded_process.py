"""Bounded binary pipes for trusted direct subprocesses, using stdlib threads.

Deadlines cover the direct child and pipe drain, followed by a bounded cleanup
attempt. This is not a process-tree terminator or a sandbox. Callers must not
use commands that leave descendants holding the pipe handles open.
"""
import math
import os
import subprocess
import sys
import threading
import time


def _require(value, message):
    if not value:
        raise ValueError(message)


def _cleanup(process, readers):
    """Reap before releasing pipes; report incomplete cleanup without waiting forever."""
    failures = []
    until = time.monotonic() + 2
    try:
        if process.poll() is None:
            process.kill()
    except BaseException as exc:
        failures.append(exc)
    try:
        process.wait(timeout=max(.001, until - time.monotonic()))
    except BaseException as exc:
        failures.append(exc)
    for reader in readers:
        try:
            reader.join(timeout=max(0, until - time.monotonic()))
        except BaseException as exc:
            failures.append(exc)
    for pipe in (process.stdout, process.stderr):
        try:
            pipe.close()
        except BaseException as exc:
            failures.append(exc)
    if any(reader.is_alive() for reader in readers):
        failures.append(OSError('UNKNOWN: subprocess reader cleanup incomplete'))
    if failures:
        raise failures[0]


def run(argv, *, stdin=None, env=None, stdout_limit, stderr_limit=8192,
        timeout=10, deadline=None):
    """Return CompletedProcess with bounded byte stdout/stderr, without shell use.

    stdin is an already prepared binary file handle and remains caller-owned;
    absent stdin is DEVNULL. deadline, when supplied, is an absolute monotonic
    time and can shorten the per-command timeout. Nonzero exit codes are left
    to the caller. An overflow or deadline produces no successful partial result.
    """
    for value in (stdout_limit, stderr_limit):
        _require(type(value) is int and value >= 0, 'nonnegative subprocess output limit required')
    _require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
             'finite positive subprocess timeout required')
    started = time.monotonic()
    until = started + timeout
    if deadline is not None:
        _require(type(deadline) in (int, float) and math.isfinite(deadline),
                 'finite subprocess deadline required')
        until = min(until, deadline)
    _require(started < until, 'UNKNOWN: subprocess timeout')
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL if stdin is None else stdin,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, bufsize=0, close_fds=True)
    buffers = [bytearray(), bytearray()]
    failures = []
    readers = []
    finished = threading.Event()
    guard = threading.Lock()
    completed = 0

    def drain(pipe, output, limit, name):
        nonlocal completed
        try:
            while True:
                allowance = limit - len(output)
                chunk = os.read(pipe.fileno(), min(65536, allowance + 1))
                if not chunk:
                    break
                _require(len(chunk) <= allowance, 'UNKNOWN: subprocess output limit (' + name + ')')
                output.extend(chunk)
        except BaseException as exc:
            with guard:
                failures.append(exc)
            finished.set()
        finally:
            with guard:
                completed += 1
                if completed == 2:
                    finished.set()

    try:
        for pipe, output, limit, name in ((process.stdout, buffers[0], stdout_limit, 'stdout'),
                                          (process.stderr, buffers[1], stderr_limit, 'stderr')):
            reader = threading.Thread(target=drain, args=(pipe, output, limit, name),
                                      name='bounded-process-' + name + '-' + str(process.pid),
                                      daemon=True)
            reader.start()
            readers.append(reader)
        while True:
            with guard:
                failure = failures[0] if failures else None
                done = completed == 2
            if failure is not None:
                raise failure
            _require(time.monotonic() < until, 'UNKNOWN: subprocess timeout')
            if done:
                break
            finished.wait(timeout=min(.05, max(0, until - time.monotonic())))
        try:
            code = process.wait(timeout=max(.001, until - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ValueError('UNKNOWN: subprocess timeout') from exc
        return subprocess.CompletedProcess(argv, code, bytes(buffers[0]), bytes(buffers[1]))
    finally:
        primary = sys.exc_info()[1]
        try:
            _cleanup(process, readers)
        except BaseException as exc:
            if primary is not None:
                raise primary from exc
            raise
