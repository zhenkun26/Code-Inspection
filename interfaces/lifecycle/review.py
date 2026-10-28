"""PR evidence ingestion and host-adjudicated finding history. No model or publisher."""
import copy
from support.local_files import decode_json, read_bytes, digest as bytes_digest
from interfaces.lifecycle.core import keys, text, subject, digest, timestamp, now, STATES, require

def candidate(s, item):
    keys(item,['id','subject','location','trigger','impact','severity','confidence_basis','attribution','attribution_basis','source','evidence_refs'])
    subject(item['subject'])
    for key in ['id','location','trigger','impact','severity','confidence_basis','attribution_basis','source']:text(item[key])
    require(item['attribution'] in {'introduced','pre_existing','uncertain'},'attribution')
    require(isinstance(item['evidence_refs'],list) and item['evidence_refs'],'finding needs evidence references')
    f=s.state['findings'].setdefault(item['id'],{'id':item['id'],'observations':[],'decisions':[],
                    'fact':'candidate','disposition':'open','blocking':True,'alias':None})
    f['observations'].append(copy.deepcopy(item))
    f.update(fact='candidate',disposition='open',blocking=True,alias=None)
    # New observation can reopen; absence never closes anything.


def ingest(s, root, batch):
    keys(batch,['schema','evidence','findings'])
    require(batch['schema']=='review-batch/v1' and isinstance(batch['evidence'],list) and isinstance(batch['findings'],list),'batch schema')
    needs_round=bool(batch['findings']) or any(c['id']==e.get('check') and c['gate'] in {'semantic','security'} for e in batch['evidence'] if isinstance(e,dict) for c in s.policy['checks'])
    require(not needs_round or s.state['round_subject']==digest(s.state['subject']), 'REPLAN: register a review round for the current subject before review ingestion')
    s.charge()
    staged=copy.deepcopy(s.state)
    try:
        for e in batch['evidence']:
            keys(e,['id','check','subject','config','scope','status','complete','observed_at','expires_at',
                    'provider','run','job','attempt','tool_version','context_id','reviewer','raw'])
            subject(e['subject']);require(e['status'] in STATES,'unknown status')
            require(type(e['complete']) is bool and isinstance(e['scope'],list),'coverage')
            c=next((c for c in s.policy['checks'] if c['id']==e['check']),None)
            if c and c['stage']=='pr' and c['gate'] in {'semantic','security'}:
                require(e['subject']==s.state['subject'],'review evidence must belong to the registered current subject')
            for key in ['id','check','config','provider','run','job','attempt','tool_version']:text(e[key])
            require(isinstance(e['context_id'],str) and isinstance(e['reviewer'],str),'review identity')
            for x in e['scope']:text(x)
            timestamp(e['observed_at']);timestamp(e['expires_at'])
            keys(e['raw'],['path','sha256'])
            require(bytes_digest(read_bytes(root,e['raw']['path']))==e['raw']['sha256'],'raw evidence digest mismatch')
            # Import is a preserved observation, never a human approval.
            require(not any(old['id']==e['id'] for old in s.state['evidence']),'evidence ID already present; new attempt needs new ID')
            s.state['evidence'].append(copy.deepcopy(e))
        for f in batch['findings']:
            require(all(ref in {e['id'] for e in s.state['evidence']} for ref in f['evidence_refs']),'unknown finding evidence')
            candidate(s,f)
        s.event('ingest',{'batch_digest':digest(batch),'evidence_count':len(batch['evidence']),'findings_count':len(batch['findings'])})
        s.persist()
    except Exception:
        s.state=staged
        raise
    return evaluate(s,'pr')


def effective(s,e,check,identity):
    if e['subject']!=identity or e['config']!=check['config']:return 'UNKNOWN'
    if not e['complete'] or not set(check['scope'])<=set(e['scope']):return 'UNKNOWN'
    if not timestamp(e['observed_at'])<=now()<=timestamp(e['expires_at']):return 'UNKNOWN'
    if e['status']=='N_A':return 'N_A' if check['na_reason'] else 'UNKNOWN'
    return e['status']


