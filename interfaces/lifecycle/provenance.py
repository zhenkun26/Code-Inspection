"""Optional build bindings checked by a policy-selected trusted host callback.

No signature verifier, remote authentication, loader or network client is bundled.
The host supplies callbacks out of band. Stored proofs describe an earlier check;
fresh eligibility requires a new callback result and never grants action authority.
"""
import copy
from concurrent.futures import CancelledError
import hashlib
import re

from interfaces.lifecycle import artifacts
from interfaces.lifecycle.core import digest, keys, now, require, subject, timestamp
from support.local_files import read_bytes

MAX_BYTES = 1024 * 1024
MAX_TEXT = 4096
MAX_BASIS = 8192
MAX_AGE_SECONDS = 365 * 24 * 60 * 60
MAX_RUN_ATTEMPT = 2147483647
ACTIONS = {'merge-execute', 'delivery-deploy', 'delivery-rollback'}
ACTION_SCOPES = {'delivery-promote': 'delivery-deploy',
                 'delivery-flag-enable': 'delivery-deploy'}
SELECTORS = {'verifier_id', 'trust_root_id', 'repository', 'issuer',
             'builder_id', 'build_environment'}
DESCRIPTOR_FIELDS = {'run_id', 'run_attempt', 'evidence_path', 'evidence_sha256'}
BINDING_FIELDS = {'schema', 'task_id', 'policy_sha', *SELECTORS, 'source_subject',
                  'candidate_tree', 'build_config_digest', 'artifact_sha256',
                  'size_bytes', 'run_id', 'run_attempt', 'action', 'intent_sha256'}
RESULT_FIELDS = {'schema', 'status', 'binding', 'evidence_sha256', 'built_at',
                 'checked_at', 'expires_at', 'revoked', 'basis'}


def _text(value, limit=MAX_TEXT):
    require(type(value) is str and 0 < len(value) <= limit and bool(value.strip())
            and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            'UNKNOWN: bounded provenance string required')


def _integer(value, low, high):
    require(type(value) is int and low <= value <= high,
            'UNKNOWN: bounded provenance integer required')


def _hash(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'UNKNOWN: canonical provenance SHA-256 required')


def _source(value):
    subject(value)
    for item in value.values():
        _text(item)


def _action(value):
    require(type(value) is str and value in ACTIONS | set(ACTION_SCOPES),
            'UNKNOWN: unsupported provenance action')


def config(s):
    if 'build_provenance' not in s.policy:
        return None
    value = s.policy['build_provenance']
    keys(value, {'schema', 'required_actions', 'max_age_seconds', *SELECTORS})
    require(value['schema'] == 'build-provenance-policy/v1',
            'UNKNOWN: build provenance policy schema')
    actions = value['required_actions']
    require(type(actions) is list and all(type(a) is str for a in actions)
            and len(actions) == len(set(actions)) and set(actions) <= ACTIONS,
            'UNKNOWN: build provenance policy action scope')
    for name in SELECTORS:
        _text(value[name])
    _integer(value['max_age_seconds'], 1, MAX_AGE_SECONDS)
    return copy.deepcopy(value)


policy_config = config


def required(s, action):
    value = config(s)
    return value is not None and ACTION_SCOPES.get(action, action) in value['required_actions']


def _descriptor(value):
    keys(value, DESCRIPTOR_FIELDS)
    _text(value['run_id'])
    _integer(value['run_attempt'], 1, MAX_RUN_ATTEMPT)
    _text(value['evidence_path'])
    artifacts.locator(value['evidence_path'])
    _hash(value['evidence_sha256'])


def _binding(value):
    keys(value, BINDING_FIELDS)
    require(value['schema'] == 'build-provenance-binding/v1',
            'UNKNOWN: build provenance binding schema')
    for name in SELECTORS | {'task_id', 'run_id'}:
        _text(value[name])
    for name in {'policy_sha', 'build_config_digest', 'artifact_sha256', 'intent_sha256'}:
        _hash(value[name])
    _source(value['source_subject'])
    require(type(value['candidate_tree']) is str
            and re.fullmatch('[0-9a-f]{40}', value['candidate_tree']) is not None
            and value['candidate_tree'] != '0' * 40,
            'UNKNOWN: provenance candidate tree OID required')
    _integer(value['size_bytes'], 0, artifacts.MAX_BYTES)
    _integer(value['run_attempt'], 1, MAX_RUN_ATTEMPT)
    _action(value['action'])


