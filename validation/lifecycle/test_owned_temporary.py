"""Owned, disposable host fixtures; cleanup never extends to unknown residues.

Namespace-replacement tests check refusals before the decision. They do not claim
that POSIX stat/unlink resists nonparticipating writers after that decision.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle import core, owned_temporary as owned
from support.local_files import root_lock
from validation.test_support import temporary_directory


class ManagedPlatform(unittest.TestCase):
    def test_unsupported_refuses_before_path_or_mutation(self):
        with patch.object(owned.os, 'name', 'nt'), patch.object(owned.os, 'open') as opening:
            with self.assertRaisesRegex(ValueError, 'unsupported'): owned._supported()
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                owned.ManagedTemporaryNamespace('unused', '0' * 64, exclusive_namespace=True)
            opening.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'managed removal requires native POSIX descriptors')
class OwnedTemporary(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve(strict=True)
        self.parent = self.base / 'parent'; self.parent.mkdir()
        self.host = self.parent / 'host'; self.host.mkdir()
        self.policy = b'{"purpose":"disposable owned temporary fixture"}\n'
        (self.host / 'policy.json').write_bytes(self.policy)
        self.pin = hashlib.sha256(self.policy).hexdigest()
        self.original = b'{"budget":{"operations":7},"events":["preserve"],"receipts":{"x":1}}\n'
        (self.host / 'state.json').write_bytes(self.original)
        (self.host / 'authority.json').write_bytes(b'untouched authority')

    def namespace(self, **kwargs):
        return owned.ManagedTemporaryNamespace(self.host, self.pin, exclusive_namespace=True, **kwargs)

    def residue(self, namespace=None):
        namespace = namespace or self.namespace()
        with namespace.locked() as root:
            with patch.object(owned.ManagedRoot, '_publish', side_effect=RuntimeError('fixture publication failed')):
                with self.assertRaisesRegex(RuntimeError, 'fixture publication failed'):
                    core.save(root, 'state.json', {'uncommitted': 'never adopt'})
        record, = self.host.glob('.lifecycle-owner-*.json')
        owner = json.loads(record.read_bytes())
        return namespace, record, self.host / owner['payload'], owner

    def assert_preserved(self):
        self.assertEqual((self.host / 'state.json').read_bytes(), self.original)
        self.assertEqual((self.host / 'authority.json').read_bytes(), b'untouched authority')

    def refuse(self, namespace, record):
        with namespace.locked() as root:
            with self.assertRaises((ValueError, OSError)):
                root.recover(record.name, authorize=True)

    def test_registered_hard_exit_recovers_exact_owned_file_only_and_releases_lock(self):
        unknown = self.host / '.lifecycle-legacy-unknown'; unknown.write_bytes(b'never delete')
        code = """import os,sys
from interfaces.lifecycle import core,owned_temporary as owned
def stop(*args): os._exit(73)
owned.ManagedRoot._publish=stop
namespace=owned.ManagedTemporaryNamespace(sys.argv[1],sys.argv[2],exclusive_namespace=True)
with namespace.locked() as root:
    core.save(root,'state.json',{'uncommitted':'never adopt'})
