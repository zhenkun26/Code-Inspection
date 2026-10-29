"""One bounded file's bytes, not authenticated build provenance or a deployment.

The host controls policy, the artifact-files namespace and its writers. A path
locates bytes; identity is their SHA-256. Historical validation never opens it.
"""
from dataclasses import dataclass
import hashlib
import re

from interfaces.lifecycle.core import digest, keys, require, subject, text
from support.local_files import read_bytes

MAX_BYTES = 1024 * 1024
ACTIONS = {'merge-execute', 'delivery-deploy', 'delivery-rollback'}
PROOF_FIELDS = {'schema', 'algorithm', 'sha256', 'size_bytes', 'source_subject_digest',
                'candidate_tree', 'ci_config_digest', 'build_config_digest',
                'provenance', 'scope'}


def hash_value(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'UNKNOWN: canonical artifact SHA-256 required')


def locator(value):
    require(isinstance(value, str) and bool(value), 'UNKNOWN: artifact relative path required')
    parts = value.split('/')
    for part in parts:
        require(bool(part) and part not in {'.', '..'} and not part.endswith((' ', '.'))
                and not any(ord(c) < 32 or c in '\\:*?"<>|\x7f' for c in part),
                'UNKNOWN: artifact relative path required')
        require(re.fullmatch(r'CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]',
                             part.split('.')[0], re.IGNORECASE) is None,
                'UNKNOWN: reserved artifact path')
    return value


def policy_config(s):
    config = s.policy.get('artifact_content')
    if 'artifact_content' not in s.policy:
        return None
    keys(config, ['schema', 'required_actions', 'max_bytes', 'isolation'])
    require(config['schema'] == 'artifact-content-policy/v1', 'UNKNOWN: artifact policy schema')
    actions = config['required_actions']
    require(isinstance(actions, list) and all(isinstance(a, str) for a in actions)
            and len(actions) == len(set(actions)) and set(actions) <= ACTIONS,
            'UNKNOWN: artifact policy action scope')
    require(type(config['max_bytes']) is int and 0 < config['max_bytes'] <= MAX_BYTES,
            'UNKNOWN: artifact byte limit must be 1..1048576')
    require(isinstance(config['isolation'], str) and config['isolation'] in {'host-controlled', 'unconfirmed'},
            'UNKNOWN: artifact isolation declaration')
    return config


def required(s, action):
    config = policy_config(s)
    return config is not None and action in config['required_actions']


def require_binding(s, action, container):
    """Fresh eligibility only; never call from historical receipt recovery."""
    config = policy_config(s)
    if container is None:
        require(not required(s, action), 'UNKNOWN: required artifact content proof missing; replan')
        return
    _shape(container)
    require(config is not None and config['isolation'] == 'host-controlled',
            'UNKNOWN: artifact writer isolation not established by trusted host')
    require(container['proof']['size_bytes'] <= config['max_bytes'],
            'UNKNOWN: artifact exceeds policy byte limit')


def _shape(container):
    keys(container, ['proof', 'locator'])
    locator(container['locator'])
    proof = container['proof']
    keys(proof, PROOF_FIELDS)
    require(proof['schema'] == 'artifact-content/v1' and proof['algorithm'] == 'sha256',
            'UNKNOWN: unsupported artifact proof schema/algorithm')
    for name in ['sha256', 'source_subject_digest', 'build_config_digest']:
        hash_value(proof[name])
    require(type(proof['size_bytes']) is int and 0 <= proof['size_bytes'] <= MAX_BYTES,
            'UNKNOWN: artifact size invalid')
    require(isinstance(proof['candidate_tree'], str)
            and re.fullmatch('[0-9a-f]{40}', proof['candidate_tree']) is not None
            and proof['candidate_tree'] != '0' * 40, 'UNKNOWN: artifact tree OID required')
    text(proof['ci_config_digest'])
    require(proof['provenance'] == 'host-declared-association'
            and proof['scope'] == 'single-file-snapshot', 'UNKNOWN: artifact claim scope invalid')


def identity(container):
    _shape(container)
    return 'sha256:' + container['proof']['sha256']


def report(container):
    """Response-only historical description, never a fresh-file PASS."""
    if container is not None:
        _shape(container)
    proof = None if container is None else container['proof']
    return {'record_kind': 'legacy-label-only' if proof is None else 'single-file-content',
            'current_file_status': 'NOT_RUN', 'build_provenance': 'UNKNOWN',
            'stored_sha256': None if proof is None else proof['sha256'],
            'stored_size_bytes': None if proof is None else proof['size_bytes'],
            'limitation': 'Historical intent only; query does not reopen bytes or authenticate a build.'}


def validate(container, source_subject, tree, artifact_id):
    """Validate stored associations without re-reading historical bytes."""
    _shape(container)
    subject(source_subject)
    proof = container['proof']
    require(proof['source_subject_digest'] == digest(source_subject)
            and proof['candidate_tree'] == tree
            and proof['ci_config_digest'] == source_subject['ci_config_digest'],
            'UNKNOWN: artifact source/tree/config binding mismatch')
    require(artifact_id == identity(container), 'UNKNOWN: artifact content identity mismatch')


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    sha256: str
    size_bytes: int


def check_snapshot(snapshot, container):
    _shape(container)
    require(isinstance(snapshot, Snapshot) and type(snapshot.data) is bytes,
            'UNKNOWN: immutable artifact snapshot required')
    proof = container['proof']
    require(snapshot.sha256 == proof['sha256']
            and type(snapshot.size_bytes) is int and snapshot.size_bytes == proof['size_bytes']
            and len(snapshot.data) == proof['size_bytes']
            and hashlib.sha256(snapshot.data).hexdigest() == proof['sha256'],
            'UNKNOWN: artifact snapshot bytes mismatch')


def acquire(s, container):
    require_binding(s, 'snapshot', container)
    config = policy_config(s)
    # Keep artifact-files in the handle-relative traversal; never resolve it as
    # a separate root, which could hide a swapped intermediate link.
    data = read_bytes(s.root, 'artifact-files/' + container['locator'],
                      limit=config['max_bytes'], single_link=True)
    snapshot = Snapshot(data, hashlib.sha256(data).hexdigest(), len(data))
    check_snapshot(snapshot, container)
    return snapshot


def fresh(s, action, container):
    require_binding(s, action, container)
    return None if container is None else acquire(s, container)


def capture(s, descriptor, source_subject, tree):
    keys(descriptor, ['path', 'algorithm', 'sha256', 'size_bytes', 'build_config_digest'])
    subject(source_subject)
    container = {'locator': descriptor['path'], 'proof': {
        'schema': 'artifact-content/v1', 'algorithm': descriptor['algorithm'],
        'sha256': descriptor['sha256'], 'size_bytes': descriptor['size_bytes'],
        'source_subject_digest': digest(source_subject), 'candidate_tree': tree,
        'ci_config_digest': source_subject['ci_config_digest'],
        'build_config_digest': descriptor['build_config_digest'],
        'provenance': 'host-declared-association', 'scope': 'single-file-snapshot'}}
    snapshot = acquire(s, container)
    return container, snapshot
