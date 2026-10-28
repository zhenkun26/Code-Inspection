"""Keep verification artifacts outside the checkout, optionally retaining them.

Set CODE_INSPECTION_KEEP_TEST_ARTIFACTS=1 when cleanup is not authorized.
An unusable explicit system temporary directory fails instead of falling back
to the current working directory. No test logs overwrite historical evidence.
"""
import os
from pathlib import Path
import tempfile


class RetainedDirectory:
    def __init__(self, directory):
        self.name = directory

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        return False

    def cleanup(self):
        pass


def temporary_directory():
    base = Path(os.environ.get('TMPDIR') or os.environ.get('TEMP') or '/tmp').resolve(strict=True)
    checkout = Path(__file__).resolve().parents[1]
    if base == checkout or checkout in base.parents:
        raise ValueError('test temporary directory must be outside the checkout')
    options = dict(prefix='code-inspection-test-', dir=str(base))
    if os.environ.get('CODE_INSPECTION_KEEP_TEST_ARTIFACTS') == '1':
        return RetainedDirectory(tempfile.mkdtemp(**options))
    return tempfile.TemporaryDirectory(**options)
