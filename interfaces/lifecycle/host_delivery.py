"""Policy-selected trusted-host deployment contract; no provider is bundled.

The host installs objects out of band and must implement conditional apply,
operation idempotency and durable query. Typed receipts check consistency, not
provider authenticity, atomicity, propagation, health, or callback isolation.
Only deployment and rollback are supported. Historical recovery never reopens
artifact bytes, asks for authority, or reruns build verification.
"""
import copy
import re
from concurrent.futures import CancelledError

from interfaces.lifecycle.core import digest, keys, now, require, subject, timestamp
from interfaces.lifecycle import artifacts, provenance
from interfaces.lifecycle import delivery as local

ACTIONS = {'deploy', 'rollback'}
GUARANTEES = {'conditional_apply', 'durable_idempotency'}
CAPABILITIES = {'deployment', 'recovery'} | GUARANTEES
TERMINAL = {'confirmed', 'not_applied', 'failed'}
STATUSES = TERMINAL | {'pending', 'unknown'}
STATE_FIELDS = {'schema', 'provider_id', 'provider_version', 'target', 'revision',
                'artifact', 'cohort', 'config', 'data_version', 'enabled', 'paused',
                'capabilities', 'observed_at'}
PROVIDER_FIELDS = {'provider_id', 'provider_version', 'target'}
LIMITATION = ('Trusted host contract evidence only; no provider authentication, '
              'atomicity, propagation, production health, or online acceptance proof.')


def _text(value):
    require(type(value) is str and 0 < len(value) <= 4096 and bool(value.strip())
            and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            'UNKNOWN: bounded host delivery string required')


def _selector(value):
    require(type(value) is str and len(value) <= 256
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+/-]*', value) is not None,
            'UNKNOWN: symbolic host provider selector required')


def _time(value):
    _text(value)
    try:
        return timestamp(value)
    except (ValueError, OverflowError):
        raise ValueError('UNKNOWN: host delivery timestamp invalid') from None


def _hash(value):
    local.hash_value(value)


def config(s):
    """Validate only declarative policy; never load code or select a default."""
    if 'delivery_provider' not in s.policy:
        return None
    value = s.policy['delivery_provider']
    keys(value, {'schema', *PROVIDER_FIELDS, 'max_age_seconds', 'guarantees'})
    require(value['schema'] == 'host-delivery-policy/v1',
            'UNKNOWN: host delivery policy schema')
    for name in PROVIDER_FIELDS:
        _selector(value[name])
    require(type(value['max_age_seconds']) is int
            and 1 <= value['max_age_seconds'] <= 365 * 24 * 60 * 60,
            'UNKNOWN: bounded host state freshness required')
    guarantees = value['guarantees']
    require(type(guarantees) is list and all(type(x) is str for x in guarantees)
            and len(guarantees) == len(set(guarantees))
            and set(guarantees) == GUARANTEES,
            'UNKNOWN: conditional apply and durable idempotency must be selected')
    return copy.deepcopy(value)


def selected(s):
    """Return only the host-installed object named by validated policy."""
    value = config(s)
    if value is None:
        return None
    registry = getattr(s, 'delivery_providers', None)
    provider = registry.get(value['provider_id']) if type(registry) is dict else None
    if provider is None:
        return None
    require(all(callable(getattr(provider, name, None)) for name in ('read', 'apply', 'query')),
            'UNKNOWN: trusted host delivery provider contract unavailable')
    return provider


def _identity(value):
    return {name: copy.deepcopy(value[name]) for name in PROVIDER_FIELDS}


def _context(s):
    return {'schema': 'host-delivery-context/v1', 'task_id': s.policy['task_id'],
            'policy_sha': s.pin, **_identity(config(s))}


def _state(value, selection, fresh=False):
    keys(value, STATE_FIELDS)
    require(value['schema'] == 'host-delivery-state/v1',
            'UNKNOWN: host delivery state schema')
    require(_identity(value) == _identity(selection),
            'UNKNOWN: host provider identity/target mismatch')
    for name in PROVIDER_FIELDS:
        _selector(value[name])
    for name in ('revision', 'artifact', 'cohort', 'config', 'data_version'):
        _text(value[name])
    require(type(value['enabled']) is bool and type(value['paused']) is bool,
            'UNKNOWN: host delivery state flags')
    caps = value['capabilities']
    require(type(caps) is list and all(type(c) is str for c in caps)
            and len(caps) == len(set(caps)) and set(caps) <= CAPABILITIES,
            'UNKNOWN: host provider capabilities')
    require(GUARANTEES <= set(caps),
            'UNKNOWN: host conditional apply/idempotency guarantees unavailable')
    observed = _time(value['observed_at'])
    require(observed <= now(), 'UNKNOWN: host state observation is in the future')
    if fresh:
        require(0 <= (now() - observed).total_seconds() <= selection['max_age_seconds'],
                'UNKNOWN: host state observation is not fresh')
    return copy.deepcopy(value)


