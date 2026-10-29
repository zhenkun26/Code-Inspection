"""Optional scoped source snapshots; no build provenance or artifact bytes.

Fresh eligibility reads the host-selected source. Historical validation checks
stored associations only, so an exact attempted action never needs that source.
"""
import copy
from pathlib import Path

from interfaces.lifecycle.core import digest, keys, require, subject
from support import source_manifests as manifests


def policy_config(s):
    if 'source_content' not in s.policy:
        return None
    config = s.policy['source_content']
    keys(config, ['schema', 'root', 'config', 'required'])
    require(config['schema'] == 'source-content-policy/v1', 'UNKNOWN: source policy schema')
    require(type(config['required']) is bool, 'UNKNOWN: source policy required boolean')
    require(isinstance(config['root'], str) and Path(config['root']).is_absolute(),
            'UNKNOWN: trusted absolute source root required')
    manifests.validate_config(config['config'])
    return config


def validate(container, source_subject):
    """Pure stored-proof validation, independent of current policy/source files."""
    keys(container, ['schema', 'manifest', 'source_subject_digest'])
    require(container['schema'] == 'source-content/v1', 'UNKNOWN: source content schema')
    subject(source_subject)
    identity = manifests.identity(container['manifest'])
    require(container['source_subject_digest'] == digest(source_subject)
            and source_subject['worktree_digest'] == identity,
            'UNKNOWN: source manifest subject binding mismatch')
    manifest = container['manifest']
    if manifest['config']['mode'] == 'git-worktree':
        require(manifest['base'] == source_subject['base'] and manifest['head'] == source_subject['head'],
                'UNKNOWN: source Git identity mismatch')
    return identity


def fresh(s, container, source_subject):
    config = policy_config(s)
    if container is None:
        require(config is None or not config['required'], 'UNKNOWN: required source content proof missing')
        return None
    validate(container, source_subject)
    require(config is not None, 'UNKNOWN: source content policy missing')
    require(container['manifest']['config'] == config['config'], 'UNKNOWN: source manifest scope/config mismatch')
    arguments = {'base': source_subject['base'], 'head': source_subject['head']} \
        if config['config']['mode'] == 'git-worktree' else {}
    actual = manifests.capture(config['root'], config['config'],
                               metrics=getattr(s, 'metrics', None), **arguments)
    require(manifests.identity(actual) == source_subject['worktree_digest'],
            'UNKNOWN: source content changed or inconsistent; advance and review the new identity')
    return copy.deepcopy(container)


def capture(s, container, source_subject):
    # Supplied null is not an absent optional proof.
    validate(container, source_subject)
    return fresh(s, container, source_subject)


def build(s, source_subject):
    """Read-only capture suggesting a new subject; never initialize or approve."""
    subject(source_subject)
    config = policy_config(s)
    require(config is not None, 'UNKNOWN: source content policy missing')
    arguments = {'base': source_subject['base'], 'head': source_subject['head']} \
        if config['config']['mode'] == 'git-worktree' else {}
    manifest = manifests.capture(config['root'], config['config'],
                                 metrics=getattr(s, 'metrics', None), **arguments)
    suggested = dict(source_subject, worktree_digest=manifests.identity(manifest))
    container = {'schema': 'source-content/v1', 'manifest': manifest,
                 'source_subject_digest': digest(suggested)}
    return {'schema': 'source-capture-result/v1', 'subject': suggested, 'source_content': container,
            'current_source_status': 'PASS', 'build_provenance': 'UNKNOWN',
            'limitation': 'Scoped source snapshot only; other subject fields are host-declared, not verified by this capture.'}


def current(s):
    return fresh(s, s.state.get('source_content'), s.state['subject'])


def require_committed(container, source_subject):
    if container is None:
        return
    validate(container, source_subject)
    require(container['manifest']['config']['mode'] == 'git-worktree'
            and manifests.committed(container['manifest']),
            'UNKNOWN: source manifest does not represent the exact committed input; directory/dirty input cannot merge')


def report(container):
    """Response-only description; never verify the current source in recovery."""
    return {'record_kind': 'legacy-source-label' if container is None else 'scoped-source-manifest',
            'current_source_status': 'NOT_RUN', 'build_provenance': 'UNKNOWN',
            'stored_identity': None if container is None else manifests.identity(container['manifest'])}
