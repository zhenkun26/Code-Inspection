"""Finite delivery state machine with an explicitly simulated, local provider."""
import copy
import math
from interfaces.lifecycle.core import load, save, keys, text, digest, timestamp, now, require
from interfaces.lifecycle.merge import query as merge_query
from interfaces.lifecycle.review import evaluate as pr_evaluate
from datetime import timedelta

CAPABILITY={'deploy':'deployment','flag-set':'flags','pause':'flags','promote':'channels',
            'rollback':'recovery','forward-fix':'recovery'}

class LocalSimulation:
    """Host contract: read(), apply(plan), query(operation). No external side effects."""
    def __init__(self,s):
        self.s=s
        p=s.policy['delivery']
        keys(p,['enabled','capabilities','initial_cohort','observation','rollback_compatibility'])
        require(type(p['enabled']) is bool,'delivery enabled flag')
        require(set(p['capabilities'])=={'deployment','flags','channels','observation','recovery'},'capabilities explicit')
        for c in p['capabilities'].values():
            keys(c,['enabled','required']);require(type(c['enabled']) is bool and type(c['required']) is bool,'capability flags')
            require(not c['required'] or c['enabled'],'required capability cannot be disabled')
        self.config=p
    def read(self):
        value=load(self.s.root,'sim-provider.json')
        keys(value,['schema','simulation','revision','artifact','target','cohort','config','data_version','enabled','paused','channels','operations','capabilities','effective_at'])
        require(value['schema']=='local-delivery-simulation/v1' and value['simulation'] is True,'real providers unsupported by this adapter')
        require(type(value['revision']) is int and value['revision']>=0,'provider revision')
        require(timestamp(value['effective_at'])<=now(),'provider effective time is in the future')
        for n in ['artifact','target','cohort','config','data_version']:text(value[n])
        require(type(value['enabled']) is bool and type(value['paused']) is bool,'provider flags')
        require(isinstance(value['channels'],dict) and isinstance(value['operations'],dict) and isinstance(value['capabilities'],list),'provider state')
        return value
    def query(self,operation):
        state=self.read()
        return {'simulation':True,'operation':operation,'result':state['operations'].get(operation,{'result':'not_applied'}),'state':state}
    def apply(self,plan):
        state=self.read()
        if plan['id'] in state['operations']:return self.query(plan['id'])
        require(digest(state)==plan['expected_provider'],'HOLD: provider drift')
        a=plan['action'];v=plan['parameters'];cap=CAPABILITY[a]
        require(cap in state['capabilities'],'BLOCKED: provider capability absent')
        if a=='deploy':state.update(artifact=v['artifact'],enabled=False,paused=False,cohort=self.config['initial_cohort'])
        elif a=='flag-set':state.update(enabled=v['enabled'],cohort=v['cohort'])
        elif a=='pause':state['paused']=True # Freeze automation; never pretend exposure decreased.
        elif a=='promote':state['channels'][v['channel']]=v['artifact']
        elif a=='rollback':state.update(artifact=v['artifact'],enabled=False,paused=True)
        elif a=='forward-fix':
            pass # Records a handoff request, not code implementation or a deployed repair.
        state['revision']+=1;state['config']='local-config-'+str(state['revision']);state['effective_at']=now().isoformat()
        receipt={'result':'confirmed','simulation':True,'action':a,'parameters':v,'observed_at':now().isoformat(),
                 'artifact':state['artifact'],'target':state['target'],'cohort':state['cohort'],'config':state['config'],
                 'revision':state['revision'],'enabled':state['enabled'],'paused':state['paused'],
                 'limitation':'Local simulated state only; no remote propagation or health proof.'}
        state['operations'][plan['id']]=receipt
        save(self.s.root,'sim-provider.json',state)
        return self.query(plan['id'])


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
            done=[r for r in state['operations'].values() if r['action'] in actions and r['result']=='confirmed' and all(r[k]==state[k] for k in ['artifact','target'])]
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
    plans=s.state['delivery'].get('plans',{})
    for operation,receipt in state['operations'].items():
        proposal=plans.get(operation,{})
        if (receipt.get('action')=='deploy' and receipt.get('result')=='confirmed'
            and all(receipt.get(k)==state[k] for k in ['artifact','target'])
            and proposal.get('action')=='deploy' and proposal.get('subject')==s.state['subject']
            and proposal.get('policy_sha')==s.pin
            and proposal.get('parameters')=={'artifact':state['artifact']}
            and proposal.get('source')=={'merge_plan':merged}):
            return {'merge_plan':merged,'deployment_operation':operation}
    raise ValueError('HOLD: confirmed deployment of the current validated artifact required')