def _snapshot(value):
    # Observation time is evidence freshness, not a deployment-state revision.
    return {name: copy.deepcopy(value[name]) for name in STATE_FIELDS - {'observed_at'}}


def _read(s, provider):
    try:
        value = copy.deepcopy(provider.read(copy.deepcopy(_context(s))))
        return _state(value, config(s), fresh=True)
    except CancelledError:
        raise
    except Exception:
        raise ValueError('UNKNOWN: trusted host delivery state unavailable or inconsistent') from None


def _ledger(s):
    s.require_usable()
    ledger = s.state['delivery']
    keys(ledger, [], ['plans', 'requests', 'observations', 'host_results'])
    for name in ('plans', 'requests', 'host_results'):
        require(type(ledger.get(name, {})) is dict, 'UNKNOWN: delivery ' + name + ' map')
        for identifier in ledger.get(name, {}):
            _hash(identifier)
    require(type(ledger.get('observations', [])) is list, 'UNKNOWN: delivery observations list')
    return ledger


def _effect(s, proposal, state):
    return {'artifact': proposal['parameters']['artifact'], 'target': state['target'],
            'cohort': (s.policy['delivery']['initial_cohort']
                       if proposal['action'] == 'deploy' else state['cohort']),
            'data_version': state['data_version'], 'enabled': False,
            'paused': proposal['action'] == 'rollback'}


def validate_plan(s, operation):
    """Historical validation: no artifact reads, registry, verifier or authority."""
    ledger = _ledger(s)
    _hash(operation)
    require(operation in ledger.get('plans', {}), 'UNKNOWN: unknown host delivery operation')
    proposal = copy.deepcopy(ledger['plans'][operation])
    keys(proposal, {'schema', 'action', 'parameters', 'provider_snapshot', 'expected_provider',
                    'provider', 'policy_sha', 'task_id', 'subject', 'source',
                    'artifact_content', 'effect', 'id'}, ['build_provenance'])
    require(proposal['schema'] == 'host-delivery-proposal/v1',
            'UNKNOWN: host delivery proposal schema; explicit migration required')
    require(proposal['id'] == operation
            and operation == digest({k: v for k, v in proposal.items() if k != 'id'}),
            'UNKNOWN: host delivery proposal digest/map-key mismatch')
    require(type(proposal['action']) is str and proposal['action'] in ACTIONS,
            'UNKNOWN: unsupported host delivery action')
    local.parameters(proposal['action'], proposal['parameters'])
    require(proposal['policy_sha'] == s.pin and proposal['task_id'] == s.policy['task_id'],
            'UNKNOWN: host delivery policy/task mismatch; explicit migration required')
    selection = config(s)
    require(selection is not None, 'UNKNOWN: host delivery policy required')
    keys(proposal['provider'], PROVIDER_FIELDS)
    require(proposal['provider'] == _identity(selection), 'UNKNOWN: host delivery provider selection mismatch')
    subject(proposal['subject'])
    before = _state(proposal['provider_snapshot'], selection)
    _hash(proposal['expected_provider'])
    require(proposal['expected_provider'] == digest(_snapshot(before)),
            'UNKNOWN: host delivery expected state digest mismatch')
    p = local.LocalSimulation(s)
    local.action_state(p, proposal, before)
    require(digest(proposal['effect']) == digest(_effect(s, proposal, before)),
            'UNKNOWN: host delivery effect mismatch')
    source = proposal['source']
    keys(source, ['merge_plan'])
    _hash(source['merge_plan'])
    merged = local.historical_merge(s, source['merge_plan'])
    require(local.merge_receipt(s, merged)['result'] == 'confirmed',
            'HOLD: confirmed historical host delivery source required')
    if proposal['action'] == 'deploy':
        require(merged['source_subject'] == proposal['subject'],
                'UNKNOWN: host deployment source subject mismatch')
    require(proposal['artifact_content'] == merged.get('artifact_content'),
            'UNKNOWN: host delivery merge artifact mismatch')
    artifacts.validate(proposal['artifact_content'], merged['source_subject'],
                       merged['tree'], proposal['parameters']['artifact'])
    if 'build_provenance' in proposal:
        provenance.validate(s, proposal['build_provenance'], merged['source_subject'],
                            merged['tree'], proposal['artifact_content'],
                            'delivery-' + proposal['action'], proposal)
    return proposal


