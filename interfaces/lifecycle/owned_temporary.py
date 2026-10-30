"""Opt-in POSIX temporary ownership under a trusted, participating host namespace.

The host must exclude nonparticipating namespace writers for the entire context.
Descriptor checks do not make compare-and-unlink atomic against such writers.
Records are ownership evidence only: never ledger, publication, or action authority.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from support.local_files import decode_json, require

MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 4096
_TOKEN = object()


def _supported():
    require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'),
            'managed temporary recovery unsupported on this platform')


def _identity(info):
    return (info.st_dev, info.st_ino)


def _metadata(info):
    return dict(device=info.st_dev, inode=info.st_ino, size=info.st_size,
                mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def _flat(name):
    require(type(name) is str and bool(name) and name not in {'.', '..'}
            and not any(c in name for c in '/\\:\x00') and len(name.encode('utf-8')) <= 255,
            'flat state name required')


def _target(name):
    _flat(name)
    folded = name.casefold()
    require(not folded.startswith('.lifecycle-') and folded not in {'policy.json', 'lock'},
            'managed reserved target')
    require(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]*', name) is not None,
            'ASCII managed target basename required')


def _chain(path):
    """Open every literal ancestor without resolving user-selected symlinks."""
    descriptors = []
    try:
        descriptors.append(os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for name in path.parts[1:]:
            descriptors.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        return descriptors
    except BaseException as primary:
        _release(descriptors, primary=primary)
        raise


def _release(descriptors, *, unlock=None, primary=None):
    """Try every release; a teardown error must not replace an active failure."""
    cleanup = None
    if unlock is not None:
        try:
            import fcntl
            fcntl.flock(unlock, fcntl.LOCK_UN)
        except BaseException as error: cleanup = error
    for fd in reversed(descriptors):
        try: os.close(fd)
        except BaseException as error:
            if cleanup is None: cleanup = error
    if cleanup is not None:
        if primary is not None: raise primary from cleanup
        raise cleanup


class CleanupUnknown(OSError):
    """Deletion was attempted; callers must retain uncertainty after any failure."""
    status = 'UNKNOWN'


class ManagedTemporaryNamespace:
    """Trusted Python host grant; never construct from tool-supplied JSON.

    Use a canonical host-selected absolute path (e.g. /private/tmp on macOS).
    Symlink ancestors are refused rather than silently canonicalized.
    """
    def __init__(self, host_root, policy_sha, *, exclusive_namespace=False):
        _supported()
        require(exclusive_namespace is True,
                'explicit exclusively participating host namespace assurance required')
        require(type(policy_sha) is str and re.fullmatch('[0-9a-f]{64}', policy_sha),
                'SHA256 host policy pin required')
        path = Path(host_root)
        require(path.is_absolute() and '..' not in path.parts, 'absolute canonical host root required')
        self.path, self.pin, self._busy = path, policy_sha, False
        descriptors = _chain(path)
        try: self._identities = tuple(_identity(os.fstat(fd)) for fd in descriptors)
        finally: _release(descriptors, primary=sys.exc_info()[1])

    def matches(self, root, pin):
        return Path(root) == self.path and pin == self.pin

    @contextmanager
    def locked(self):
        _supported()
        require(not self._busy, 'managed namespace context already active')
        self._busy = True
        descriptors, lock, acquired, selected = [], None, False, None
        try:
            descriptors = _chain(self.path)
            require(tuple(_identity(os.fstat(fd)) for fd in descriptors) == self._identities,
                    'managed root or ancestor replaced')
            lock = os.open('lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=descriptors[-1])
            info = os.fstat(lock)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    'managed lock must be a single-link regular file')
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); acquired = True
            selected = ManagedRoot(self, descriptors, lock, _TOKEN)
            selected.validate()
            yield selected
        finally:
            if selected is not None: selected._active = False
            try:
                _release(descriptors + ([] if lock is None else [lock]),
                         unlock=lock if acquired else None, primary=sys.exc_info()[1])
            finally:
                self._busy = False


class ManagedRoot:
    """An active root minted only by ManagedTemporaryNamespace.locked()."""
    def __init__(self, namespace, descriptors, lock, token):
        require(token is _TOKEN, 'active managed namespace context required')
        self._namespace, self._descriptors, self._lock = namespace, descriptors, lock
        self._active = True

    def __fspath__(self): return os.fspath(self._namespace.path)
    def __truediv__(self, name): return self._namespace.path / name

    def _current(self):
        require(self._active and self._namespace._busy, 'managed context is no longer active')
        fds = self._descriptors
        require(tuple(_identity(os.fstat(fd)) for fd in fds) == self._namespace._identities,
                'managed root identity changed')
        for index, name in enumerate(self._namespace.path.parts[1:]):
            info = os.stat(name, dir_fd=fds[index], follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode) and _identity(info) == _identity(os.fstat(fds[index+1])),
                    'managed root or ancestor replaced')
        info = os.stat('lock', dir_fd=fds[-1], follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and _identity(info) == _identity(os.fstat(self._lock)), 'managed lock replaced or linked')
        return fds[-1]

    def _read(self, name, limit):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._current())
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and 0 <= before.st_size <= limit, 'not a bounded single-link regular file')
            data = b''
            while len(data) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(data)))
                if not chunk: break
                data += chunk
            require(len(data) <= limit and len(data) == before.st_size, 'managed read limit or size changed')
            require(_metadata(os.fstat(fd)) == _metadata(before) and os.fstat(fd).st_nlink == 1,
                    'managed file changed during read')
            self._same(name, before)
            return data, before
        finally: _release([fd], primary=sys.exc_info()[1])

    def _same(self, name, expected):
        info = os.stat(name, dir_fd=self._current(), follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and _metadata(info) == _metadata(expected), 'managed file replaced, linked or changed')

    def validate(self):
        data, _ = self._read('policy.json', MAX_PAYLOAD_BYTES)
        require(hashlib.sha256(data).hexdigest() == self._namespace.pin, 'host policy pin mismatch')

    def read(self, name):
        _flat(name); self.validate()
        return self._read(name, MAX_PAYLOAD_BYTES)[0]

    def _write_new(self, name, data):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._current())
        try:
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                require(written > 0, 'managed write made no progress')
                offset += written
            os.fsync(fd)
            return os.fstat(fd)
        finally: _release([fd], primary=sys.exc_info()[1])

    def _publish(self, source, target):
        fd = self._current()
        os.replace(source, target, src_dir_fd=fd, dst_dir_fd=fd)

    def _unlink(self, name):
        os.unlink(name, dir_fd=self._current())

    def _sync(self): os.fsync(self._current())

    def save(self, name, value):
        _target(name); self.validate()
        try:
            info = os.stat(name, dir_fd=self._current(), follow_symlinks=False)
        except FileNotFoundError: pass
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'managed state type refused')
            policy = os.stat('policy.json', dir_fd=self._current(), follow_symlinks=False)
            require(_identity(info) not in {_identity(policy), _identity(os.fstat(self._lock))},
                    'managed reserved target identity')
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b'\n'
        require(len(data) <= MAX_PAYLOAD_BYTES, 'ledger limit reached; archive under host control, do not reset')
        resource = uuid.uuid4().hex
        payload, record = '.lifecycle-managed-' + resource, '.lifecycle-owner-' + resource + '.json'
        info = self._write_new(payload, data)
        owner = dict(schema='lifecycle-owned-temporary/v1', policy_sha=self._namespace.pin,
                     root=dict(zip(('device', 'inode'), self._namespace._identities[-1])),
                     resource=resource, payload=payload, target=name, metadata=_metadata(info),
                     sha256=hashlib.sha256(data).hexdigest())
        raw = json.dumps(owner, sort_keys=True, separators=(',', ':')).encode() + b'\n'
        require(len(raw) <= MAX_RECORD_BYTES, 'ownership record limit')
        recorded = self._write_new(record, raw)
        self._sync()  # Registration is durable before publication is attempted.
        self.validate(); self._same(payload, info); self._same(record, recorded)
        self._publish(payload, name)
        self._sync()
        self._same(record, recorded); self._unlink(record); self._sync()

    def recover(self, record_name, *, authorize=False):
        """Explicit host grant for exactly this record; no scan, ledger load or adopt."""
        require(authorize is True, 'explicit host cleanup grant required')
        require(type(record_name) is str and re.fullmatch(r'\.lifecycle-owner-[0-9a-f]{32}\.json', record_name),
                'exact managed ownership record name required')
        self.validate()
        raw, record_info = self._read(record_name, MAX_RECORD_BYTES)
        owner = decode_json(raw)
        require(type(owner) is dict and set(owner) == {'schema', 'policy_sha', 'root', 'resource',
                'payload', 'target', 'metadata', 'sha256'}, 'ownership record fields')
        resource = record_name[len('.lifecycle-owner-'):-len('.json')]
        require(owner['schema'] == 'lifecycle-owned-temporary/v1'
                and owner['policy_sha'] == self._namespace.pin and owner['resource'] == resource
                and owner['payload'] == '.lifecycle-managed-' + resource, 'ownership record binding')
        require(type(owner['root']) is dict and set(owner['root']) == {'device', 'inode'}
                and all(type(v) is int for v in owner['root'].values())
                and owner['root'] == dict(zip(('device', 'inode'), self._namespace._identities[-1])),
                'ownership root mismatch')
        _target(owner['target'])
        metadata = owner['metadata']
        require(type(metadata) is dict and set(metadata) == {'device', 'inode', 'size', 'mtime_ns', 'ctime_ns'}
                and all(type(v) is int and v >= 0 for v in metadata.values())
                and metadata['size'] <= MAX_PAYLOAD_BYTES, 'ownership metadata invalid')
        require(type(owner['sha256']) is str and re.fullmatch('[0-9a-f]{64}', owner['sha256']),
                'ownership digest invalid')
        result = dict(schema='lifecycle-temp-cleanup/v1', record=record_name, status='UNKNOWN',
                      action='RETAIN', publication='UNKNOWN', authorizes_action=False)
        try: data, info = self._read(owner['payload'], MAX_PAYLOAD_BYTES)
        except FileNotFoundError: return result  # Never redirect to the intended target.
        require(_metadata(info) == metadata, 'owned payload identity or metadata mismatch')
        require(hashlib.sha256(data).hexdigest() == owner['sha256'], 'owned payload digest mismatch')
        self.validate(); self._same(record_name, record_info); self._same(owner['payload'], info)
        # Conditional on the host assurance: POSIX has no inode-conditional unlink.
        try:
            self._unlink(owner['payload']); self._sync()
            self._same(record_name, record_info); self._unlink(record_name); self._sync()
        except Exception as error:
            raise CleanupUnknown('UNKNOWN: temporary cleanup may be partially complete') from error
        result.update(status='PASS', action='REMOVED_OWNED_TEMPORARY')
        return result
