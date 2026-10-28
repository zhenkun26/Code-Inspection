"""Finite delivery state machine with an explicitly simulated, local provider."""
import copy
import math
from interfaces.lifecycle.core import load, save, keys, text, subject, digest, timestamp, now, require
from interfaces.lifecycle.merge import query as merge_query, validate_plan as merge_validate, validate_receipt as merge_receipt
from interfaces.lifecycle.review import evaluate as pr_evaluate
from datetime import timedelta

CAPABILITY={'deploy':'deployment','flag-set':'flags','pause':'flags','promote':'channels',
            'rollback':'recovery','forward-fix':'recovery'}

PROVIDER_FIELDS={'schema','simulation','revision','artifact','target','cohort','config',
                 'data_version','enabled','paused','channels','capabilities','effective_at'}
RECEIPT_FLAT={'artifact','target','cohort','config','revision','enabled','paused'}
LIMITATION='Local simulated state only; no remote propagation or health proof.'


def provider_value(value, snapshot=False):
    keys(value, PROVIDER_FIELDS | ({'operations_sha'} if snapshot else set()))
    require(value['schema']=='local-delivery-simulation/v1' and value['simulation'] is True,
            'real providers unsupported by this adapter')
    require(type(value['revision']) is int and value['revision']>=0,'provider revision')
    require(timestamp(value['effective_at'])<=now(),'provider effective time is in the future')
    for name in ['artifact','target','cohort','config','data_version']:text(value[name])
    require(type(value['enabled']) is bool and type(value['paused']) is bool,'provider flags')
    require(isinstance(value['channels'],dict),'provider channels')
    for channel,artifact in value['channels'].items():text(channel);text(artifact)
    capabilities=value['capabilities']
    require(isinstance(capabilities,list) and all(isinstance(c,str) for c in capabilities)
            and len(capabilities)==len(set(capabilities))
            and set(capabilities)<=set(CAPABILITY.values())|{'observation'},'provider capabilities')
    if snapshot:hash_value(value['operations_sha'])
    return copy.deepcopy(value)


def hash_value(value):
    require(isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value),
            'canonical SHA256 required')


def projection(state):
    return {k:copy.deepcopy(state[k]) for k in PROVIDER_FIELDS}


def provider_snapshot(state):
    return dict(projection(state),operations_sha=digest(state['operations']))


def parameters(action,value):
    require(isinstance(action,str) and action in CAPABILITY,'unsupported delivery action')
    if action in {'deploy','rollback'}:
        keys(value,['artifact']);text(value['artifact'])
    elif action=='promote':
        keys(value,['artifact','channel']);text(value['artifact']);text(value['channel'])
    elif action=='flag-set':
        keys(value,['enabled','cohort']);require(type(value['enabled']) is bool,'enabled boolean');text(value['cohort'])
    elif action=='pause':keys(value,[])
    else:
        keys(value,['finding_ids','handoff_ref']);ids=value['finding_ids']
        require(isinstance(ids,list) and bool(ids),'finding IDs')
        for identifier in ids:text(identifier)
        require(len(ids)==len(set(ids)),'unique finding IDs');text(value['handoff_ref'])


def action_state(p,proposal,state,fresh=False):
    action=proposal['action'];value=proposal['parameters'];cap=CAPABILITY[action]
    require(cap in state['capabilities'],'BLOCKED: provider capability absent')
    if fresh:
        require(p.config['enabled'],'delivery module not enabled')
        require(p.config['capabilities'][cap]['enabled'],'action capability not selected')
    if action=='promote':require(value['artifact']==state['artifact'],'same tested artifact required; rebuild needs new deployment/evidence')
    if action=='rollback':
        require(p.config['rollback_compatibility'].get(value['artifact'])==state['data_version'],
                'BLOCKED: compatibility with current data not established')


