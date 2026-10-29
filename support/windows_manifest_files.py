"""Held-handle source observations on native Windows x64/local NTFS.

No atomic snapshot or hostile-writer guarantee. Names and bytes are bounded;
all traversal uses the existing no-reparse native backend. Handles pin the
selected root chain and traversed directories until capture completes.
"""
from contextlib import ExitStack
import ctypes as c
import platform
import stat
import struct

from support import windows_files as win


class BasicInfo(c.Structure):
    _fields_ = [('CreationTime', c.c_int64), ('LastAccessTime', c.c_int64),
                ('LastWriteTime', c.c_int64), ('ChangeTime', c.c_int64),
                ('Attributes', win.ULONG)]


class HandleInfo(c.Structure):
    _fields_ = [('Attributes', win.ULONG), ('CreationTime', win.w.FILETIME),
                ('LastAccessTime', win.w.FILETIME), ('LastWriteTime', win.w.FILETIME),
                ('VolumeSerialNumber', win.ULONG), ('FileSizeHigh', win.ULONG),
                ('FileSizeLow', win.ULONG), ('NumberOfLinks', win.ULONG),
                ('FileIndexHigh', win.ULONG), ('FileIndexLow', win.ULONG)]


HandleInformation = win.bind(win.K, 'GetFileInformationByHandle', win.w.BOOL,
                             win.HANDLE, c.POINTER(HandleInfo))