def latest_evidence(s,check,identity):
    """Select each source's latest observation, independently of ingestion order.

    Observation timestamps are host-attested evidence, not an authentication
    mechanism. Opaque run/attempt IDs cannot establish an ordering. Conflicting
    assessments at the same instant remain UNKNOWN until a later observation.
    """
    groups={}
    for e in s.state['evidence']:
        if e['check']!=check['id'] or e['subject']!=identity:continue
        key=(e['provider'],e['job'],e['reviewer'],e['context_id'])
        observed=timestamp(e['observed_at'])
        if key not in groups or observed>groups[key][0]:groups[key]=(observed,[e])
        elif observed==groups[key][0]:groups[key][1].append(e)
    result=[]
    for key in sorted(groups):
        records=sorted(groups[key][1],key=lambda e:e['id'])
        assessments=set()
        for e in records:
            assessment={k:v for k,v in e.items() if k not in {'id','run','attempt','observed_at'}}
            assessment['expires_at']=timestamp(e['expires_at']).isoformat()
            assessment['scope']=sorted(set(e['scope']))
            assessments.add(digest(assessment))
        status=effective(s,records[0],check,identity) if len(assessments)==1 else 'UNKNOWN'
        result.append({'records':records,'status':status})
    return result


def checks(s,stage,identity):
    result=[]
    for c in s.policy['checks']:
        if c['stage']!=stage or not c['required']:continue
        if c['na_reason']:
            result.append({'id':c['id'],'status':'N_A','reason':c['na_reason']});continue
        latest=latest_evidence(s,c,identity)
        statuses=[group['status'] for group in latest]
        status=next((x for x in ['FAIL','BLOCKED','UNKNOWN','NOT_RUN'] if x in statuses), 'PASS' if statuses else 'NOT_RUN')
        result.append({'id':c['id'],'status':status,'evidence':[e['id'] for group in latest for e in group['records']]})
    return result


def evaluate(s,stage='pr',identity=None):
    identity=identity or s.state['subject'];results=checks(s,stage,identity);reasons=[]
    if stage=='pr' and s.state['round_subject']!=digest(s.state['subject']):reasons.append('current subject review round not registered')
    for f in s.state['findings'].values():
        if f['blocking'] and not f['alias']:reasons.append('unresolved finding '+f['id'])
    binding=digest(identity)
    if s.policy['risk']!='low' or not s.policy['low_auto']:
        if not s.authorized(stage+'-approve',binding):reasons.append('valid human decision missing')
    if s.policy['risk']=='high':
        experts=set()
        for c in s.policy['checks']:
            if c['stage']!='pr' or c['gate'] not in {'semantic','security'}:continue
            for group in latest_evidence(s,c,s.state['subject']):
                e=group['records'][0]
                if group['status']=='PASS' and e['reviewer'] in s.policy['reviewers'] and e['context_id']:
                    experts.add((e['reviewer'],e['context_id']))
        # Different assigned people/contexts; still no claim of statistical independence.
        if len({x[0] for x in experts})<s.policy['high_expert_count'] or len({x[1] for x in experts})<s.policy['high_expert_count']:reasons.append('high-risk expert coverage missing')
    return s.output(stage,results,reasons)