def historical_merge(s,identifier):
    candidates=[s.state.get('merge',{})]
    for event in s.state.get('events',[]):
        require(isinstance(event,dict),'delivery source event record')
        if event.get('action')=='merge-prepare':
            require(isinstance(event.get('detail'),dict),'delivery source event detail')
            candidates.append(event['detail'].get('plan',{}))
    for candidate in candidates:
        if isinstance(candidate,dict) and candidate.get('id')==identifier:
            merged=merge_validate(s,candidate)[0]
            receipt=merge_receipt(s,merged)
            require(receipt is not None and receipt['result'] in {'pending','unknown','confirmed'},
                    'HOLD: validated historical merge attempt required')
            return merged
    raise ValueError('HOLD: validated historical merge source required')


def validate_plan(s,operation,provider,seen=None):
    """Return immutable historical intent; current eligibility is checked separately."""
    text(operation);plans=s.state['delivery'].get('plans',{})
    require(isinstance(plans,dict) and operation in plans,'unknown operation')
    proposal=copy.deepcopy(plans[operation])
    require(isinstance(proposal,dict) and proposal.get('schema')=='delivery-proposal/v2',
            'legacy record requires explicit replan/migration: delivery proposal')
    keys(proposal,['schema','action','parameters','expected_provider','provider_snapshot',
                   'policy_sha','subject','source','simulation','id'])
    require(proposal['id']==operation,'delivery operation/map-key mismatch')
    hash_value(operation)
    require(operation==digest({k:v for k,v in proposal.items() if k!='id'}),'delivery proposal digest mismatch')
    require(proposal['simulation'] is True,'delivery simulation required')
    require(proposal['policy_sha']==s.pin,'delivery policy mismatch');subject(proposal['subject'])
    parameters(proposal['action'],proposal['parameters'])
    snapshot=provider_value(proposal['provider_snapshot'],snapshot=True)
    require(proposal['expected_provider']==digest(snapshot),'delivery provider snapshot digest mismatch')
    p=LocalSimulation(s);action_state(p,proposal,snapshot)
    source=proposal['source'];action=proposal['action'];value=proposal['parameters']
    seen=set() if seen is None else set(seen)
    require(operation not in seen,'delivery source cycle');seen.add(operation)
    if action=='deploy':
        keys(source,['merge_plan']);text(source['merge_plan'])
        merged=historical_merge(s,source['merge_plan'])
        require(merged['source_subject']==proposal['subject'] and
                merged['subject']['artifact_digest']==value['artifact'],'delivery merge source mismatch')
    elif action=='promote' or (action=='flag-set' and value['enabled']):
        keys(source,['merge_plan','deployment_operation']);text(source['merge_plan']);hash_value(source['deployment_operation'])
        deployed,_,receipt=validated_operation(s,source['deployment_operation'],provider,seen)
        require(deployed['action']=='deploy' and receipt is not None
                and deployed['subject']==proposal['subject']
                and deployed['source']=={'merge_plan':source['merge_plan']}
                and all(receipt['after'][k]==snapshot[k] for k in ['artifact','target']),
                'HOLD: confirmed deployment source mismatch')
    else:keys(source,[])
    # The nonrecursive snapshot still protects the complete prior operation map.
    prior={}
    for identifier,receipt in provider['operations'].items():
        require(isinstance(receipt,dict) and isinstance(receipt.get('after'),dict)
                and type(receipt['after'].get('revision')) is int,'legacy record requires explicit replan/migration: provider receipt')
        if receipt['after']['revision']<=snapshot['revision']:prior[identifier]=receipt
    require(digest(prior)==snapshot['operations_sha'],'delivery prior provider operations mismatch')
    return proposal


def request_intent(proposal,requested_at):
    return {'schema':'delivery-request/v2','operation':proposal['id'],'proposal_id':proposal['id'],
            'policy_sha':proposal['policy_sha'],'expected_provider':proposal['expected_provider'],
            'action':proposal['action'],'parameters':copy.deepcopy(proposal['parameters']),
            'simulation':True,'target':proposal['provider_snapshot']['target'],
            'requested_at':requested_at,'result':'pending'}