def expected(s, descriptor, source_subject, tree, content, action, intent):
    """Construct the exact request from policy and validated local associations."""
    value = config(s)
    require(value is not None, 'UNKNOWN: build provenance policy missing')
    _descriptor(descriptor)
    _source(source_subject)
    _action(action)
    require(content is not None, 'UNKNOWN: provenance requires artifact content proof')
    artifacts.validate(content, source_subject, tree, artifacts.identity(content))
    require(type(intent) is dict, 'UNKNOWN: provenance intent required')
    intent_value = {k: copy.deepcopy(v) for k, v in intent.items()
                    if k not in {'schema', 'id', 'build_provenance'}}
    proof = content['proof']
    request = {'schema': 'build-provenance-binding/v1',
               'task_id': s.policy['task_id'], 'policy_sha': s.pin,
               **{name: value[name] for name in SELECTORS},
               'source_subject': copy.deepcopy(source_subject), 'candidate_tree': tree,
               'build_config_digest': proof['build_config_digest'],
               'artifact_sha256': proof['sha256'], 'size_bytes': proof['size_bytes'],
               'run_id': descriptor['run_id'], 'run_attempt': descriptor['run_attempt'],
               'action': action, 'intent_sha256': digest(intent_value)}
    _binding(request)
    return copy.deepcopy(request)


def _time(value):
    _text(value, 128)
    try:
        return timestamp(value)
    except (ValueError, OverflowError):
        raise ValueError('UNKNOWN: build verifier timestamp invalid') from None


def _result(value, request, descriptor):
    keys(value, RESULT_FIELDS)
    require(value['schema'] == 'build-verifier-result/v1'
            and type(value['status']) is str
            and value['status'] in {'verified', 'unverified', 'unknown'},
            'UNKNOWN: build verifier result schema/status')
    _binding(value['binding'])
    require(value['binding'] == request, 'UNKNOWN: build verifier binding mismatch')
    _hash(value['evidence_sha256'])
    require(value['evidence_sha256'] == descriptor['evidence_sha256'],
            'UNKNOWN: build verifier evidence identity mismatch')
    require(type(value['revoked']) is bool, 'UNKNOWN: build verifier revocation boolean required')
    basis = value['basis']
    require(type(basis) is str and 0 < len(basis) <= MAX_BASIS and bool(basis.strip()),
            'UNKNOWN: bounded build verifier basis required')
    built, checked, expires = (_time(value[name]) for name in
                               ('built_at', 'checked_at', 'expires_at'))
    require(built <= checked <= expires, 'UNKNOWN: build verifier time order invalid')
    return built, checked, expires


def _proof(value):
    keys(value, {'schema', 'descriptor', 'result'})
    require(value['schema'] == 'build-provenance/v1', 'UNKNOWN: build provenance proof schema')
    _descriptor(value['descriptor'])
    result = value['result']
    require(type(result) is dict and type(result.get('binding')) is dict,
            'UNKNOWN: build verifier result required')
    _result(result, result['binding'], value['descriptor'])
    require(result['status'] == 'verified' and result['revoked'] is False,
            'UNKNOWN: stored verified build provenance required')
    require(result['binding']['run_id'] == value['descriptor']['run_id']
            and result['binding']['run_attempt'] == value['descriptor']['run_attempt'],
            'UNKNOWN: stored build run binding mismatch')


def require_binding(s, action, proof):
    """Check optional presence and shape; callers supply full context to fresh."""
    value = config(s)
    if proof is None:
        require(not required(s, action), 'UNKNOWN: required build provenance missing; replan')
        return
    require(value is not None, 'UNKNOWN: build provenance policy missing')
    _proof(proof)
    binding = proof['result']['binding']
    require(binding['policy_sha'] == s.pin and binding['task_id'] == s.policy['task_id']
            and binding['action'] == action
            and all(binding[name] == value[name] for name in SELECTORS),
            'UNKNOWN: stored provenance policy/action binding mismatch')