def request_intent(proposal, requested_at):
    request = {'schema': 'host-delivery-request/v1', 'operation': proposal['id'],
               'proposal_id': proposal['id'], 'task_id': proposal['task_id'],
               'policy_sha': proposal['policy_sha'], **copy.deepcopy(proposal['provider']),
               'action': proposal['action'], 'parameters': copy.deepcopy(proposal['parameters']),
               'expected_provider': proposal['expected_provider'],
               'expected_state': copy.deepcopy(proposal['provider_snapshot']),
               'effect': copy.deepcopy(proposal['effect']),
               'artifact_content': copy.deepcopy(proposal['artifact_content']),
               'requested_at': requested_at}
    request['request_digest'] = digest(request)
    return request


def validate_request(proposal, value):
    require(type(value) is dict, 'UNKNOWN: host delivery request required')
    expected = request_intent(proposal, value.get('requested_at'))
    keys(value, set(expected) | {'result'}, ['receipt_sha', 'observed_at'])
    require(digest({name: value[name] for name in expected}) == digest(expected),
            'UNKNOWN: host delivery request intent/digest mismatch')
    require(type(value['result']) is str and value['result'] in STATUSES,
            'UNKNOWN: host delivery request status')
    require(_time(proposal['provider_snapshot']['observed_at'])
            <= _time(value['requested_at']) <= now(),
            'UNKNOWN: host delivery request time order')
    if value['result'] in TERMINAL:
        require(set(value) == set(expected) | {'result', 'receipt_sha', 'observed_at'},
                'UNKNOWN: host delivery terminal request metadata')
        _hash(value['receipt_sha'])
        require(_time(value['observed_at']) >= _time(value['requested_at']),
                'UNKNOWN: host delivery request observation time')
    else:
        require(set(value) == set(expected) | {'result'},
                'UNKNOWN: host delivery incomplete request metadata')
    return copy.deepcopy(value)


def validate_result(s, proposal, request, value, fresh=False):
    require(type(value) is dict, 'UNKNOWN: host delivery result required')
    require(type(value.get('status')) is str and value['status'] in STATUSES,
            'UNKNOWN: host delivery result status')
    fields = {'schema', 'operation', 'request_digest', *PROVIDER_FIELDS, 'status', 'observed_at'}
    confirmed = value['status'] == 'confirmed'
    keys(value, fields | ({'after', 'consumed_content'} if confirmed else set()))
    require(value['schema'] == 'host-delivery-result/v1', 'UNKNOWN: host delivery result schema')
    require(value['operation'] == proposal['id']
            and value['request_digest'] == request['request_digest']
            and _identity(value) == proposal['provider'],
            'UNKNOWN: host delivery result request/provider binding mismatch')
    observed = _time(value['observed_at'])
    require(_time(request['requested_at']) <= observed <= now(),
            'UNKNOWN: host result observation time invalid')
    if fresh:
        require(0 <= (now() - observed).total_seconds() <= config(s)['max_age_seconds'],
                'UNKNOWN: host delivery result observation is not fresh')
    if confirmed:
        after = _state(value['after'], config(s))
        before = proposal['provider_snapshot']
        require(all(after[name] == item for name, item in proposal['effect'].items()),
                'UNKNOWN: host delivery result effect mismatch')
        require(after['revision'] != before['revision'],
                'UNKNOWN: host delivery confirmation requires changed revision')
        require(_time(request['requested_at']) <= _time(after['observed_at']) <= observed,
                'UNKNOWN: host delivery after-state time order')
        require(local.CAPABILITY[proposal['action']] in after['capabilities'],
                'UNKNOWN: host result action capability missing')
        consumed = value['consumed_content']
        keys(consumed, ['sha256', 'size_bytes'])
        require(type(consumed['size_bytes']) is int and consumed == {
            name: proposal['artifact_content']['proof'][name] for name in ('sha256', 'size_bytes')},
            'UNKNOWN: host delivery consumed bytes mismatch')
    if request['result'] in TERMINAL:
        require(value['status'] == request['result']
                and digest(value) == request['receipt_sha']
                and value['observed_at'] == request['observed_at'],
                'UNKNOWN: host delivery request/result contradiction')
    return copy.deepcopy(value)