def validate_request(proposal,request):
    require(isinstance(request,dict) and request.get('schema')=='delivery-request/v2',
            'legacy record requires explicit replan/migration: delivery request')
    expected=request_intent(proposal,request.get('requested_at'))
    keys(request,list(expected),['receipt_sha','observed_at'])
    require(request['result'] in {'pending','unknown','confirmed','failed'},'delivery request result')
    for key in expected:
        if key!='result':require(digest(request[key])==digest(expected[key]),'delivery request intent mismatch: '+key)
    require(timestamp(request['requested_at'])>=timestamp(proposal['provider_snapshot']['effective_at'])
            and timestamp(request['requested_at'])<=now(),'delivery request time')
    if request['result']=='confirmed':
        require(set(request)==set(expected)|{'receipt_sha','observed_at'},'delivery confirmed request metadata')
        hash_value(request['receipt_sha']);require(timestamp(request['observed_at'])>=timestamp(request['requested_at']),'delivery request observation time')
    else:require(set(request)==set(expected),'delivery incomplete request metadata')
    return copy.deepcopy(request)


def expected_after(p,proposal,observed_at):
    state={k:copy.deepcopy(proposal['provider_snapshot'][k]) for k in PROVIDER_FIELDS}
    a=proposal['action'];v=proposal['parameters']
    if a=='deploy':state.update(artifact=v['artifact'],enabled=False,paused=False,cohort=p.config['initial_cohort'])
    elif a=='flag-set':state.update(enabled=v['enabled'],cohort=v['cohort'])
    elif a=='pause':state['paused']=True
    elif a=='promote':state['channels'][v['channel']]=v['artifact']
    elif a=='rollback':state.update(artifact=v['artifact'],enabled=False,paused=True)
    # forward-fix records a handoff request, without changing deployment fields.
    state['revision']+=1;state['config']='local-config-'+str(state['revision']);state['effective_at']=observed_at
    return state


def validate_receipt(s,proposal,request,receipt,state):
    require(isinstance(receipt,dict) and receipt.get('schema')=='delivery-receipt/v2',
            'legacy record requires explicit replan/migration: delivery receipt')
    intent=request_intent(proposal,receipt.get('requested_at'))
    fields=(set(intent)-{'schema'})|RECEIPT_FLAT|{'schema','after','observed_at','limitation'}
    keys(receipt,fields)
    for key in set(intent)-{'schema','result'}:
        require(digest(receipt[key])==digest(intent[key]),'delivery receipt intent mismatch: '+key)
    require(receipt['result']=='confirmed','delivery provider receipt result')
    require(request is not None,'HOLD: delivery receipt has no recorded attempt')
    require(request['requested_at']==receipt['requested_at'] and request['result']!='failed',
            'delivery receipt/request contradiction')
    at=timestamp(receipt['observed_at'])
    require(timestamp(receipt['requested_at'])<=at<=now(),'delivery receipt observation time')
    after=provider_value(receipt['after'])
    require(digest(after)==digest(expected_after(LocalSimulation(s),proposal,receipt['observed_at'])),
            'delivery receipt result-state mismatch')
    for key in RECEIPT_FLAT:require(digest(receipt[key])==digest(after[key]),'delivery receipt projection mismatch: '+key)
    require(receipt['limitation']==LIMITATION,'delivery receipt limitation')
    if request['result']=='confirmed':
        require(request['receipt_sha']==digest(receipt) and request['observed_at']==receipt['observed_at'],
                'delivery receipt/request result mismatch')
    require(state['revision']>=after['revision'],'delivery provider revision predates receipt')
    if state['revision']==after['revision']:
        require(digest(projection(state))==digest(after),'delivery provider state disagrees with latest receipt')
    return copy.deepcopy(receipt)


def validated_operation(s,operation,state,seen=None):
    proposal=validate_plan(s,operation,state,seen)
    requests=s.state['delivery'].get('requests',{})
    require(isinstance(requests,dict),'delivery requests map')
    request=validate_request(proposal,requests[operation]) if operation in requests else None
    raw=state['operations'].get(operation)
    receipt=validate_receipt(s,proposal,request,raw,state) if operation in state['operations'] else None
    require(not (request is not None and request['result']=='confirmed' and receipt is None),
            'delivery confirmed request has no provider receipt')
    return proposal,request,receipt