"""
        child = subprocess.run([sys.executable, '-B', '-c', code, str(self.host), self.pin],
                               cwd=ROOT, capture_output=True, timeout=15)
        self.assertEqual(child.returncode, 73, child.stderr)
        record, = self.host.glob('.lifecycle-owner-*.json')
        payload = self.host / json.loads(record.read_bytes())['payload']
        with self.namespace().locked() as root:
            result = root.recover(record.name, authorize=True)
            self.assertEqual(result['action'], 'REMOVED_OWNED_TEMPORARY')
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['publication'], 'UNKNOWN')
            self.assertFalse(result['authorizes_action'])
            with self.assertRaises(FileNotFoundError): root.recover(record.name, authorize=True)
        self.assertFalse(record.exists()); self.assertFalse(payload.exists())
        self.assertEqual(unknown.read_bytes(), b'never delete'); self.assert_preserved()

    def test_explicit_assurance_and_exact_cleanup_grant_precede_reads(self):
        with patch.object(owned.os, 'open') as opening, \
             patch.object(owned, '_chain', side_effect=AssertionError('unassured namespace opened')):
            for assurance in (False, None, 1, 'host-controlled'):
                with self.assertRaises(ValueError):
                    owned.ManagedTemporaryNamespace(self.host, self.pin, exclusive_namespace=assurance)
            opening.assert_not_called()
        namespace, record, payload, _ = self.residue()
        with namespace.locked() as root:
            with patch.object(root, '_read', side_effect=AssertionError('no payload before grant')):
                for grant in (False, None, 1, 'approve'):
                    with self.assertRaises(ValueError): root.recover(record.name, authorize=grant)
                for name in (payload.name, '../' + record.name, '*', 'state.json', record.name + '/x'):
                    with self.assertRaises(ValueError): root.recover(name, authorize=True)
        self.assertTrue(payload.exists()); self.assertTrue(record.exists()); self.assert_preserved()

    def test_context_is_exact_active_nonreentrant_and_legacy_save_stays_unregistered(self):
        namespace = self.namespace()
        with namespace.locked() as root:
            with self.assertRaises(ValueError):
                with namespace.locked(): pass
            core.save(root, 'state.json', {'managed': True})
            self.assertEqual(core.load(root, 'state.json'), {'managed': True})
            self.assertEqual(list(self.host.glob('.lifecycle-*')), [])
        with patch.object(owned.os, 'fstat', side_effect=AssertionError('inactive context reached descriptor')):
            with self.assertRaises(ValueError): core.save(root, 'state.json', {'stale': True})
        with self.assertRaises(ValueError): root.recover('.lifecycle-owner-' + '0'*32 + '.json', authorize=True)
        class Forged:
            def save(self, *args): raise AssertionError('forged root dispatched')
            def __truediv__(self, name): return self_host / name
            def __fspath__(self): return str(self_host)
        self_host = self.host
        core.save(Forged(), 'state.json', {'legacy': True})
        self.assertEqual(json.loads((self.host / 'state.json').read_bytes()), {'legacy': True})
        self.assertEqual(list(self.host.glob('.lifecycle-owner-*')), [])

    def test_lock_contention_and_single_link_regular_lock_required(self):
        namespace = self.namespace()
        with root_lock(self.host):
            with self.assertRaises(OSError):
                with namespace.locked(): pass
        lock = self.host / 'lock'; alias = self.host / 'lock-alias'; os.link(lock, alias)
        with self.assertRaises(ValueError):
            with namespace.locked(): pass
        alias.unlink(); lock.unlink(); lock.mkdir()
        with self.assertRaises((ValueError, OSError)):
            with namespace.locked(): pass
        lock.rmdir(); lock.symlink_to(self.host / 'state.json')
        with self.assertRaises(OSError):
            with namespace.locked(): pass
        self.assert_preserved()

    def test_lock_shape_and_payload_structure_precede_context_or_payload_reads(self):
        namespace = self.namespace()
        with namespace.locked(): pass
        alias = self.host / 'lock-alias'; os.link(self.host / 'lock', alias)
        with patch.object(owned.ManagedRoot, 'validate', side_effect=AssertionError('linked lock reached validation')):
            with self.assertRaises(ValueError):
                with namespace.locked(): pass
        alias.unlink()
        fifo = self.host / '.lifecycle-test-fifo'; os.mkfifo(fifo)
        link = self.host / '.lifecycle-test-link'; link.symlink_to(self.host / 'state.json')
        oversized = self.host / '.lifecycle-test-oversize'; oversized.write_bytes(b'x' * 4097)
        with namespace.locked() as root:
            with patch.object(owned.os, 'read', side_effect=AssertionError('rejected structure reached payload read')):
                with self.assertRaises(ValueError): root._read(fifo.name, 4096)
                with self.assertRaises(OSError): root._read(link.name, 4096)
                with self.assertRaises(ValueError): root._read(oversized.name, 4096)
        self.assert_preserved()

    def test_early_and_partial_records_are_retained_with_primary_failure(self):
        namespace = self.namespace()
        primary = RuntimeError('primary payload fsync failure')
        with namespace.locked() as root:
            with patch.object(owned.os, 'fsync', side_effect=primary):
                with self.assertRaises(RuntimeError) as caught: core.save(root, 'state.json', {'early': True})
            self.assertIs(caught.exception, primary)
        payload, = self.host.glob('.lifecycle-managed-*')
        self.assertTrue(payload.exists()); self.assertEqual(list(self.host.glob('.lifecycle-owner-*')), [])
        original = owned.ManagedRoot._write_new
        def partial(root, name, data):
            if name.startswith('.lifecycle-owner-'):
                original(root, name, b'{"partial":')
                raise RuntimeError('primary marker failure')
            return original(root, name, data)
        with namespace.locked() as root:
            with patch.object(owned.ManagedRoot, '_write_new', partial):
                with self.assertRaisesRegex(RuntimeError, 'primary marker failure'):
                    core.save(root, 'state.json', {'partial': True})
        record, = self.host.glob('.lifecycle-owner-*')
        self.refuse(namespace, record)
        self.assertTrue(record.exists()); self.assertEqual(len(list(self.host.glob('.lifecycle-managed-*'))), 2)
        self.assert_preserved()

    def test_policy_and_record_root_binding_reject_before_payload(self):
        namespace, record, payload, owner = self.residue()
        owner['root']['inode'] += 1; record.write_text(json.dumps(owner))
        with namespace.locked() as root:
            original = root._read
            def record_only(name, limit):
                self.assertIn(name, {'policy.json', record.name}, 'root drift must precede payload read')
                return original(name, limit)
            with patch.object(root, '_read', side_effect=record_only):
                with self.assertRaisesRegex(ValueError, 'root mismatch'): root.recover(record.name, authorize=True)
        self.assertTrue(payload.exists())
        (self.host / 'policy.json').write_bytes(b'changed policy')
        with self.assertRaisesRegex(ValueError, 'policy pin'):
            with namespace.locked(): pass
        self.assertTrue(record.exists()); self.assert_preserved()

    def test_record_schema_bounds_and_identity_bindings_refuse(self):
        namespace, record, payload, original = self.residue()
        malformed = [b'{', b'[]', b'x' * (owned.MAX_RECORD_BYTES + 1),
                     b'{"schema":1,"schema":2}']
        for field, value in [('schema','other'), ('policy_sha','0'*64), ('resource','0'*32),
                             ('payload','state.json'), ('target','../state.json'), ('target','policy.json'),
                             ('metadata',dict(original['metadata'], size=True)), ('sha256','unknown')]:
            owner = copy.deepcopy(original); owner[field] = value; malformed.append(json.dumps(owner).encode())
        owner = copy.deepcopy(original); owner['extra'] = True; malformed.append(json.dumps(owner).encode())
        for data in malformed:
            with self.subTest(data=data[:80]):
                record.write_bytes(data); self.refuse(namespace, record)
                self.assertTrue(payload.exists()); self.assertTrue(record.exists())
        self.assert_preserved()

    def test_payload_type_link_replacement_metadata_and_digest_guards(self):
        namespace, record, payload, owner = self.residue()
        original = payload.read_bytes()
        payload.write_bytes(b'changed payload'); self.refuse(namespace, record)
        payload.unlink(); payload.write_bytes(original); self.refuse(namespace, record)
        owner['metadata'] = owned._metadata(payload.stat())
        owner['sha256'] = '0' * 64; record.write_text(json.dumps(owner)); self.refuse(namespace, record)
        owner['sha256'] = hashlib.sha256(original).hexdigest(); record.write_text(json.dumps(owner))
        alias = self.host / 'payload-alias'; os.link(payload, alias); self.refuse(namespace, record); alias.unlink()
        payload.unlink()
        for kind in ('directory','fifo','symlink'):
            with self.subTest(kind=kind):
                if kind == 'directory': payload.mkdir()
                elif kind == 'fifo': os.mkfifo(payload)
                else: payload.symlink_to(self.host / 'state.json')
                self.refuse(namespace, record)
                if kind == 'directory': payload.rmdir()
                else: payload.unlink()
        self.assertTrue(record.exists()); self.assert_preserved()

    def test_record_links_nonregular_and_oversize_refuse_before_payload(self):
        namespace, record, payload, _ = self.residue()
        raw = record.read_bytes(); alias = self.host / 'record-alias'; os.link(record, alias)
        self.refuse(namespace, record); alias.unlink(); record.unlink()
        for kind in ('directory','fifo','symlink'):
            with self.subTest(kind=kind):
                if kind == 'directory': record.mkdir()
                elif kind == 'fifo': os.mkfifo(record)
                else: record.symlink_to(self.host / 'state.json')
                self.refuse(namespace, record)
                if kind == 'directory': record.rmdir()
                else: record.unlink()
        record.write_bytes(raw); payload.write_bytes(b'x' * (owned.MAX_PAYLOAD_BYTES + 1))
        self.refuse(namespace, record); self.assertTrue(record.exists()); self.assertTrue(payload.exists())
        self.assert_preserved()

    def test_published_missing_payload_never_reads_or_removes_target(self):
        namespace = self.namespace()
        with namespace.locked() as root:
            original = root._sync; calls = []
            def fail_after_publish():
                calls.append(None)
                if len(calls) == 2: raise OSError('directory sync uncertain')
                original()
            with patch.object(root, '_sync', side_effect=fail_after_publish):
                with self.assertRaises(OSError): core.save(root, 'state.json', {'published': True})
        record, = self.host.glob('.lifecycle-owner-*')
        saved = (self.host / 'state.json').read_bytes()
        with namespace.locked() as root:
            original = root._read
            def no_target(name, limit):
                self.assertNotEqual(name, 'state.json', 'missing temporary must never redirect to target')
                return original(name, limit)
            with patch.object(root, '_read', side_effect=no_target):
                result = root.recover(record.name, authorize=True)
                again = root.recover(record.name, authorize=True)
        self.assertEqual(result, again); self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['action'], 'RETAIN'); self.assertTrue(record.exists())
        self.assertEqual((self.host / 'state.json').read_bytes(), saved)

    def test_parent_replacement_before_validation_never_touches_outside_payload(self):
        namespace, record, payload, _ = self.residue()
        outside = self.base / 'outside'; outside.mkdir(); outside_host = outside / 'host'; outside_host.mkdir()
        target = outside_host / payload.name; target.write_bytes(b'outside preserve')
        with namespace.locked() as root:
            old = self.base / 'original-parent'
            self.parent.rename(old); self.parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaises((ValueError, OSError)): root.recover(record.name, authorize=True)
            self.assertTrue((old / 'host' / payload.name).exists())
            self.assertEqual(target.read_bytes(), b'outside preserve')
        with self.assertRaises((ValueError, OSError)):
            with namespace.locked(): pass
        with self.assertRaises((ValueError, OSError)): self.namespace()
        self.parent.unlink(); old.rename(self.parent); self.assert_preserved()

    def test_policy_change_after_payload_read_refuses_before_delete(self):
        namespace, record, payload, _ = self.residue()
        with namespace.locked() as root:
            original = root._read
            def drift(name, limit):
                result = original(name, limit)
                if name == payload.name: (self.host / 'policy.json').write_bytes(b'changed policy')
                return result
            with patch.object(root, '_read', side_effect=drift):
                with self.assertRaisesRegex(ValueError, 'policy pin'): root.recover(record.name, authorize=True)
        self.assertTrue(payload.exists()); self.assertTrue(record.exists()); self.assert_preserved()

    def test_target_replacement_is_irrelevant_and_record_replacement_refuses(self):
        namespace, record, payload, _ = self.residue()
        target = self.host / 'state.json'; target.unlink(); target.symlink_to(self.host / 'authority.json')
        with namespace.locked() as root:
            raw = record.read_bytes(); original = root._read
            def replace_marker(name, limit):
                result = original(name, limit)
                if name == payload.name:
                    record.unlink(); record.write_bytes(raw)
                return result
            with patch.object(root, '_read', side_effect=replace_marker):
                with self.assertRaisesRegex(ValueError, 'replaced'): root.recover(record.name, authorize=True)
            self.assertTrue(payload.exists()); self.assertTrue(record.exists())
            result = root.recover(record.name, authorize=True)
        self.assertEqual(result['status'], 'PASS'); self.assertTrue(target.is_symlink())
        self.assertEqual((self.host / 'authority.json').read_bytes(), b'untouched authority')

    def test_valid_oversized_record_refuses_before_payload_and_hardlinks_precede_read(self):
        namespace, record, payload, _ = self.residue()
        raw = record.read_bytes()
        record.write_bytes(raw + b' ' * (4097 - len(raw)))
        self.refuse(namespace, record)
        self.assertTrue(record.exists()); self.assertTrue(payload.exists())
        record.write_bytes(raw)
        with namespace.locked() as root:
            alias = self.host / 'marker-alias'; os.link(record, alias)
            with patch.object(owned.os, 'read', side_effect=AssertionError('hardlink payload read')):
                with self.assertRaises(ValueError): root._read(record.name, owned.MAX_RECORD_BYTES)
            alias.unlink()
        self.assert_preserved()

    def test_cancellation_propagates_and_second_unlink_failure_is_unknown(self):
        namespace, record, payload, _ = self.residue()
        with namespace.locked() as root:
            original = root._unlink
            def denied_marker(name):
                if name == record.name: raise PermissionError('fixture marker unlink denied')
                original(name)
            with patch.object(root, '_unlink', side_effect=denied_marker):
                with self.assertRaises(owned.CleanupUnknown) as caught: root.recover(record.name, authorize=True)
            self.assertIsInstance(caught.exception.__cause__, PermissionError)
        self.assertFalse(payload.exists()); self.assertTrue(record.exists())
        record.unlink()
        namespace, record, payload, _ = self.residue(namespace)
        with namespace.locked() as root:
            with patch.object(root, '_sync', side_effect=KeyboardInterrupt('fixture cancellation')):
                with self.assertRaises(KeyboardInterrupt): root.recover(record.name, authorize=True)
        self.assertFalse(payload.exists()); self.assertTrue(record.exists()); self.assert_preserved()

    def test_teardown_releases_every_descriptor_and_preserves_primary(self):
        namespace = self.namespace(); primary = RuntimeError('primary operation failure')
        real_close = owned.os.close; released = []; failures = []
        def close_then_fail(fd):
            real_close(fd); released.append(fd)
            if not failures:
                failures.append(OSError('fixture close failure')); raise failures[0]
        context = namespace.locked(); selected = context.__enter__()
        expected = set(selected._descriptors + [selected._lock])
        with patch.object(owned.os, 'close', side_effect=close_then_fail):
            with self.assertRaises(RuntimeError) as caught:
                try: raise primary
                except BaseException:
                    if not context.__exit__(*sys.exc_info()): raise
        self.assertIs(caught.exception, primary); self.assertIs(caught.exception.__cause__, failures[0])
        self.assertEqual(set(released), expected); self.assertFalse(namespace._busy)
        with namespace.locked(): pass

    def test_partial_chain_failure_closes_all_and_preserves_open_failure(self):
        real_open, real_close = owned.os.open, owned.os.close
        opened, closed = [], []; primary = PermissionError('fixture ancestor refused')
        def opening(*args, **kwargs):
            if len(opened) == 3: raise primary
            fd = real_open(*args, **kwargs); opened.append(fd); return fd
        def closing(fd):
            real_close(fd); closed.append(fd)
            if len(closed) == 1: raise OSError('fixture cleanup failure')
        with patch.object(owned.os, 'open', side_effect=opening), patch.object(owned.os, 'close', side_effect=closing):
            with self.assertRaises(PermissionError) as caught: self.namespace()
        self.assertIs(caught.exception, primary); self.assertEqual(set(opened), set(closed))

    def test_unlink_and_post_deletion_sync_failures_remain_unknown(self):
        namespace, record, payload, _ = self.residue()
        with namespace.locked() as root:
            with patch.object(root, '_unlink', side_effect=PermissionError('fixture unlink denied')):
                with self.assertRaises(owned.CleanupUnknown) as caught: root.recover(record.name, authorize=True)
            self.assertEqual(caught.exception.status, 'UNKNOWN')
            self.assertTrue(payload.exists()); self.assertTrue(record.exists())
            with patch.object(root, '_sync', side_effect=OSError('fixture durability unknown')):
                with self.assertRaises(owned.CleanupUnknown): root.recover(record.name, authorize=True)
        self.assertFalse(payload.exists()); self.assertTrue(record.exists()); self.assert_preserved()

    def test_record_is_exclusive_and_registered_durable_before_publish(self):
        namespace = self.namespace(); resource = 'a' * 32
        record = self.host / ('.lifecycle-owner-' + resource + '.json'); record.write_bytes(b'never overwrite')
        with namespace.locked() as root:
            with patch.object(owned.uuid, 'uuid4', return_value=type('ID', (), {'hex':resource})()), \
                 patch.object(root, '_publish', side_effect=AssertionError('no publish without registration')):
                with self.assertRaises(FileExistsError): core.save(root, 'state.json', {'new': True})
        self.assertEqual(record.read_bytes(), b'never overwrite'); self.assert_preserved()
        record.unlink()
        with namespace.locked() as root:
            with patch.object(root, '_sync', side_effect=OSError('registration not durable')), \
                 patch.object(root, '_publish') as publish:
                with self.assertRaises(OSError): core.save(root, 'state.json', {'new': True})
                publish.assert_not_called()
        self.assert_preserved()

    def test_session_binding_save_failure_fence_and_reload_do_not_reset_budget(self):
        from validation.lifecycle.fixture import create
        fixture = create(self.base / 'session-fixture')
        host = Path(fixture['host']); pin = fixture['policy_sha']
        namespace = owned.ManagedTemporaryNamespace(host, pin, exclusive_namespace=True)
        with self.assertRaises(ValueError): core.Session(host, pin, temporary_namespace=object())
        with self.assertRaises(ValueError): core.Session(host, pin, temporary_namespace=self.namespace())
        session = core.Session(host, pin, temporary_namespace=namespace)
        handoff = json.loads((host / 'handoff.json').read_bytes())
        with session.locked() as s:
            s.initialize(handoff); before = (host / 'state.json').read_bytes()
            primary = PermissionError('primary publication failure')
            with patch.object(s.root, '_publish', side_effect=primary), \
                 patch.object(s.root, '_unlink', side_effect=OSError('cleanup must not mask primary')):
                with self.assertRaises(PermissionError) as caught: s.charge(operation='observe')
            self.assertIs(caught.exception, primary)
            self.assertTrue(s._persistence_failed); self.assertEqual(s.accounting_durability, 'unknown')
            with self.assertRaisesRegex(ValueError, 'persistence failed'): s.persist()
            self.assertEqual((host / 'state.json').read_bytes(), before)
        with session.locked() as s:
            self.assertFalse(s._persistence_failed)
            self.assertEqual(s.state['budget'], json.loads(before)['budget'])
            self.assertEqual((host / 'state.json').read_bytes(), before)
            record, = host.glob('.lifecycle-owner-*')
            result = s.root.recover(record.name, authorize=True)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual((host / 'state.json').read_bytes(), before)

    def test_managed_session_outside_context_refuses_before_any_effect_and_legacy_still_saves(self):
        from validation.lifecycle.fixture import create
        fixture = create(self.base / 'context-fixture')
        host, pin = Path(fixture['host']), fixture['policy_sha']
        legacy = core.Session(host, pin)
        with legacy.locked() as s:
            s.initialize(json.loads((host / 'handoff.json').read_bytes()))
        with patch.object(core.tempfile, 'mkstemp', wraps=core.tempfile.mkstemp) as anonymous:
            legacy.persist()
        self.assertEqual(anonymous.call_count, 1)
        namespace = owned.ManagedTemporaryNamespace(host, pin, exclusive_namespace=True)
        session = core.Session(host, pin, temporary_namespace=namespace)
        with session.locked(): pass
        before_state = copy.deepcopy(session.state)
        before_flags = (session.accounting_trusted, session.accounting_durability, session._persistence_failed)
        before_files = {p.name:p.read_bytes() for p in host.iterdir() if p.is_file()}
        with patch.object(core.tempfile, 'mkstemp', side_effect=AssertionError('managed session fell back to anonymous save')) as anonymous:
            for operation in (session.persist, lambda:session.charge(operation='observe'),
                              lambda:session.event('outside-context', {})):
                with self.assertRaisesRegex(ValueError, 'active matching locked context'): operation()
        anonymous.assert_not_called()
        self.assertEqual(session.state, before_state)
        self.assertEqual((session.accounting_trusted, session.accounting_durability, session._persistence_failed), before_flags)
        self.assertEqual({p.name:p.read_bytes() for p in host.iterdir() if p.is_file()}, before_files)
        with session.locked() as s: s.persist()

    def test_reserved_target_case_aliases_refuse_before_new_files(self):
        for reserved in ('LOCK','POLICY.JSON'):
            with self.assertRaises(ValueError): owned._target(reserved)
        namespace = self.namespace()
        with namespace.locked() as root:
            originals = {name:(self.host / name).read_bytes() for name in ('policy.json','lock','state.json')}
            identities = {name:owned._identity((self.host / name).stat()) for name in originals}
            root_identity = owned._identity(self.host.stat())
            # This is a real identity control on case-insensitive filesystems;
            # the rejection expectation also runs on case-sensitive POSIX hosts.
            for alias, name in (('POLICY.JSON','policy.json'),('LOCK','lock')):
                if (self.host / alias).exists():
                    self.assertTrue(os.path.samefile(self.host / alias, self.host / name))
            with patch.object(root, '_write_new', side_effect=AssertionError('reserved alias reached new write')) as write_new:
                for name in ('POLICY.JSON','Policy.Json','LOCK','Lock','lOcK',
                             '.LIFECYCLE-managed-other','.Lifecycle-OWNER-other'):
                    with self.subTest(name=name):
                        with self.assertRaises(ValueError): core.save(root, name, {'alias': True})
                write_new.assert_not_called()
            self.assertEqual(owned._identity(self.host.stat()), root_identity)
            self.assertEqual({name:(self.host / name).read_bytes() for name in originals}, originals)
            self.assertEqual({name:owned._identity((self.host / name).stat()) for name in originals}, identities)
            self.assertEqual(list(self.host.glob('.lifecycle-*')), [])

    def test_owner_reserved_target_case_aliases_are_retained(self):
        namespace, record, payload, owner = self.residue()
        with namespace.locked() as root:
            for name in ('POLICY.JSON','pOLicy.JSON','LOCK','lOcK','.LIFECYCLE-other'):
                owner['target'] = name; record.write_text(json.dumps(owner))
                with self.subTest(name=name):
                    with self.assertRaisesRegex(ValueError, 'reserved'): root.recover(record.name, authorize=True)
                self.assertTrue(record.exists()); self.assertTrue(payload.exists())
        self.assert_preserved()

    def test_existing_target_identity_cannot_alias_policy_or_lock(self):
        namespace = self.namespace()
        with namespace.locked() as root:
            original_stat = owned.os.stat
            for reserved in ('policy.json','lock'):
                info = (self.host / reserved).stat()
                def aliased_stat(name, *args, **kwargs):
                    if name == 'filesystem-alias' and kwargs.get('dir_fd') == root._descriptors[-1]: return info
                    return original_stat(name, *args, **kwargs)
                with patch.object(owned.os, 'stat', side_effect=aliased_stat), \
                     patch.object(root, '_write_new', side_effect=AssertionError('reserved identity reached new write')) as writing:
                    with self.assertRaisesRegex(ValueError, 'reserved target identity'):
                        core.save(root, 'filesystem-alias', {'alias': True})
                    writing.assert_not_called()
            self.assertEqual(list(self.host.glob('.lifecycle-*')), [])
        self.assert_preserved()

    def test_ascii_target_contract_allows_unicode_roots_and_payloads(self):
        host = self.base / 'host-\u76ee\u5f55'; host.mkdir()
        (host / 'policy.json').write_bytes(self.policy)
        namespace = owned.ManagedTemporaryNamespace(host, self.pin, exclusive_namespace=True)
        with namespace.locked() as root:
            core.save(root, 'state.json', {'unicode_payload':'\u4fdd\u7559'})
            self.assertEqual(core.load(root, 'state.json'), {'unicode_payload':'\u4fdd\u7559'})
            originals = {p.name:p.read_bytes() for p in host.iterdir() if p.is_file()}
            with patch.object(root, '_write_new', side_effect=AssertionError('Unicode target reached new write')) as writing:
                for name in ('st\u00e1te.json','pol\u0131cy.json','\u2113ock','\uff33\uff34\uff21\uff34\uff25.json','state name.json'):
                    with self.subTest(name=name):
                        with self.assertRaisesRegex(ValueError, 'ASCII managed target'): core.save(root, name, {})
                writing.assert_not_called()
            self.assertEqual({p.name:p.read_bytes() for p in host.iterdir() if p.is_file()}, originals)


if __name__ == '__main__': unittest.main()