def _operation(s, operation):
    proposal = validate_plan(s, operation)
    ledger = _ledger(s)
    request = (validate_request(proposal, ledger['requests'][operation])
               if operation in ledger.get('requests', {}) else None)
    result = None
    if operation in ledger.get('host_results', {}):
        require(request is not None, 'UNKNOWN: host result has no recorded request')
        result = validate_result(s, proposal, request, ledger['host_results'][operation])
        require(result['status'] in TERMINAL, 'UNKNOWN: nonterminal host result cannot be cached')
        require(request['result'] in TERMINAL, 'UNKNOWN: terminal host result requires terminal request')
    require(request is None or request['result'] not in TERMINAL or result is not None,
            'UNKNOWN: terminal host request has no result')
    return proposal, request, result


def _validate_attempt_links(s, ledger):
    # An unresolved request cannot disappear merely because its plan is damaged.
    for operation in set(ledger.get('requests', {})) | set(ledger.get('host_results', {})):
        proposal = ledger.get('plans', {}).get(operation)
        require(type(proposal) is dict, 'HOLD: delivery attempt has no valid plan')
        if proposal.get('schema') == 'host-delivery-proposal/v1':
            _operation(s, operation)
        else:
            require(proposal.get('schema') in {'delivery-proposal/v2', 'delivery-proposal/v3', 'delivery-proposal/v4'}
                    and operation not in ledger.get('host_results', {}),
                    'HOLD: delivery attempt plan schema is unknown')
            local.validated_operation(s, operation, local.LocalSimulation(s).read())


def _unsettled(s, target, except_operation=None):
    ledger = _ledger(s)
    _validate_attempt_links(s, ledger)
    for operation, raw in ledger.get('plans', {}).items():
        if operation == except_operation or type(raw) is not dict or raw.get('schema') != 'host-delivery-proposal/v1':
            continue
        proposal, request, result = _operation(s, operation)
        require(not (proposal['provider']['target'] == target and request is not None
                     and request['result'] in {'pending', 'unknown'} and result is None),
                'HOLD: prior same-target host delivery outcome is unresolved')


def _fresh(s, proposal, state):
    require(proposal['subject'] == s.state['subject'], 'HOLD: stale host delivery subject')
    local.action_state(local.LocalSimulation(s), proposal, state, fresh=True)
    require(digest(_snapshot(state)) == proposal['expected_provider'],
            'HOLD: host provider drift; replan before action')
    require(s.authorized('delivery-' + proposal['action'], proposal['id']),
            'BLOCKED: host delivery action authority missing/revoked')
    artifacts.require_binding(s, 'delivery-' + proposal['action'], proposal['artifact_content'])
    if proposal['action'] == 'deploy':
        require(local.pr_evaluate(s)['recommendation'] == 'READY_FOR_NEXT',
                'HOLD: unresolved PR evidence or finding')
        require(proposal['source'] == {'merge_plan': local.merge_source(s, proposal['parameters']['artifact'])},
                'HOLD: host deployment merge relation changed')
    local.fresh_provenance(s, proposal)