class LocalSimulation:
    """Host contract: validated read/apply/query; no external side effects."""
    def __init__(self,s):
        self.s=s
        ledger=s.state['delivery'];keys(ledger,[],['plans','requests','observations'])
        for name in ['plans','requests']:
            require(isinstance(ledger.get(name,{}),dict),'delivery '+name+' map')
            for operation in ledger.get(name,{}):hash_value(operation)
        require(isinstance(ledger.get('observations',[]),list),'delivery observations list')
        p=s.policy['delivery']
        keys(p,['enabled','capabilities','initial_cohort','observation','rollback_compatibility'])
        require(type(p['enabled']) is bool,'delivery enabled flag')
        require(isinstance(p['capabilities'],dict),'delivery capabilities map')
        require(set(p['capabilities'])=={'deployment','flags','channels','observation','recovery'},'capabilities explicit')
        for c in p['capabilities'].values():
            keys(c,['enabled','required']);require(type(c['enabled']) is bool and type(c['required']) is bool,'capability flags')
            require(not c['required'] or c['enabled'],'required capability cannot be disabled')
        text(p['initial_cohort']);require(isinstance(p['rollback_compatibility'],dict),'rollback compatibility map')
        for artifact,version in p['rollback_compatibility'].items():text(artifact);text(version)
        self.config=p
    def read(self):
        value=load(self.s.root,'sim-provider.json')
        keys(value,PROVIDER_FIELDS|{'operations'})
        provider_value(projection(value))
        require(isinstance(value['operations'],dict),'provider operations map')
        for operation in value['operations']:hash_value(operation)
        return value
    def query(self,operation):
        state=self.read();proposal,request,receipt=validated_operation(self.s,operation,state)
        absent='not_applied' if digest(provider_snapshot(state))==proposal['expected_provider'] else 'unknown'
        if request and request['result']=='unknown':absent='unknown'
        return {'simulation':True,'operation':operation,'result':receipt if receipt is not None else
                {'result':absent},'state':state}
    def apply(self,plan):
        state=self.read();proposal,request,receipt=validated_operation(self.s,plan['id'],state)
        require(digest(plan)==digest(proposal),'delivery apply proposal mismatch')
        if receipt is not None:return self.query(proposal['id'])
        require(request is not None and request['result']=='pending','delivery pending attempt required')
        fresh_conditions(self.s,self,proposal,state)
        at=now().isoformat();after=expected_after(self,proposal,at)
        receipt={k:copy.deepcopy(v) for k,v in request.items() if k not in {'schema','result'}}
        receipt.update(schema='delivery-receipt/v2',result='confirmed',after=after,
                       observed_at=at,limitation=LIMITATION)
        receipt.update({k:copy.deepcopy(after[k]) for k in RECEIPT_FLAT})
        # Validate the constructed result before persisting any provider change.
        result_state=dict(after,operations=dict(state['operations'],**{proposal['id']:receipt}))
        validate_receipt(self.s,proposal,request,receipt,result_state)
        save(self.s.root,'sim-provider.json',result_state)
        return self.query(proposal['id'])


def observe(s,record):
    p=LocalSimulation(s);state=p.read();o=p.config['observation']
    keys(record,['schema','artifact','target','cohort','config','window_start','window_end','source','query','samples','value','complete','observed_at'])
    require(record['schema']=='observation/v1','observation schema');text(record['source']);text(record['query'])
    require(type(record['samples']) is int and record['samples']>=0,'samples')
    require(type(record['value']) in {int,float} and math.isfinite(record['value']),'finite metric required')
    require(type(record['complete']) is bool,'complete flag')
    for n in ['window_start','window_end','observed_at']:timestamp(record[n])
    require(p.config['enabled'] and p.config['capabilities']['observation']['enabled'],'observation not enabled')
    s.charge();s.state['delivery'].setdefault('observations',[]).append(copy.deepcopy(record));s.event('observe',{'record_digest':digest(record)});s.persist()
    return observation_status(s,p,state)