def _streams(handle):
    """Reject named streams without reading their payloads or growing buffers."""
    buffer = (c.c_uint64 * (65536 // 8))()
    if not win.GetInfo(handle, 7, c.byref(buffer), c.sizeof(buffer)):
        error = c.get_last_error()
        if error == 38: return  # ERROR_HANDLE_EOF: no streams (e.g. directories)
        raise c.WinError(error)
    raw = bytes(buffer)
    following, length = struct.unpack_from('<II', raw)
    win.need(following == 0 and length == len('::$DATA'.encode('utf-16-le'))
             and raw[24:24+length].decode('utf-16-le', 'strict') == '::$DATA',
             'UNKNOWN: source alternate data streams refused')


def _stamp(handle, directory):
    standard = win.check(handle, directory)
    basic = BasicInfo(); identity = HandleInfo()
    win.wincheck(win.GetInfo(handle, 0, c.byref(basic), c.sizeof(basic)))
    win.wincheck(HandleInformation(handle, c.byref(identity)))
    _streams(handle)
    win.need(standard.EndOfFile >= 0, 'UNKNOWN: invalid source size')
    # These synthetic bits support the shared kind/size preflight only. They
    # are never serialized as Windows permissions or executable-bit evidence.
    mode = (stat.S_IFDIR if directory else stat.S_IFREG) | (0o444 if basic.Attributes & 1 else 0o666)
    return (identity.VolumeSerialNumber,
            (identity.FileIndexHigh << 32) | identity.FileIndexLow,
            mode, standard.NumberOfLinks, standard.EndOfFile,
            basic.LastWriteTime, basic.ChangeTime, basic.Attributes, basic.CreationTime)


def _records(raw):
    """Parse FILE_ID_BOTH_DIR_INFO, with explicit x64 ABI offsets and bounds."""
    offset = 0
    while True:
        win.need(offset + 104 <= len(raw), 'UNKNOWN: truncated native directory record')
        following = struct.unpack_from('<I', raw, offset)[0]
        attrs, length = struct.unpack_from('<II', raw, offset + 56)
        file_id = struct.unpack_from('<Q', raw, offset + 96)[0]
        end = offset + following if following else len(raw)
        win.need(not following or (following >= 104 and following % 8 == 0 and end <= len(raw)),
                 'UNKNOWN: malformed native directory offset')
        win.need(length > 0 and length % 2 == 0 and length <= 510
                 and offset + 104 + length <= end, 'UNKNOWN: malformed native directory name')
        name = raw[offset + 104:offset + 104 + length].decode('utf-16-le', 'strict')
        yield name, attrs, file_id
        if not following: break
        offset = end


def _entries(handle):
    # Fixed, aligned allocation; the API's continuation cursor is handle-local.
    buffer = (c.c_uint64 * (65536 // 8))()
    first = True
    while True:
        c.memset(c.byref(buffer), 0, c.sizeof(buffer))
        ok = win.GetInfo(handle, 11 if first else 10, c.byref(buffer), c.sizeof(buffer))
        if not ok:
            error = c.get_last_error()
            if error == 18: return  # ERROR_NO_MORE_FILES only
            raise c.WinError(error)
        first = False
        yield from _records(bytes(buffer))


def _marker(parent):
    """Existence probe only: any .git object, including a reparse, is a marker."""
    raw = '.git'.encode('utf-16-le'); buffer = c.create_string_buffer(raw + b'\0\0')
    string = win.UnicodeString(len(raw), len(raw)+2, c.addressof(buffer))
    attrs = win.Attributes(c.sizeof(win.Attributes), parent, c.pointer(string),
                           win.OBJ_DONT_REPARSE | 0x40, None, None)
    handle = win.HANDLE(); status = win.IoStatus()
    result = win.NtCreateFile(c.byref(handle), 0x100080, c.byref(attrs), c.byref(status),
                              None, 0, 7, 1, 0x20, None, 0)
    if result == 0:
        win.close(handle)
        return True
    if result & 0xffffffff in {0xC0000034, 0xC000003A}: return False
    # Reparse, inaccessible or otherwise ambiguous markers fail closed.
    raise ValueError('UNKNOWN: Git checkout requires git-worktree manifest mode')


class NativeSource:
    mode_model = 'windows-readonly/v1'

    def __init__(self, root):
        self.root = win.Root(root, pin=True, listing=True)
        self.path = self.root.path
        self.stack = None
        self.directories = {}

    def __enter__(self):
        win.need(platform.machine().lower() in {'amd64', 'x86_64'}, 'UNKNOWN: native Windows x64 required')
        win.need(c.sizeof(BasicInfo) == 40 and c.sizeof(HandleInfo) == 52,
                 'UNKNOWN: unsupported native source ABI')
        self.stack = ExitStack()
        try:
            self.stack.enter_context(self.root)
            self.directories['.'] = self.root.handle
            self.root_identity = _stamp(self.root.handle, True)[:2]
            return self
        except BaseException as primary:
            win.cleanup(self.stack.close, primary)
            raise

    def __exit__(self, *exc):
        win.cleanup(self.stack.close, exc[1])
        return False

    @staticmethod
    def mode(info):
        """windows-readonly/v1: attribute absent=0, present=1; no ACL claim."""
        return int(bool(info[7] & 1))

    def directory_boundary(self):
        for handle in self.root.handles:
            win.need(not _marker(handle), 'UNKNOWN: Git checkout requires git-worktree manifest mode')

    def walk(self, config, metrics=None):
        from support.source_manifests import MAX_DEPTH, MAX_VISITED, _path, _covered, _included, _collision
        result = {}; visited = 0
        if metrics is not None: metrics.repository_scan()

        def descend(handle, prefix, depth):
            nonlocal visited
            win.need(depth <= MAX_DEPTH, 'UNKNOWN: manifest depth limit')
            before = _stamp(handle, True)
            seen = set()
            for name, attrs, file_id in _entries(handle):
                visited += 1
                if metrics is not None: metrics.repository_entry()
                win.need(visited <= MAX_VISITED, 'UNKNOWN: manifest enumeration limit')
                if name in {'.', '..'}: continue
                win.component(name)
                win.need(name not in seen, 'UNKNOWN: duplicate native directory entry')
                seen.add(name)
                path = name if prefix == '.' else prefix + '/' + name
                _path(path)
                if prefix == '.' and name == '.git': continue
                if _covered(path, config['exclusions']): continue
                relevant = _covered(path, config['scope']) or any(scope.startswith(path + '/') for scope in config['scope'])
                if not relevant: continue
                win.need(name.casefold() != '.git', 'UNKNOWN: nested/aliased Git repository unsupported')
                win.need(not attrs & 0x400, 'UNKNOWN: source reparse point refused')
                directory = bool(attrs & 0x10)
                with win.opened(handle, name, directory=directory, pin=True, listing=directory) as child:
                    info = _stamp(child, directory)
                    win.need(info[1] == file_id, 'UNKNOWN: source changed during enumeration')
                    win.need(directory or info[3] == 1, 'UNKNOWN: source hardlink refused')
                    if _included(path, config): result[path] = info
                    win.need(len(result) <= config['max_files'], 'UNKNOWN: manifest entry limit')
                    if directory:
                        if path not in self.directories:
                            win.need(len(self.directories) <= MAX_VISITED, 'UNKNOWN: manifest directory handle limit')
                            held = self.stack.enter_context(win.opened(handle, name, directory=True, pin=True, listing=True))
                            self.directories[path] = held
                        held = self.directories[path]
                        win.need(_stamp(held, True) == info, 'UNKNOWN: source directory changed')
                        descend(held, path, depth+1)
                    win.need(_stamp(child, directory) == info, 'UNKNOWN: source changed during enumeration')
            win.need(_stamp(handle, True) == before, 'UNKNOWN: source directory changed')

        if _included('.', config): result['.'] = _stamp(self.root.handle, True)
        descend(self.root.handle, '.', 0)
        _collision(result)
        return result

    def read(self, path, expected, limit):
        from support.source_manifests import _path
        _path(path)
        win.need(type(limit) is int and limit >= 0, 'UNKNOWN: nonnegative source read limit required')
        names = win.parts(path)
        parent_path = '/'.join(names[:-1]) or '.'
        win.need(parent_path in self.directories, 'UNKNOWN: unobserved source directory')
        parent = self.directories[parent_path]
        with win.opened(parent, names[-1]) as leaf:
            before = _stamp(leaf, False)
            win.need(before == expected and before[3] == 1, 'UNKNOWN: source changed before read')
            win.need(before[4] <= limit, 'UNKNOWN: manifest file/aggregate limit')
            data = bytearray()
            while len(data) <= limit:
                buffer = c.create_string_buffer(min(65536, limit + 1 - len(data)))
                count = win.ULONG()
                win.wincheck(win.ReadFile(leaf, buffer, len(buffer), c.byref(count), None))
                if not count.value: break
                data.extend(buffer.raw[:count.value])
            win.need(len(data) <= limit and len(data) == before[4] and _stamp(leaf, False) == expected,
                     'UNKNOWN: source changed during read or limit exceeded')
            return bytes(data)

    def verify_root(self):
        win.need(_stamp(self.root.handle, True)[:2] == self.root_identity, 'UNKNOWN: source root changed')
        with win.Root(self.path, pin=True, listing=True) as current:
            win.need(_stamp(current.handle, True)[:2] == self.root_identity, 'UNKNOWN: source root changed')