def _outcome(status, reason, proof=None):
    result = {'status': status,
              'check_status': {'verified': 'PASS', 'unverified': 'FAIL', 'unknown': 'UNKNOWN'}[status],
              'scope': 'trusted-host-verifier', 'reason': reason,
              'authorizes_action': False, 'cryptographic_verification': 'UNKNOWN',
              'remote_identity': 'UNKNOWN'}
    if proof is not None:
        result['proof'] = copy.deepcopy(proof)
    return result


def verify(s, descriptor, source_subject, tree, content, action, intent):
    """Read bounded evidence and invoke only the policy-selected host callable."""
    try:
        descriptor = copy.deepcopy(descriptor)
        request = expected(s, descriptor, source_subject, tree, content, action, intent)
        value = config(s)
        registry = getattr(s, 'verifiers', None)
        callback = registry.get(value['verifier_id']) if isinstance(registry, dict) else None
        if not callable(callback):
            return _outcome('unknown', 'Trusted host build verifier unavailable.')
        evidence = read_bytes(s.root, 'provenance-evidence/' + descriptor['evidence_path'],
                              limit=MAX_BYTES, single_link=True)
        require(type(evidence) is bytes and len(evidence) <= MAX_BYTES
                and hashlib.sha256(evidence).hexdigest() == descriptor['evidence_sha256'],
                'UNKNOWN: build provenance evidence unavailable or inconsistent')
        # Neither the expected binding nor Session is exposed to callback mutation.
        result = copy.deepcopy(callback(copy.deepcopy(request), evidence))
        built, checked, expires = _result(result, request, descriptor)
        current = now()
        require(checked <= current <= expires
                and (current - built).total_seconds() <= value['max_age_seconds'],
                'UNKNOWN: build verifier result is not current')
        if result['revoked'] or result['status'] == 'unverified':
            return _outcome('unverified', 'Trusted host build verifier did not verify this binding.')
        if result['status'] == 'unknown':
            return _outcome('unknown', 'Trusted host build verifier could not establish this binding.')
        proof = {'schema': 'build-provenance/v1', 'descriptor': descriptor, 'result': result}
        return _outcome('verified', 'Trusted host build verifier verified this binding.', proof)
    except CancelledError:
        raise
    except Exception:
        # Evidence, callback exception text and malformed result values are private.
        return _outcome('unknown', 'Build provenance verification unavailable or inconsistent.')


def validate(s, proof, source_subject, tree, content, action, intent):
    """Pure historical validation: no evidence reads, callbacks or current clock."""
    _proof(proof)
    request = expected(s, proof['descriptor'], source_subject, tree, content, action, intent)
    _result(proof['result'], request, proof['descriptor'])
    return copy.deepcopy(proof)


def capture(s, descriptor, source_subject, tree, content, action, intent):
    result = verify(s, descriptor, source_subject, tree, content, action, intent)
    require(result['status'] == 'verified', 'UNKNOWN: build provenance not verified; HOLD')
    return copy.deepcopy(result['proof'])


def fresh(s, proof, source_subject, tree, content, action, intent, *, return_observation=False):
    require_binding(s, action, proof)
    if proof is None:
        return None
    stored = validate(s, proof, source_subject, tree, content, action, intent)
    result = verify(s, stored['descriptor'], source_subject, tree, content, action, intent)
    require(result['status'] == 'verified', 'UNKNOWN: fresh build provenance not verified; HOLD')
    require(_time(result['proof']['result']['built_at']) == _time(stored['result']['built_at']),
            'UNKNOWN: build completion identity changed; replan')
    # The earlier intent remains immutable even when a callback returns a new check.
    # An executor may retain the fresh observation ephemerally across a slow host
    # checkpoint. The default historical proof and saved proposal stay unchanged.
    return copy.deepcopy(result['proof']) if return_observation else stored


def report(proof):
    """Historical description only; never claim a current verifier result."""
    if proof is not None:
        _proof(proof)
    return {'record_kind': 'legacy-unverified' if proof is None else 'stored-build-provenance',
            'current_status': 'UNKNOWN', 'cryptographic_verification': 'UNKNOWN',
            'remote_identity': 'UNKNOWN', 'authorizes_action': False,
            'limitation': 'Stored trusted host verifier result only; current verification was not run.'}