def observation_status(s,p,state):
    o=p.config['observation'];keys(o,['duration_seconds','min_samples','max_value','max_age_seconds','source','query'])
    require(type(o['duration_seconds']) in {int,float} and math.isfinite(o['duration_seconds']) and o['duration_seconds']>0,'finite duration required')
    start=timestamp(state['effective_at']);end=start+timedelta(seconds=o['duration_seconds'])
    require(start<end,'finite ordered window required')
    require(type(o['min_samples']) is int and o['min_samples']>0,'minimum samples')
    require(type(o['max_age_seconds']) is int and o['max_age_seconds']>0,'freshness limit')
    require(type(o['max_value']) in {int,float} and math.isfinite(o['max_value']),'finite limit')
    records=s.state['delivery'].get('observations',[])
    if not records:return {'id':'observation','status':'NOT_RUN','reason':'no observation'}
    r=records[-1]
    matched=all(r[k]==state[k] for k in ['artifact','target','cohort','config'])
    matched &= all(r[k]==o[k] for k in ['source','query'])
    matched &= timestamp(r['window_start'])==start and timestamp(r['window_end'])==end
    fresh=0<=(now()-timestamp(r['observed_at'])).total_seconds()<=o['max_age_seconds']
    complete=r['complete'] and r['samples']>=o['min_samples'] and timestamp(r['observed_at'])>=end
    if not matched or not fresh or not complete:return {'id':'observation','status':'UNKNOWN','reason':'identity/window/source/coverage/freshness incomplete'}
    if now()<end:return {'id':'observation','status':'NOT_RUN','reason':'window not finished'}
    return {'id':'observation','status':'PASS' if r['value']<=o['max_value'] else 'FAIL','samples':r['samples'],'source':r['source']}


def evaluate(s):
    p=LocalSimulation(s)
    if not p.config['enabled']:return s.output('post_merge',[{'id':'module','status':'NOT_RUN','reason':'explicitly not enabled; stage not entered; no online acceptance claimed'}],[])
    state=p.read();checks=[]
    receipts=[validated_operation(s,operation,state)[2] for operation in state['operations']]
    for name,c in p.config['capabilities'].items():
        if not c['enabled']:
            checks.append({'id':name,'status':'N_A','reason':'explicit project opt-out'});continue
        if not c['required']:continue
        if name not in state['capabilities']:checks.append({'id':name,'status':'BLOCKED','reason':'required provider capability unavailable'})
        elif name=='observation':checks.append(observation_status(s,p,state))
        else:
            actions=[a for a,cap in CAPABILITY.items() if cap==name]
            # Reuse only identities relevant to this capability, not every global revision.
            if name=='deployment':actions=['deploy','rollback']
            if name=='recovery':actions=['rollback'] # forward-fix receipt is a handoff, not a repaired deployment.
            done=[r for r in receipts if r is not None and r['action'] in actions and r['result']=='confirmed' and all(r[k]==state[k] for k in ['artifact','target'])]
            if name=='flags':done=[r for r in done if all(r[k]==state[k] for k in ['cohort','enabled','paused'])]
            if name=='channels':done=[r for r in done if state['channels'].get(r['parameters']['channel'])==state['artifact']]
            if name=='recovery':done=[r for r in done if p.config['rollback_compatibility'].get(state['artifact'])==state['data_version']]
            checks.append({'id':name,'status':'PASS' if done else 'NOT_RUN','scope':'simulated operation receipt only; no production health claim'})
    return s.output('post_merge',checks,['unresolved finding '+f['id'] for f in s.state['findings'].values() if f['blocking'] and not f['alias']])


def merge_source(s,artifact):
    """Bind a delivery action to this session's validated, confirmed combination."""
    merged=s.state['merge']
    require(merged and merged['source_subject']==s.state['subject'],'HOLD: current merge relation required')
    require(artifact==merged['subject']['artifact_digest'],'HOLD: artifact must match validated combination')
    require(merge_query(s)['result']=='confirmed','HOLD: actual merge receipt required')
    return merged['id']