def plan(s,record):
    keys(record,['schema','action','parameters']);require(record['schema']=='delivery-plan/v1','delivery plan schema')
    action=record['action'];require(action in CAPABILITY,'unsupported delivery action')
    p=LocalSimulation(s);require(p.config['enabled'],'delivery module not enabled');c=p.config['capabilities'][CAPABILITY[action]]
    require(c['enabled'],'action capability not selected');state=p.read();v=record['parameters']
    if action in {'deploy','rollback','promote'}:
        keys(v,['artifact'],['channel'] if action=='promote' else []);text(v['artifact'])
    elif action=='flag-set':keys(v,['enabled','cohort']);require(type(v['enabled']) is bool,'enabled boolean');text(v['cohort'])
    elif action=='pause':keys(v,[])
    else:keys(v,['finding_ids','handoff_ref']);require(isinstance(v['finding_ids'],list) and v['finding_ids'],'finding IDs');text(v['handoff_ref'])
    if action in {'deploy','promote'} or (action=='flag-set' and v['enabled']):
        require(pr_evaluate(s)['recommendation']=='READY_FOR_NEXT','HOLD: unresolved PR evidence or finding')
    if action=='promote':text(v.get('channel'));require(v['artifact']==state['artifact'],'same tested artifact required; rebuild needs new deployment/evidence')
    source={}
    if action=='deploy':source={'merge_plan':merge_source(s,v['artifact'])}
    if action=='promote' or (action=='flag-set' and v['enabled']):source=deployment_source(s,state)
    if action=='rollback':require(p.config['rollback_compatibility'].get(v['artifact'])==state['data_version'],'BLOCKED: compatibility with current data not established')
    if action=='promote' or (action=='flag-set' and v['enabled'] and v['cohort']!=p.config['initial_cohort']):
        require(p.config['capabilities']['observation']['enabled'] and observation_status(s,p,state)['status']=='PASS','HOLD: valid observation required for promotion/expansion')
    s.charge();result={'action':action,'parameters':copy.deepcopy(v),'expected_provider':digest(state),'policy_sha':s.pin,'subject':s.state['subject'],'source':source,'simulation':True}
    result['id']=digest(result);s.state['delivery'].setdefault('plans',{})[result['id']]=result;s.event('delivery-plan',result);s.persist();return result


def execute(s,operation):
    p=LocalSimulation(s);plans=s.state['delivery'].get('plans',{});require(operation in plans,'unknown operation')
    proposal=plans[operation]
    # Query first: repeated operations never apply twice, even after a lost caller response.
    existing=p.query(operation)
    if existing['result']['result']=='confirmed':return existing
    require(proposal['subject']==s.state['subject'],'stale delivery subject')
    require(s.authorized('delivery-'+proposal['action'],operation),'BLOCKED: delivery action authority missing/revoked')
    require(digest(p.read())==proposal['expected_provider'],'HOLD: provider drift; replan before action')
    # Recheck time-sensitive conditions at execution, not only when proposing.
    if proposal['action'] in {'deploy','promote'} or (proposal['action']=='flag-set' and proposal['parameters']['enabled']):
        require(pr_evaluate(s)['recommendation']=='READY_FOR_NEXT','HOLD: unresolved PR evidence or finding')
    if proposal['action']=='deploy':
        require(proposal.get('source')=={'merge_plan':merge_source(s,proposal['parameters']['artifact'])},'HOLD: merge relation changed')
    if proposal['action']=='promote' or (proposal['action']=='flag-set' and proposal['parameters']['enabled']):
        require(proposal.get('source')==deployment_source(s,p.read()),'HOLD: deployment relation changed')
    if proposal['action']=='promote' or (proposal['action']=='flag-set' and proposal['parameters']['enabled'] and proposal['parameters']['cohort']!=p.config['initial_cohort']):
        require(observation_status(s,p,p.read())['status']=='PASS','HOLD: observation no longer valid')
    s.charge();s.state['delivery'].setdefault('requests',{})[operation]={'result':'pending'};s.persist()
    receipt=p.apply(proposal);s.state['delivery']['requests'][operation]=receipt['result'];s.event('delivery-receipt',receipt['result']);s.persist();return receipt
