"""Native x64 Windows / local NTFS backend. No fallback, no third-party DLLs.

RootDirectory-relative opens and OBJ_DONT_REPARSE protect evidence traversal.
Host-owned state namespaces and Git fixtures must still have trusted writers.
"""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys

K = c.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
N = c.WinDLL('ntdll.dll', use_last_error=True, winmode=0x800)
HANDLE = c.c_void_p
ULONG = c.c_uint32
NTSTATUS = c.c_int32
SIZE = c.c_size_t

class UnicodeString(c.Structure):
    _fields_ = [('Length', c.c_uint16), ('MaximumLength', c.c_uint16), ('Buffer', c.c_void_p)]
class Attributes(c.Structure):
    _fields_ = [('Length', ULONG), ('RootDirectory', HANDLE), ('ObjectName', c.POINTER(UnicodeString)),
                ('Attributes', ULONG), ('SecurityDescriptor', c.c_void_p), ('SecurityQualityOfService', c.c_void_p)]
class IoStatus(c.Structure):
    _fields_ = [('StatusOrPointer', c.c_void_p), ('Information', SIZE)]
class Overlapped(c.Structure):
    _fields_ = [('Internal', SIZE), ('InternalHigh', SIZE), ('Offset', ULONG), ('OffsetHigh', ULONG), ('hEvent', HANDLE)]
class TagInfo(c.Structure):
    _fields_ = [('Attributes', ULONG), ('Tag', ULONG)]
class StandardInfo(c.Structure):
    _fields_ = [('AllocationSize', c.c_int64), ('EndOfFile', c.c_int64), ('NumberOfLinks', ULONG),
                ('DeletePending', c.c_ubyte), ('Directory', c.c_ubyte)]

def bind(dll, name, result, *args):
    f = getattr(dll, name); f.restype = result; f.argtypes = args
    return f
CreateFile = bind(K, 'CreateFileW', HANDLE, w.LPCWSTR, ULONG, ULONG, c.c_void_p, ULONG, ULONG, HANDLE)
CloseHandle = bind(K, 'CloseHandle', w.BOOL, HANDLE)
NtCreateFile = bind(N, 'NtCreateFile', NTSTATUS, c.POINTER(HANDLE), ULONG, c.POINTER(Attributes),
                    c.POINTER(IoStatus), c.c_void_p, ULONG, ULONG, ULONG, ULONG, c.c_void_p, ULONG)
ToError = bind(N, 'RtlNtStatusToDosError', ULONG, NTSTATUS)
GetInfo = bind(K, 'GetFileInformationByHandleEx', w.BOOL, HANDLE, c.c_int, c.c_void_p, ULONG)
GetType = bind(K, 'GetFileType', ULONG, HANDLE)
ReadFile = bind(K, 'ReadFile', w.BOOL, HANDLE, c.c_void_p, ULONG, c.POINTER(ULONG), c.c_void_p)
VolumeInfo = bind(K, 'GetVolumeInformationByHandleW', w.BOOL, HANDLE, w.LPWSTR, ULONG,
                  c.POINTER(ULONG), c.POINTER(ULONG), c.POINTER(ULONG), w.LPWSTR, ULONG)
DriveType = bind(K, 'GetDriveTypeW', ULONG, w.LPCWSTR)
DosDevice = bind(K, 'QueryDosDeviceW', ULONG, w.LPCWSTR, w.LPWSTR, ULONG)
LockFileEx = bind(K, 'LockFileEx', w.BOOL, HANDLE, ULONG, ULONG, ULONG, ULONG, c.POINTER(Overlapped))
UnlockFileEx = bind(K, 'UnlockFileEx', w.BOOL, HANDLE, ULONG, ULONG, ULONG, c.POINTER(Overlapped))
OBJ_DONT_REPARSE = 0x1000
DIR_ACCESS = 0x100080 | 0x20  # synchronize, attributes, traverse
FILE_READ = 0x100081


def need(ok, message):
    if not ok: raise ValueError(message)
def wincheck(ok):
    if not ok: raise c.WinError(c.get_last_error())
def close(handle):
    wincheck(CloseHandle(handle))
def cleanup(call, primary):
    try: call()
    except BaseException as failure:
        if primary is not None: raise primary from failure
        raise
def component(name):
    need(bool(name) and name not in {'.', '..'} and not name.endswith((' ', '.')),
         'relative bounded path required')
    need(not any(ord(x) < 32 or x in '\\/:*?"<>|\x7f' for x in name), 'relative bounded path required')
    base = name.split('.')[0].upper()
    need(not re.fullmatch(r'CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]', base),
         'reserved Windows name')
    need(len(name.encode('utf-16-le')) <= 510, 'Windows component too long')
    return name

def parts(relative):
    raw = os.fspath(relative)
    if isinstance(relative, Path): raw = raw.replace('\\', '/')
    need(isinstance(raw, str), 'relative bounded path required')
    return [component(p) for p in raw.split('/')]

def check(handle, directory):
    tag = TagInfo(); info = StandardInfo()
    wincheck(GetInfo(handle, 9, c.byref(tag), c.sizeof(tag)))
    wincheck(GetInfo(handle, 1, c.byref(info), c.sizeof(info)))
    need(not tag.Attributes & 0x400 and tag.Tag == 0, 'reparse point refused')
    need(GetType(handle) == 1 and bool(info.Directory) == directory and not info.DeletePending,
         'not a bounded regular file' if not directory else 'directory required')
    return info

