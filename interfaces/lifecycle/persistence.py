"""Policy-selected host checkpoint before a fresh external delivery apply.

No adapter is bundled. The trusted host qualifies the selected implementation
and storage configuration; matching records alone never provide a capability.
This operational handshake does not prove hardware or power-loss durability.
"""
import copy
from concurrent.futures import CancelledError
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from interfaces.lifecycle.core import digest, keys, require
from support.local_files import decode_json, is_link, read_bytes

IDENTITY = {'adapter_id', 'adapter_version', 'storage_target', 'config_sha256', 'qualification'}
MAX_BYTES = 1024 * 1024


def _selector(value):
    require(type(value) is str and len(value) <= 256
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+/-]*', value) is not None,
            'UNKNOWN: symbolic host persistence selector required')


def config(s):
    """Declarative selection only: never import code, open a report or run a CLI."""
    if 'persistence' not in s.policy:
        return None
    value = s.policy['persistence']
    keys(value, {'schema', *IDENTITY})
    require(value['schema'] == 'host-persistence-policy/v1',
            'UNKNOWN: host persistence policy schema')
    for name in IDENTITY - {'config_sha256'}:
        _selector(value[name])
    require(type(value['config_sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', value['config_sha256']) is not None,
            'UNKNOWN: host persistence configuration digest required')
    return copy.deepcopy(value)


def selected(s):
    """Require a matching host-installed operational adapter before charging."""
    s.require_usable()
    require(s._locked_active, 'HOLD: host persistence requires an active session lock')
    value = config(s)
    require(value is not None, 'UNKNOWN: target-qualified host persistence selection required')
    try:
        registry = getattr(s, 'persistence_adapters', None)
        adapter = registry.get(value['adapter_id']) if type(registry) is dict else None
        require(adapter is not None and callable(getattr(adapter, 'checkpoint', None)),
                'UNKNOWN: host persistence capability unavailable')
        identity = {name: getattr(adapter, name, None) for name in IDENTITY} if adapter is not None else {}
        require(all(type(identity.get(name)) is str and identity[name] == value[name] for name in IDENTITY),
                'UNKNOWN: host persistence identity mismatch')
    except Exception:
        raise ValueError('UNKNOWN: target-qualified host persistence capability unavailable or inconsistent') from None
    return value, adapter


def _root(s):
    locator = os.fspath(s.root)
    require(not is_link(locator), 'UNKNOWN: host persistence root link refused')
    info = os.stat(locator, follow_symlinks=False)
    require(stat.S_ISDIR(info.st_mode), 'UNKNOWN: host persistence root must be a directory')
    return {'locator': str(Path(locator).resolve(strict=True)),
            'device': info.st_dev, 'inode': info.st_ino}


def _read(s, name):
    from interfaces.lifecycle.owned_temporary import ManagedRoot
    return s.root.read(name) if type(s.root) is ManagedRoot else read_bytes(s.root, name, MAX_BYTES)


def _snapshot(s):
    require(s._locked_active, 'HOLD: host persistence requires an active session lock')
    root = _root(s)
    policy = _read(s, 'policy.json')
    require(hashlib.sha256(policy).hexdigest() == s.pin
            and digest(decode_json(policy)) == digest(s.policy),
            'UNKNOWN: host persistence policy pin changed')
    raw = _read(s, 'state.json')
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES,
            'UNKNOWN: bounded saved host state bytes required')
    require(digest(decode_json(raw)) == digest(s.state),
            'UNKNOWN: saved host state does not match pending session state')
    require(_root(s) == root, 'UNKNOWN: host persistence root changed during read')
    return root, raw


def checkpoint(s, proposal, request, selection, adapter):
    """Confirm the exact saved pending bytes; all uncertainty fences the session."""
    try:
        current, installed = selected(s)
        require(current == selection and installed is adapter,
                'UNKNOWN: host persistence selection changed')
        root, raw = _snapshot(s)
        saved = decode_json(raw)
        operation = proposal['id']
        pending = saved['delivery']['requests'][operation]
        require(pending.get('result') == 'pending' and digest(pending) == digest(request),
                'UNKNOWN: exact saved host delivery pending request required')
        binding = {'schema': 'host-persistence-checkpoint/v1',
                   **{name: selection[name] for name in IDENTITY},
                   'task_id': s.policy['task_id'], 'policy_sha': s.pin,
                   'operation': operation, 'request_digest': request['request_digest'],
                   'pending_request_sha256': digest(request),
                   'state_sha256': hashlib.sha256(raw).hexdigest(),
                   'state_size_bytes': len(raw), 'root': root, 'challenge': uuid.uuid4().hex}
        # The callback gets copied metadata and immutable bytes, never the Session.
        confirmation = adapter.checkpoint(copy.deepcopy(binding), raw)
        require(type(confirmation) is dict, 'UNKNOWN: structured host persistence confirmation required')
        keys(confirmation, {'schema', 'status', 'binding'})
        require(type(confirmation['schema']) is str
                and confirmation['schema'] == 'host-persistence-confirmation/v1'
                and type(confirmation['status']) is str
                and confirmation['status'] == 'confirmed'
                and type(confirmation['binding']) is dict
                and digest(confirmation['binding']) == digest(binding),
                'UNKNOWN: exact host persistence confirmation required')
        after_selection, after_adapter = selected(s)
        require(after_selection == selection and after_adapter is adapter,
                'UNKNOWN: host persistence selection changed during checkpoint')
        after_root, after_raw = _snapshot(s)
        require(after_root == root and after_raw == raw,
                'UNKNOWN: saved host persistence checkpoint changed')
    except BaseException as exc:
        s._persistence_failed = True
        s.accounting_durability = 'unknown'
        if isinstance(exc, CancelledError):
            raise CancelledError('UNKNOWN: host persistence checkpoint cancelled; locked reload required') from None
        if isinstance(exc, Exception):
            raise ValueError('UNKNOWN: host persistence checkpoint unconfirmed; locked reload required') from None
        raise


def readiness(s):
    try:
        selected(s)
    except ValueError:
        return {'id': 'host-persistence', 'status': 'UNKNOWN',
                'reason': 'Fresh host apply requires a matching target-qualified persistence capability.'}
    return {'id': 'host-persistence', 'status': 'PASS',
            'scope': 'Host adapter available; each fresh apply still requires its own exact pending checkpoint.'}
