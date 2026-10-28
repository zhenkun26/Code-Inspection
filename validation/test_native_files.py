"""Portable controls plus mandatory native Windows safety tests (no privilege changes)."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from support.local_files import read_bytes, root_lock
from interfaces.lifecycle.core import save

class PortableFiles(unittest.TestCase):
    def test_unicode_spaces_and_crlf_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'中文 space'; root.mkdir()
            (root/'数据.txt').write_bytes(b'{"x":1}\r\n')
            self.assertEqual(read_bytes(root,'数据.txt'), b'{"x":1}\r\n')
            old = hashlib.sha256(read_bytes(root,'数据.txt')).hexdigest()
            (root/'数据.txt').write_bytes(b'{"x":1}\n')
            self.assertNotEqual(old, hashlib.sha256(read_bytes(root,'数据.txt')).hexdigest())
    def test_lock_contention_and_exception_release(self):
        with tempfile.TemporaryDirectory() as d:
            code = "from pathlib import Path; from support.local_files import root_lock; import sys;\nwith root_lock(Path(sys.argv[1])): print('acquired',flush=True)"
            with root_lock(Path(d)):
                p = subprocess.run([sys.executable,'-B','-c',code,d], cwd=ROOT, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(p.returncode,0); self.assertNotIn('acquired',p.stdout)
            p = subprocess.run([sys.executable,'-B','-c',code,d], cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            with self.assertRaises(RuntimeError):
                with root_lock(Path(d)): raise RuntimeError('test')
            with root_lock(Path(d)): pass
    def test_cancelled_child_reaped_and_lock_released(self):
        instances=[]
        def interrupt(process,*args,**kwargs):
            instances.append(process)
            raise KeyboardInterrupt('injected cancellation')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyboardInterrupt):
                with root_lock(Path(d)), patch.object(subprocess.Popen,'communicate',interrupt):
                    subprocess.run([sys.executable,'-c','import time; time.sleep(30)'],capture_output=True,timeout=5)
            self.assertEqual(len(instances),1)
            self.assertIsNotNone(instances[0].poll())
            with root_lock(Path(d)): pass

    def test_git_stdin_bytes_are_exact(self):
        from validation.lifecycle.fixture import create
        from interfaces.lifecycle.merge import LocalGit
        with tempfile.TemporaryDirectory() as d:
            f=create(Path(d)/'fixture')
            policy=json.loads((Path(f['host'])/'policy.json').read_text(encoding='utf-8'))
            g=LocalGit(policy['merge']);content='line one\nline two\n'
            actual=g.run('hash-object','-w','--stdin',input=content)
            self.assertEqual(actual.returncode,0,actual.stderr)
            raw=content.encode('utf-8')
            expected=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            self.assertEqual(actual.stdout.strip(),expected)
            self.assertEqual(g.run('cat-file','blob',expected).stdout,content)

    def test_state_flat_name_and_failed_replace(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);save(root,'state.json',{'old':True});old=(root/'state.json').read_bytes()
            for n in ['../x','x/y','x\\y','x:stream']:
                with self.assertRaises(ValueError): save(root,n,{})
            with patch('interfaces.lifecycle.core.os.replace',side_effect=PermissionError('occupied')):
                with self.assertRaises(PermissionError): save(root,'state.json',{'new':True})
            self.assertEqual((root/'state.json').read_bytes(),old)
    def test_real_timeout_and_interrupt_cleanup(self):
        # Direct child only. This is not a process-tree sandbox.
        p=subprocess.Popen([sys.executable,'-u','-c','import time; print("started",flush=True); time.sleep(30)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            with self.assertRaises(subprocess.TimeoutExpired): p.communicate(timeout=.3)
        finally:
            p.kill();out,err=p.communicate(timeout=5)
        self.assertIn(b'started',out);self.assertIsNotNone(p.poll())
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyboardInterrupt):
                with root_lock(Path(d)): raise KeyboardInterrupt()
            with root_lock(Path(d)): pass

@unittest.skipUnless(os.name=='nt','N_A: Windows-native API controls; POSIX controls run separately')
class WindowsFiles(unittest.TestCase):
    def setUp(self):
        from support import windows_files as wf
        self.w=wf;self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def junction(self, link, target):
        # Fixed built-in command on self-owned paths, no changes to OS permissions.
        p=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(target)],capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
    def test_abi_and_names(self):
        w=self.w
        self.assertEqual(w.c.sizeof(w.StandardInfo),24)
        for path in ['../x','./x','x//y','x/../y','C:x','C:/x','/x','x\\y','x:ads','NUL.txt','COM¹','x.','x ','\\\\server\\share','x\x00y']:
            with self.subTest(path=path),self.assertRaises(ValueError): read_bytes(self.root,path)
        with self.assertRaises(ValueError): read_bytes('\\\\server\\share','x')
        (self.root/'中文 😀 space.txt').write_bytes(b'ok')
        self.assertEqual(read_bytes(self.root,'中文 😀 space.txt'),b'ok')
    def test_junction_and_symlink_all_positions(self):
        inside=self.root/'inside';outside=self.root/'outside';inside.mkdir();outside.mkdir()
        (outside/'raw').write_bytes(b'OUTSIDE')
        self.junction(inside/'junction',outside)
        (inside/'file-link').symlink_to(outside/'raw')
        (inside/'dir-link').symlink_to(outside,target_is_directory=True)
        for name in ['junction/raw','file-link','dir-link/raw']:
            with self.subTest(name=name),self.assertRaises((OSError,ValueError)): read_bytes(inside,name)
        with self.assertRaises((OSError,ValueError)): read_bytes(inside/'junction','raw')
        (inside/'junction').rmdir();(inside/'dir-link').unlink();(inside/'file-link').unlink()
    def test_rename_between_parent_and_leaf(self):
        gate=self.root/'gate';outside=self.root/'outside';gate.mkdir();outside.mkdir()
        (gate/'raw').write_bytes(b'INSIDE');(outside/'raw').write_bytes(b'OUTSIDE')
        original=self.w.opened;attempted=[]
        @contextmanager
        def intercepted(parent,name,**kw):
            with original(parent,name,**kw) as h:
                if name=='gate' and kw.get('directory'):
                    try:
                        gate.rename(self.root/'parked');self.junction(gate,outside)
                    except OSError: pass  # OS denial is safe; barrier still exercised.
                    attempted.append(True)
                yield h
        with patch.object(self.w,'opened',intercepted):
            try: result=read_bytes(self.root,'gate/raw')
            except (OSError,ValueError): result=None
        self.assertTrue(attempted);self.assertIn(result,[None,b'INSIDE'])
        if gate.is_junction(): gate.rmdir()
    def test_attribute_only_reparse_race(self):
        w=self.w;gate=self.root/'gate';outside=self.root/'outside';gate.mkdir();outside.mkdir();(outside/'raw').write_bytes(b'OUTSIDE')
        original=w.opened;attempted=[];mutation=[]
        ioctl=w.bind(w.K,'DeviceIoControl',w.w.BOOL,w.HANDLE,w.ULONG,w.c.c_void_p,w.ULONG,w.c.c_void_p,w.ULONG,w.c.POINTER(w.ULONG),w.c.c_void_p)
        @contextmanager
        def intercepted(parent,name,**kw):
            with original(parent,name,**kw) as h:
                if name=='gate' and kw.get('directory'):
                    attacker=w.CreateFile(str(gate),0x100,7,None,3,0x02200000,None)
                    if attacker==w.c.c_void_p(-1).value: raise w.c.WinError(w.c.get_last_error())
                    try:
                        sub=('\\??\\'+str(outside)).encode('utf-16-le');pr=str(outside).encode('utf-16-le')
                        payload=struct.pack('<HHHH',0,len(sub),len(sub)+2,len(pr))+sub+b'\0\0'+pr+b'\0\0'
                        data=struct.pack('<IHH',0xA0000003,len(payload),0)+payload
                        buf=w.c.create_string_buffer(data);count=w.ULONG()
                        ok=ioctl(attacker,0x900A4,buf,len(data),None,0,w.c.byref(count),None)
                        mutation.append(bool(ok));attempted.append(True)
                        # This setup must actually create the threat, not only deny an unrelated permission.
                        self.assertTrue(ok, str(w.c.WinError(w.c.get_last_error())))
                    finally: w.close(attacker)
                yield h
        with patch.object(w,'opened',intercepted):
            try: result=read_bytes(self.root,'gate/raw')
            except (OSError,ValueError): result=None
        self.assertEqual(attempted,[True]);self.assertEqual(mutation,[True]);self.assertIsNone(result)
        if gate.is_junction(): gate.rmdir()
    def test_lockfileex_contention_and_crash(self):
        w=self.w
        with w.Root(self.root) as r, w.opened(r.handle,'lock',create=True,lock=True) as a, w.opened(r.handle,'lock',lock=True) as b:
            ov=w.Overlapped();w.wincheck(w.LockFileEx(a,3,0,1,0,w.c.byref(ov)))
            try:
                self.assertFalse(w.LockFileEx(b,3,0,1,0,w.c.byref(w.Overlapped())))
                self.assertEqual(w.c.get_last_error(),33)
            finally: w.wincheck(w.UnlockFileEx(a,0,1,0,w.c.byref(ov)))
        code="from pathlib import Path; from support.local_files import root_lock; import sys,time;\nwith root_lock(Path(sys.argv[1])): print('locked',flush=True); time.sleep(30)"
        p=subprocess.Popen([sys.executable,'-u','-c',code,str(self.root)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try: self.assertEqual(p.stdout.readline().strip(),b'locked')
        finally: p.kill();p.communicate(timeout=5)
        with root_lock(self.root): pass
    def test_busy_replace_and_cleanup_failure(self):
        w=self.w;save(self.root,'state.json',{'old':True});old=(self.root/'state.json').read_bytes()
        handle=w.CreateFile(str(self.root/'state.json'),0x80000000,3,None,3,0,None)
        if handle==w.c.c_void_p(-1).value: raise w.c.WinError(w.c.get_last_error())
        try:
            with self.assertRaises(OSError): save(self.root,'state.json',{'new':True})
            with self.assertRaises(OSError): (self.root/'state.json').unlink()
            self.assertEqual((self.root/'state.json').read_bytes(),old)
        finally: w.close(handle)
        save(self.root,'state.json',{'new':True});self.assertEqual(json.loads((self.root/'state.json').read_text(encoding='utf-8')),{'new':True})
    def test_capability_failure_and_handle_cleanup(self):
        w=self.w;file=self.root/'raw';file.write_bytes(b'ok')
        for i in range(20):
            with patch.object(w,'NtCreateFile',return_value=-1073741790):
                with self.assertRaises(OSError): read_bytes(self.root,'raw')
        self.assertEqual(read_bytes(self.root,'raw'),b'ok')
        count=w.bind(w.K,'GetProcessHandleCount',w.w.BOOL,w.HANDLE,w.c.POINTER(w.ULONG))
        process=w.bind(w.K,'GetCurrentProcess',w.HANDLE)
        def handles():
            n=w.ULONG();w.wincheck(count(process(),w.c.byref(n)));return n.value
        before=handles()
        original=w.check
        def fail_leaf(h,directory):
            result=original(h,directory)
            if not directory: raise OSError('injected after-open check failure')
            return result
        for i in range(20):
            with patch.object(w,'check',side_effect=fail_leaf):
                with self.assertRaises(OSError): read_bytes(self.root,'raw')
        for i in range(20):
            with patch.object(w,'ReadFile',return_value=False):
                with self.assertRaises(OSError): read_bytes(self.root,'raw')
        self.assertEqual(handles(),before)
        file.unlink()  # No leaked leaf handle preventing deletion.

    def test_native_cleanup_preserves_primary(self):
        w=self.w
        def failure(): raise OSError('cleanup failure')
        with self.assertRaisesRegex(ValueError,'original failure') as raised:
            w.cleanup(failure,ValueError('original failure'))
        self.assertIsInstance(raised.exception.__cause__,OSError)

    def test_real_temp_cleanup_failure_preserves_primary(self):
        w=self.w;save(self.root,'state.json',{'old':True});old=(self.root/'state.json').read_bytes();held=[]
        def occupied_replace(source,target):
            h=w.CreateFile(str(source),0x80000000,3,None,3,0,None)
            if h==w.c.c_void_p(-1).value: raise w.c.WinError(w.c.get_last_error())
            held.append(h)
            raise PermissionError('primary replace refused')
        try:
            with patch('interfaces.lifecycle.core.os.replace',side_effect=occupied_replace):
                with self.assertRaisesRegex(PermissionError,'primary replace refused') as raised:
                    save(self.root,'state.json',{'new':True})
            self.assertIsInstance(raised.exception.__cause__,OSError)
            self.assertTrue(list(self.root.glob('.lifecycle-*')))
            self.assertEqual((self.root/'state.json').read_bytes(),old)
        finally:
            for h in held: w.close(h)
        for p in self.root.glob('.lifecycle-*'): p.unlink()


if __name__=='__main__': unittest.main(verbosity=2)