def deployment_source(s,state):
    """Initial provider contents alone are not evidence of a validated deployment."""
    merged=merge_source(s,state['artifact'])
    for operation in state['operations']:
        proposal,_,receipt=validated_operation(s,operation,state)
        if (receipt is not None and proposal['action']=='deploy'
            and all(receipt['after'][k]==state[k] for k in ['artifact','target'])
            and proposal['subject']==s.state['subject']
            and proposal['parameters']=={'artifact':state['artifact']}
            and proposal['source']=={'merge_plan':merged}):
            return {'merge_plan':merged,'deployment_operation':operation}
    raise ValueError('HOLD: confirmed deployment of the current validated artifact required')


def fresh_conditions(s,p,proposal,state):
    require(proposal['subject']==s.state['subject'],'stale delivery subject')
    action_state(p,proposal,state,fresh=True)
    require(digest(provider_snapshot(state))==proposal['expected_provider'],'HOLD: provider drift; replan before action')
    require(s.authorized('delivery-'+proposal['action'],proposal['id']),
            'BLOCKED: delivery action authority missing/revoked')
    action=proposal['action'];v=proposal['parameters']
    if action in {'deploy','promote'} or (action=='flag-set' and v['enabled']):
        require(pr_evaluate(s)['recommendation']=='READY_FOR_NEXT','HOLD: unresolved PR evidence or finding')
    if action=='deploy':
        require(proposal['source']=={'merge_plan':merge_source(s,v['artifact'])},'HOLD: merge relation changed')
    if action=='promote' or (action=='flag-set' and v['enabled']):
        require(proposal['source']==deployment_source(s,state),'HOLD: deployment relation changed')
    if action=='promote' or (action=='flag-set' and v['enabled'] and v['cohort']!=p.config['initial_cohort']):
        require(p.config['capabilities']['observation']['enabled'] and observation_status(s,p,state)['status']=='PASS',
                'HOLD: observation no longer valid')


def plan(s,record):
    keys(record,['schema','action','parameters']);require(record['schema']=='delivery-plan/v1','delivery plan schema')
    action=record['action'];v=record['parameters'];parameters(action,v)
    p=LocalSimulation(s);state=p.read()
    action_state(p,record,state,fresh=True)
    for operation in state['operations']:validated_operation(s,operation,state)
    if action in {'deploy','promote'} or (action=='flag-set' and v['enabled']):
        require(pr_evaluate(s)['recommendation']=='READY_FOR_NEXT','HOLD: unresolved PR evidence or finding')
    source={}
    if action=='deploy':source={'merge_plan':merge_source(s,v['artifact'])}
    if action=='promote' or (action=='flag-set' and v['enabled']):source=deployment_source(s,state)
    if action=='promote' or (action=='flag-set' and v['enabled'] and v['cohort']!=p.config['initial_cohort']):
        require(p.config['capabilities']['observation']['enabled'] and observation_status(s,p,state)['status']=='PASS',
                'HOLD: valid observation required for promotion/expansion')
    snapshot=provider_snapshot(state)
    result={'schema':'delivery-proposal/v2','action':action,'parameters':copy.deepcopy(v),
            'expected_provider':digest(snapshot),'provider_snapshot':snapshot,'policy_sha':s.pin,
            'subject':copy.deepcopy(s.state['subject']),'source':source,'simulation':True}
    result['id']=digest(result)
    s.charge();s.state['delivery'].setdefault('plans',{})[result['id']]=result
    s.event('delivery-plan',result);s.persist();return copy.deepcopy(result)


def execute(s,operation):
    p=LocalSimulation(s);state=p.read();proposal,request,receipt=validated_operation(s,operation,state)
    # An existing exact attempt is queried, never replayed or charged again.
    if request is not None or receipt is not None:return p.query(operation)
    fresh_conditions(s,p,proposal,state)
    s.charge();request=request_intent(proposal,now().isoformat())
    s.state['delivery'].setdefault('requests',{})[operation]=request;s.persist()
    try:out=p.apply(proposal)
    except (ValueError,OSError):
        request['result']='unknown';s.persist();raise
    receipt=out['result']
    request.update(result='confirmed',receipt_sha=digest(receipt),observed_at=receipt['observed_at'])
    s.event('delivery-receipt',receipt);s.persist();return out
