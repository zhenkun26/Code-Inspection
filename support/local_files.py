"""Bounded local evidence reads; standard library only, no commands/network.

The caller selects a trusted root. Refuse symlinks at/below that root, including
intermediate components. This is not a sandbox or proof of evidence authenticity.
"""
import hashlib
import json
import os
from pathlib import Path
import stat


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_root(root):
    if os.name == 'nt':
        from support.windows_files import Root
        with Root(root) as selected:
            return selected.path
    root = Path(root).absolute()
    require(not root.is_symlink(), 'root must not be a symbolic link')
    require(root.is_dir(), 'root must be an existing directory')
    return root.resolve(strict=True)


def read_bytes(root, relative, limit=1024 * 1024):
    if os.name == 'nt':
        from support.windows_files import Root
        require(type(limit) is int and limit >= 0, 'nonnegative read limit required')
        if isinstance(root, Root): return root.read(relative, limit)
        with Root(root) as selected: return selected.read(relative, limit)
    root = checked_root(root)
    path = Path(relative)
    require(not path.is_absolute() and path.parts and
            all(p not in {'.', '..'} for p in path.parts), 'relative bounded path required')
    require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'),
            'this platform cannot provide no-follow reads')
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in path.parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        leaf = os.open(path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(leaf)
        info = os.fstat(leaf)
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'not a bounded regular file')
        data = b''
        while len(data) <= limit:
            chunk = os.read(leaf, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        require(len(data) <= limit, 'file exceeds read limit')
        return data
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def text(root, relative):
    return read_bytes(root, relative).decode('utf-8')


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def decode_json(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))


def read_json(root, relative):
    return decode_json(read_bytes(root, relative))

def digest(data):
    return hashlib.sha256(data).hexdigest()


def is_link(path):
    """All Windows reparse tags, not only symbolic links; structural check only."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


from contextlib import contextmanager

@contextmanager
def root_lock(root):
    if os.name == 'nt':
        from support.windows_files import Root
        with Root(root) as selected, selected.locked():
            yield selected
    else:
        import fcntl
        fd = os.open(root/'lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        acquired = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); acquired = True
            yield root
        finally:
            try:
                if acquired: fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