@contextmanager
def opened(parent, name, directory=False, create=False, lock=False, *, pin=False, listing=False):
    component(name)
    raw = name.encode('utf-16-le'); buffer = c.create_string_buffer(raw + b'\0\0')
    string = UnicodeString(len(raw), len(raw)+2, c.addressof(buffer))
    attrs = Attributes(c.sizeof(Attributes), parent, c.pointer(string), OBJ_DONT_REPARSE, None, None)
    handle = HANDLE(); status = IoStatus()
    access = (DIR_ACCESS | (1 if listing else 0)) if directory else FILE_READ | (2 if lock else 0)
    # Synchronous handle, no reparse traversal; force directory/non-directory type.
    result = NtCreateFile(c.byref(handle), access, c.byref(attrs), c.byref(status), None, 0,
                          3 if lock or (directory and pin) else 7 if directory else 1, 3 if create else 1,
                          0x20 | (1 if directory else 0x40), None, 0)
    if result != 0:
        if result & 0xffffffff in {0xC00000BA, 0xC0000103}:
            raise ValueError('not a bounded regular file' if not directory else 'directory required')
        raise c.WinError(ToError(result))
    try:
        check(handle, directory)
        yield handle
    finally:
        cleanup(lambda: close(handle), sys.exc_info()[1])

class Root:
    def __init__(self, root, *, pin=False, listing=False):
        raw = os.fspath(root)
        need(not raw.startswith(('\\', '/')) and not re.match(r'^[A-Za-z]:(?:$|[^\\/])', raw),
             'local absolute Windows root required')
        # Relative roots are trusted caller selections, not evidence paths.
        raw = os.path.abspath(raw)
        need(re.match(r'^[A-Za-z]:\\', raw) is not None and len(raw.encode('utf-16-le'))//2 < 240,
             'short local Windows root required')
        self.path = Path(raw)
        self.names = [component(p) for p in raw[3:].split('\\') if p]
        self.drive = raw[:3]; self.stack = None; self.handle = None
        self.pin = pin; self.listing = listing; self.handles = []
    def __fspath__(self): return str(self.path)
    def __truediv__(self, name): return self.path / name
    def __enter__(self):
        from contextlib import ExitStack
        need(c.sizeof(HANDLE)==8 and c.sizeof(Attributes)==48 and c.sizeof(UnicodeString)==16
             and c.sizeof(IoStatus)==16 and c.sizeof(Overlapped)==32, 'unsupported Windows ABI')
        need(DriveType(self.drive)==3, 'only local fixed NTFS volumes supported')
        device = c.create_unicode_buffer(1024)
        wincheck(DosDevice(self.drive[:2], device, len(device)))
        need(re.fullmatch(r'\\Device\\HarddiskVolume\d+', device.value) is not None,
             'mapped/unsupported volume refused')
        stack = ExitStack()
        try:
            h = CreateFile(self.drive, DIR_ACCESS | (1 if self.listing else 0),
                           3 if self.pin else 7, None, 3, 0x02000000 | 0x00200000, None)
            if h == c.c_void_p(-1).value: raise c.WinError(c.get_last_error())
            stack.callback(close, h); check(h, True)
            fs = c.create_unicode_buffer(32)
            wincheck(VolumeInfo(h, None, 0, None, None, None, fs, len(fs)))
            need(fs.value == 'NTFS', 'only local fixed NTFS volumes supported')
            self.handles = [h]
            for name in self.names:
                h = stack.enter_context(opened(h, name, directory=True, pin=self.pin, listing=self.listing))
                self.handles.append(h)
            self.handle = h; self.stack = stack
            return self
        except BaseException as primary:
            cleanup(stack.close, primary)
            raise
    def __exit__(self, *exc):
        cleanup(self.stack.close, exc[1])
        return False
    def read(self, relative, limit, single_link=False):
        from contextlib import ExitStack
        names = parts(relative)
        with ExitStack() as stack:
            parent = self.handle
            for name in names[:-1]: parent = stack.enter_context(opened(parent, name, directory=True))
            leaf = stack.enter_context(opened(parent, names[-1]))
            info = check(leaf, False)
            need(info.EndOfFile <= limit, 'not a bounded regular file')
            if single_link:
                need(info.NumberOfLinks == 1, 'artifact hardlinks refused')
            result = bytearray()
            while len(result) <= limit:
                buf = c.create_string_buffer(min(65536, limit+1-len(result))); count = ULONG()
                wincheck(ReadFile(leaf, buf, len(buf), c.byref(count), None))
                if not count.value: break
                result.extend(buf.raw[:count.value])
            need(len(result) <= limit, 'file exceeds read limit')
            if single_link:
                after = check(leaf, False)
                need(after.NumberOfLinks == 1 and after.EndOfFile == info.EndOfFile,
                     'artifact changed during snapshot read')
            return bytes(result)
    @contextmanager
    def locked(self):
        with opened(self.handle, 'lock', create=True, lock=True) as handle:
            ov = Overlapped(); acquired = False
            try:
                wincheck(LockFileEx(handle, 3, 0, 1, 0, c.byref(ov))); acquired = True
                yield
            finally:
                if acquired: cleanup(lambda: wincheck(UnlockFileEx(handle, 0, 1, 0, c.byref(ov))), sys.exc_info()[1])