def adjudicate(s,record):
    keys(record,['schema','actor','subject','finding','fact','disposition','blocking','reason','evidence_refs','duplicate_of'])
    require(record['schema']=='finding-decision/v1' and record['actor'] in s.policy['reviewers'],'trusted coordinator required')
    require(record['subject']==s.state['subject'],'stale adjudication')
    require(record['fact'] in {'confirmed','refuted','insufficient','disputed'},'fact state')
    require(record['disposition'] in {'open','fixed_pending','resolved','declined','deferred','escalated'},'disposition')
    require(type(record['blocking']) is bool,'blocking flag');text(record['reason'])
    require(record['finding'] in s.state['findings'],'unknown finding')
    refs=record['evidence_refs'];require(isinstance(refs,list) and refs,'decision evidence required')
    valid=[]
    for ref in refs:
        e=next((e for e in s.state['evidence'] if e['id']==ref),None)
        c=next((c for c in s.policy['checks'] if e and c['id']==e['check']),None)
        require(c and effective(s,e,c,s.state['subject'])=='PASS','decision needs current complete verification evidence')
        valid.append(e)
    f=s.state['findings'][record['finding']]
    if not record['blocking']:
        require(record['fact']=='refuted' or record['disposition']=='resolved','deferral/reason alone cannot close blocker; waiver not supported')
    if record['disposition']=='resolved':require(record['fact']=='confirmed','resolved requires confirmed finding and reverification')
    target=record['duplicate_of']
    if target:
        require(target in s.state['findings'] and target!=f['id'] and not s.state['findings'][target]['alias'],'invalid duplicate target')
        # Keep both observations/decisions; canonical issue remains at least as blocking.
        s.state['findings'][target]['blocking'] |= record['blocking']
    s.charge();f['decisions'].append(copy.deepcopy(record));f.update(fact=record['fact'],disposition=record['disposition'],blocking=record['blocking'],alias=target or None)
    s.event('adjudicate',{'finding':f['id'],'record_digest':digest(record)});s.persist();return evaluate(s)


def advance(s,record):
    keys(record,['schema','subject','impact','reused_evidence'])
    require(record['schema']=='subject-change/v1','subject change schema');subject(record['subject']);text(record['impact'])
    require(isinstance(record['reused_evidence'],list),'reuse list')
    # Safe reference implementation conservatively invalidates all bindings on change.
    # Host may import a newly attested record after reviewing equivalence, never auto-relabel old PASS.
    require(not record['reused_evidence'],'direct identity relabel unsupported; import newly attested evidence preserving source')
    s.charge();old=s.state['subject'];s.state['subject']=record['subject'];s.state['round_subject']=None
    for f in s.state['findings'].values():
        if not f['blocking']:f.update(blocking=True,disposition='fixed_pending',fact='insufficient')
    s.event('advance',{'old':old,'new':record['subject'],'impact':record['impact']});s.persist();return evaluate(s)


def import_security(s,root,wrapper):
    """Consume existing B record as evidence, never as authorization or human approval."""
    keys(wrapper,['schema','check','record'])
    require(wrapper['schema']=='security-import/v1','security import schema')
    raw=read_bytes(root,wrapper['record']);b=decode_json(raw)
    require(isinstance(b,dict) and b.get('schema')=='security-evidence/v1' and b.get('authorizes_progress') is False,'B evidence required')
    require(b.get('status') in STATES,'B state')
    c=next((c for c in s.policy['checks'] if c['id']==wrapper['check'] and c['gate']=='security'),None);require(c,'security policy check required')
    request=b.get('request_binding',{});require(isinstance(request,dict) and request and request.get('expected_subject')==b.get('subject'),'B binding incomplete')
    require(b.get('config_digest')==c['config'],'B config mapping mismatch')
    e={'id':'B-'+bytes_digest(raw),'check':c['id'],'subject':b['subject'],'config':b['config_digest'],
        'scope':b.get('scope',[]),'status':b['status'],'complete':b.get('complete',False),
        'observed_at':b.get('observed_at_utc',now().isoformat()),'expires_at':request['valid_until_utc'],
        'provider':'supplied-security-evidence/v1','run':request['request_id'],'job':c['id'],'attempt':bytes_digest(raw),
        'tool_version':str(b.get('tool',request['expected_tool'])['version']),'context_id':'','reviewer':'',
        'raw':{'path':wrapper['record'],'sha256':bytes_digest(raw)}}
    findings=[]
    for f in b.get('findings',[]):
        findings.append({'id':'B-'+request['request_id']+'-'+f['id'],'subject':b['subject'],'location':f['location'],'trigger':f['trigger'],
            'impact':f['impact'],'severity':f['tool_severity'],'confidence_basis':'unverified tool candidate','attribution':'uncertain',
            'attribution_basis':'not established by tool output','source':e['id'],'evidence_refs':[e['id']]})
    return ingest(s,root,{'schema':'review-batch/v1','evidence':[e],'findings':findings})
