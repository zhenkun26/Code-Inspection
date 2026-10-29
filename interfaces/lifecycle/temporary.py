"""Bounded metadata-only crash residue inspection, never cleanup or recovery."""
from contextlib import contextmanager
import hashlib
import os
import re
import stat

from support.local_files import checked_root, read_bytes, require, root_lock

MAX_SCANNED_ENTRIES = 1024
MAX_REPORTED_ENTRIES = 64


@contextmanager
def _entries(root):
    if os.name == 'nt':
        # Root already holds no-reparse directory handles. Enumeration is an
        # observation by pathname, not an atomic snapshot against hostile writers.
        with os.scandir(os.fspath(root)) as entries:
            yield entries
    else:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with os.scandir(fd) as entries:
                yield entries
        finally:
            os.close(fd)


def report(host_root, policy_sha):
    """Pin raw policy and lock; do not parse state, read residue or infer ownership."""
    require(isinstance(policy_sha, str) and re.fullmatch('[0-9a-f]{64}', policy_sha),
            'SHA256 host policy pin required')
    root = checked_root(host_root)
    with root_lock(root) as selected:
        require(hashlib.sha256(read_bytes(selected, 'policy.json')).hexdigest() == policy_sha,
                'host policy pin mismatch')
        rows, reasons, scanned = [], [], 0
        with _entries(selected) as entries:
            for entry in entries:
                if scanned >= MAX_SCANNED_ENTRIES:
                    reasons.append('directory-entry-limit'); break
                scanned += 1
                if not entry.name.startswith('.lifecycle-'):
                    continue
                if len(rows) >= MAX_REPORTED_ENTRIES:
                    reasons.append('reported-entry-limit'); break
                row = {'name': entry.name, 'ownership': 'UNKNOWN', 'action': 'RETAIN',
                       'kind': 'UNKNOWN', 'size_bytes': None, 'link_count': None}
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        row['kind'] = 'link-or-reparse'
                    elif stat.S_ISREG(info.st_mode):
                        row['kind'] = 'hardlinked-regular' if info.st_nlink > 1 else 'regular'
                        # Windows DirEntry.stat reports zero when this field is unavailable.
                        row['link_count'] = info.st_nlink if info.st_nlink > 0 else None
                        row['size_bytes'] = info.st_size
                    elif stat.S_ISDIR(info.st_mode):
                        row['kind'] = 'directory'
                    else:
                        row['kind'] = 'special'
                except OSError:
                    reasons.append('entry-metadata-unavailable')
                rows.append(row)
        require(hashlib.sha256(read_bytes(selected, 'policy.json')).hexdigest() == policy_sha,
                'host policy changed during inspection')
    return {'schema': 'lifecycle-temp-report/v1', 'scope': 'temporary-file-metadata-only',
            'status': 'UNKNOWN' if rows or reasons else 'PASS',
            'policy_sha': policy_sha, 'lock_acquired': True,
            'complete': not reasons, 'atomic_snapshot': False,
            'scanned_entries': scanned, 'limits': {'scanned_entries': MAX_SCANNED_ENTRIES,
                                                  'reported_entries': MAX_REPORTED_ENTRIES},
            'entries': sorted(rows, key=lambda row: row['name']), 'reasons': reasons,
            'cleanup': 'NOT_RUN', 'ledger_recovery': 'NOT_RUN', 'authorizes_action': False}