def plan(s, record):
    s.require_usable()
    keys(record, ['schema', 'action', 'parameters'],
         ['artifact_content', 'artifact_source_merge', 'build_provenance'])
    require(record['schema'] == 'delivery-plan/v1', 'UNKNOWN: host delivery plan schema')
    require(type(record['action']) is str and record['action'] in ACTIONS,
            'UNKNOWN: host delivery action unsupported; only deploy/rollback supported')
    local.parameters(record['action'], record['parameters'])
    require(('artifact_content' in record) == ('artifact_source_merge' in record)
            and ('artifact_content' not in record or record['action'] == 'rollback'),
            'UNKNOWN: rollback artifact descriptor and historical merge source required together')
    require('build_provenance' not in record or (record['action'] == 'rollback'
            and type(record['build_provenance']) is dict),
            'UNKNOWN: rollback build provenance descriptor required')
    require(record['action'] != 'rollback' or 'artifact_content' in record,
            'UNKNOWN: real rollback requires explicit artifact descriptor and historical source')
    selection = config(s)
    require(selection is not None, 'UNKNOWN: host delivery policy not selected')
    _ledger(s)
    _unsettled(s, selection['target'])
    artifacts.policy_config(s)
    provenance.config(s)
    provider = selected(s)
    require(provider is not None, 'UNKNOWN: trusted host delivery provider unavailable')
    state = _read(s, provider)
    local.action_state(local.LocalSimulation(s), record, state, fresh=True)
    action = record['action']
    artifact = record['parameters']['artifact']
    if action == 'deploy':
        require(local.pr_evaluate(s)['recommendation'] == 'READY_FOR_NEXT',
                'HOLD: unresolved PR evidence or finding')
        source = {'merge_plan': local.merge_source(s, artifact)}
        merged = local.historical_merge(s, source['merge_plan'])
        content = copy.deepcopy(merged.get('artifact_content'))
        require(content is not None, 'UNKNOWN: real deployment requires artifact content; replan merge')
        artifacts.fresh(s, 'delivery-deploy', content)
    else:
        _hash(record['artifact_source_merge'])
        source = {'merge_plan': record['artifact_source_merge']}
        merged = local.historical_merge(s, source['merge_plan'])
        require(local.merge_receipt(s, merged)['result'] == 'confirmed',
                'HOLD: confirmed historical rollback source required')
        content, _ = artifacts.capture(s, record['artifact_content'], merged['source_subject'], merged['tree'])
        require(content == merged.get('artifact_content') and artifacts.identity(content) == artifact,
                'UNKNOWN: rollback content must match confirmed historical source')
    proposal = {'schema': 'host-delivery-proposal/v1', 'action': action,
                'parameters': copy.deepcopy(record['parameters']), 'provider_snapshot': state,
                'expected_provider': digest(_snapshot(state)), 'provider': _identity(selection),
                'policy_sha': s.pin, 'task_id': s.policy['task_id'],
                'subject': copy.deepcopy(s.state['subject']), 'source': source,
                'artifact_content': content}
    proposal['effect'] = _effect(s, proposal, state)
    descriptor = record.get('build_provenance')
    if descriptor is None and merged.get('build_provenance') is not None:
        descriptor = copy.deepcopy(merged['build_provenance']['descriptor'])
    if descriptor is not None:
        proposal['build_provenance'] = provenance.capture(s, descriptor, merged['source_subject'],
            merged['tree'], content, 'delivery-' + action, proposal)
    else:
        require(not provenance.required(s, 'delivery-' + action),
                'UNKNOWN: required build provenance descriptor missing')
    proposal['id'] = digest(proposal)
    s.charge(operation='delivery-plan')
    s.state['delivery'].setdefault('plans', {})[proposal['id']] = copy.deepcopy(proposal)
    s.event('delivery-plan', copy.deepcopy(proposal))
    s.persist()
    return copy.deepcopy(proposal)


def _unknown(proposal, request):
    return {'schema': 'host-delivery-result/v1', 'operation': proposal['id'],
            'request_digest': request['request_digest'], **copy.deepcopy(proposal['provider']),
            'status': 'unknown', 'observed_at': now().isoformat()}


def _output(proposal, result, state=None, historical=False):
    return {'simulation': False, 'authorizes_action': False, 'operation': proposal['id'], 'result': copy.deepcopy(result),
            'state': copy.deepcopy(state), 'current_state_status': 'PASS' if state is not None else 'UNKNOWN',
            'historical': historical, 'content_binding': artifacts.report(proposal['artifact_content']),
            'provenance_binding': provenance.report(proposal.get('build_provenance')),
            'limitation': LIMITATION}


def _record_result(s, proposal, request, result):
    status = result['status']
    stored = copy.deepcopy(request)
    stored['result'] = status
    if status in TERMINAL:
        stored.update(receipt_sha=digest(result), observed_at=result['observed_at'])
        s.state['delivery'].setdefault('host_results', {})[proposal['id']] = copy.deepcopy(result)
    s.state['delivery'].setdefault('requests', {})[proposal['id']] = stored
    s.event('delivery-host-result', {'operation': proposal['id'], 'status': status,
                                    'result_digest': digest(result)})
    # A persistence exception must escape: no successful return without durability.
    s.persist()


def _callback(s, provider, method, proposal, request, snapshot=None):
    try:
        intent = request_intent(proposal, request['requested_at'])
        if method == 'apply':
            artifacts.check_snapshot(snapshot, proposal['artifact_content'])
            raw = provider.apply(copy.deepcopy(intent), snapshot)
        else:
            raw = provider.query(copy.deepcopy(intent))
        return validate_result(s, proposal, request, copy.deepcopy(raw), fresh=(method == 'apply'))
    except CancelledError:
        raise
    except Exception:
        # Host exceptions and malformed callback payloads may contain secrets.
        return _unknown(proposal, request)


