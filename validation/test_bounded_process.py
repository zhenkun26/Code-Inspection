"""Portable bounded-pipe controls; native Windows execution is a separate receipt."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class BoundedProcess(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('support.bounded_process')
        except ImportError:
            self.fail('portable bounded subprocess capability missing')
        self.processes = []
        self.existing = {thread.ident for thread in threading.enumerate()}
        original = subprocess.Popen

        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            self.processes.append(child)
            return child

        self.patcher = patch.object(self.api.subprocess, 'Popen', side_effect=launch)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.check_cleanup)

    def check_cleanup(self):
        for child in self.processes:
            self.assertIsNotNone(child.poll(), 'direct child must be reaped')
            self.assertTrue(child.stdout.closed)
            self.assertTrue(child.stderr.closed)
        self.assertEqual([thread.name for thread in threading.enumerate()
                          if thread.ident not in self.existing
                          and thread.name.startswith('bounded-process-')], [])

    def child(self, code, **options):
        options.setdefault('stdout_limit', 1024)
        options.setdefault('timeout', 5)
        return self.api.run([sys.executable, '-I', '-u', '-c', code], **options)

    def test_binary_stdout_stderr_exact_bounds_and_nonzero_exit(self):
        result = self.child("import os; os.write(1,b'\\x00\\xff\\r\\n'); os.write(2,b'err\\x00'); raise SystemExit(7)",
                            stdout_limit=4, stderr_limit=4)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b'\x00\xff\r\n')
        self.assertEqual(result.stderr, b'err\x00')

    def test_binary_file_stdin_is_caller_owned(self):
        with tempfile.TemporaryFile() as incoming:
            incoming.write(b'\x00\xff\r\ninput'); incoming.seek(0)
            result = self.child('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())', stdin=incoming)
            self.assertFalse(incoming.closed)
            self.assertEqual(result.stdout, b'\x00\xff\r\ninput')

    def test_default_stdin_is_eof(self):
        self.assertEqual(self.child('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())').stdout, b'')

    def test_zero_limits_accept_empty_output(self):
        result = self.child('pass', stdout_limit=0, stderr_limit=0)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))

    def test_stdout_overflow_kills_before_sleeping_child_finishes(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'output limit.*stdout'):
            self.child("import os,time; os.write(1,b'x'*4096); time.sleep(30)", stdout_limit=31)
        self.assertLess(time.monotonic()-started, 5)

    def test_stderr_overflow_kills_before_sleeping_child_finishes(self):
        with self.assertRaisesRegex(ValueError, 'output limit.*stderr'):
            self.child("import os,time; os.write(2,b'x'*16384); time.sleep(30)", stderr_limit=8192)

    def test_zero_limit_rejects_first_byte(self):
        with self.assertRaisesRegex(ValueError, 'output limit'):
            self.child("import os; os.write(1,b'x')", stdout_limit=0)

    def test_both_pipes_larger_than_pipe_capacity_are_drained(self):
        # A serial stdout-then-stderr reader deadlocks while the child is blocked
        # filling stderr and its stdout pipe remains open.
        size = 262144
        result = self.child("import os; os.write(2,b'e'*262144); os.write(1,b'o'*262144)",
                            stdout_limit=size, stderr_limit=size)
        self.assertEqual(result.stdout, b'o'*size)
        self.assertEqual(result.stderr, b'e'*size)

    def test_timeout_kills_reaps_and_joins_readers(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'timeout'):
            self.child('import time; time.sleep(30)', timeout=.2)
        self.assertLess(time.monotonic()-started, 4)

    def test_closed_pipes_do_not_hide_live_child_timeout(self):
        with self.assertRaisesRegex(ValueError, 'timeout'):
            self.child('import os,time; os.close(1); os.close(2); time.sleep(30)', timeout=.2)

    def test_absolute_capture_deadline_shortens_command_timeout(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'timeout'):
            self.child('import time; time.sleep(30)', timeout=10, deadline=started+.2)
        self.assertLess(time.monotonic()-started, 4)

    def test_expired_deadline_and_invalid_options_never_start_process(self):
        choices = ({'deadline':time.monotonic()-1}, {'deadline':float('nan')},
                   {'deadline':True}, {'timeout':float('inf')}, {'timeout':0},
                   {'timeout':True}, {'stdout_limit':-1}, {'stdout_limit':True},
                   {'stderr_limit':False})
        for options in choices:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.child('pass', **options)
        self.assertEqual(self.processes, [])

    def test_interruption_still_kills_reaps_and_joins(self):
        original = self.api.threading.Event.wait

        def interrupt(event, timeout=None):
            if threading.current_thread() is threading.main_thread() and timeout == .05:
                raise KeyboardInterrupt('injected interruption')
            return original(event, timeout)

        with patch.object(self.api.threading.Event, 'wait', interrupt):
            with self.assertRaisesRegex(KeyboardInterrupt, 'injected interruption'):
                self.child('import time; time.sleep(30)')

    def test_reader_failure_is_reported_and_child_is_reaped(self):
        original = self.api.os.read

        def fail_reader(fd, count):
            if threading.current_thread().name.startswith('bounded-process-stdout-'):
                raise OSError('injected pipe read failure')
            return original(fd, count)

        with patch.object(self.api.os, 'read', fail_reader):
            with self.assertRaisesRegex(OSError, 'injected pipe read failure'):
                self.child('import time; time.sleep(30)')

    def test_partial_reader_start_failure_cleans_up_started_reader(self):
        original = self.api.threading.Thread.start
        attempted = []

        def start(reader):
            attempted.append(reader.name)
            if len(attempted) == 2:
                raise RuntimeError('injected reader startup failure')
            return original(reader)

        with patch.object(self.api.threading.Thread, 'start', start):
            with self.assertRaisesRegex(RuntimeError, 'injected reader startup failure'):
                self.child('import time; time.sleep(30)')
        self.assertEqual(len(attempted), 2)

    def test_cleanup_failure_preserves_original_timeout(self):
        original = self.api._cleanup

        def cleanup(process, readers):
            original(process, readers)
            raise OSError('injected cleanup diagnostic')

        with patch.object(self.api, '_cleanup', cleanup):
            with self.assertRaisesRegex(ValueError, 'timeout') as raised:
                self.child('import time; time.sleep(30)', timeout=.05)
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertIn('injected cleanup diagnostic', str(raised.exception.__cause__))

    def test_repeated_success_and_timeout_do_not_accumulate_readers(self):
        for _ in range(5):
            self.assertEqual(self.child("import os; os.write(1,b'ok\\n')").stdout, b'ok\n')
            with self.assertRaisesRegex(ValueError, 'timeout'):
                self.child('import time; time.sleep(30)', timeout=.05)
            self.check_cleanup()

    @unittest.skipUnless(os.name == 'nt', 'N_A: native Windows process/pipe handle accounting')
    def test_windows_process_and_pipe_handles_are_released(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        current = kernel.GetCurrentProcess
        current.argtypes = []
        current.restype = wintypes.HANDLE
        count = kernel.GetProcessHandleCount
        count.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        count.restype = wintypes.BOOL

        def handles():
            result = wintypes.DWORD()
            if not count(current(), ctypes.byref(result)):
                raise ctypes.WinError(ctypes.get_last_error())
            return result.value

        self.child('pass')  # Warm runtime synchronization handles before counting.
        before = handles()
        for _ in range(5):
            self.child('pass')
            with self.assertRaisesRegex(ValueError, 'output limit'):
                self.child("import os,time; os.write(1,b'xx'); time.sleep(30)", stdout_limit=1)
            with self.assertRaisesRegex(ValueError, 'timeout'):
                self.child('import time; time.sleep(30)', timeout=.05)
        self.check_cleanup()
        # Popen retains its process handle until object destruction on Windows;
        # release the test's spy references before observing native ownership.
        self.processes.clear()
        import gc
        gc.collect()
        self.assertLessEqual(handles(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