def query(s, operation):
    s.require_usable()
    proposal, request, result = _operation(s, operation)
    if result is not None:
        # Exact durable historical result; deliberately no registry lookup/read.
        return _output(proposal, result, historical=True)
    if request is None:
        return _output(proposal, {'status': 'not_applied', 'reason': 'No recorded host delivery attempt.'})
    provider = selected(s)
    if provider is None:
        return _output(proposal, _unknown(proposal, request))
    result = _callback(s, provider, 'query', proposal, request)
    _record_result(s, proposal, request, result)
    state = None
    try:
        state = _read(s, provider)
    except ValueError:
        pass
    return _output(proposal, result, state, historical=True)


def execute(s, operation):
    s.require_usable()
    proposal, request, result = _operation(s, operation)
    if request is not None or result is not None:
        return query(s, operation)
    _unsettled(s, proposal['provider']['target'], except_operation=operation)
    provider = selected(s)
    require(provider is not None, 'UNKNOWN: trusted host delivery provider unavailable')
    state = _read(s, provider)
    _fresh(s, proposal, state)
    snapshot = artifacts.fresh(s, 'delivery-' + proposal['action'], proposal['artifact_content'])
    artifacts.check_snapshot(snapshot, proposal['artifact_content'])
    s.charge(operation='delivery-execute')
    request = dict(request_intent(proposal, now().isoformat()), result='pending')
    s.state['delivery'].setdefault('requests', {})[operation] = copy.deepcopy(request)
    s.event('delivery-requested', {'operation': operation, 'provider': copy.deepcopy(proposal['provider'])})
    s.persist()  # Durable pending before the one and only apply callback.
    result = _callback(s, provider, 'apply', proposal, request, snapshot)
    _record_result(s, proposal, request, result)
    # Apply evidence is a historical operation result, not a fresh global read.
    return _output(proposal, result)


def evaluate(s):
    s.require_usable()
    p = local.LocalSimulation(s)
    if not p.config['enabled']:
        return s.output('post_merge', [{'id': 'module', 'status': 'NOT_RUN',
            'reason': 'explicitly not enabled; no online acceptance claimed'}], [])
    ledger = _ledger(s)
    _validate_attempt_links(s, ledger)
    state = None
    try:
        provider = selected(s)
        if provider is not None:
            state = _read(s, provider)
    except ValueError:
        pass
    receipts = []
    checks = []
    for operation, proposal in s.state['delivery'].get('plans', {}).items():
        if type(proposal) is dict and proposal.get('schema') == 'host-delivery-proposal/v1':
            intent, request, receipt = _operation(s, operation)
            if request is not None and request['result'] in {'pending', 'unknown'}:
                checks.append({'id': 'unresolved-operation:' + operation, 'status': 'UNKNOWN',
                               'reason': 'Same-target host delivery outcome remains unresolved.'})
            if receipt is not None and receipt['status'] == 'confirmed':
                receipts.append((intent, receipt))
    for capability, rule in p.config['capabilities'].items():
        if not rule['enabled']:
            checks.append({'id': capability, 'status': 'N_A', 'reason': 'explicit project opt-out'})
        elif not rule['required']:
            continue
        elif capability not in {'deployment', 'recovery'}:
            checks.append({'id': capability, 'status': 'UNKNOWN',
                           'reason': 'Host provider first scope supports deploy/rollback only.'})
        elif state is None:
            checks.append({'id': capability, 'status': 'UNKNOWN',
                           'reason': 'Fresh trusted host state unavailable.'})
        elif capability not in state['capabilities']:
            checks.append({'id': capability, 'status': 'BLOCKED',
                           'reason': 'Required host provider capability unavailable.'})
        else:
            actions = {'deploy', 'rollback'} if capability == 'deployment' else {'rollback'}
            done = any(intent['action'] in actions and _snapshot(receipt['after']) == _snapshot(state)
                       for intent, receipt in receipts)
            checks.append({'id': capability, 'status': 'PASS' if done else 'NOT_RUN',
                           'scope': LIMITATION})
    return s.output('post_merge', checks, ['unresolved finding ' + finding['id']
        for finding in s.state['findings'].values() if finding['blocking'] and not finding['alias']])
