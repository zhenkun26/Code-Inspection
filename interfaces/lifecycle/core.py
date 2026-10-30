"""Shared lifecycle records. Local trusted-host boundary, not a sandbox/auth server."""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import uuid
from support.local_files import checked_root, decode_json, read_bytes, require, root_lock, is_link

STATES = {'PASS', 'FAIL', 'NOT_RUN', 'BLOCKED', 'UNKNOWN', 'N_A'}
SUBJECT = {'base', 'head', 'worktree_digest', 'ci_config_digest', 'artifact_digest'}
OPERATIONS = {'begin-review', 'ingest', 'import-security', 'adjudicate', 'advance',
              'merge-prepare', 'merge-execute', 'delivery-plan', 'delivery-execute',
              'observe', 'unspecified'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def now(): return datetime.now(timezone.utc)
def timestamp(value):
    require(isinstance(value, str), 'timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'timezone required')
    return result.astimezone(timezone.utc)
def text(value): require(isinstance(value, str) and bool(value.strip()), 'nonempty string required')
def keys(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value) <= set(required)|set(optional), 'unknown/missing fields')
def subject(value):
    keys(value, SUBJECT)
    for v in value.values(): text(v)
    return value

def load(root, name):
    from interfaces.lifecycle.owned_temporary import ManagedRoot
    return decode_json(root.read(name) if type(root) is ManagedRoot else read_bytes(root, name))

def save(root, name, value):
    from interfaces.lifecycle.owned_temporary import ManagedRoot
    if type(root) is ManagedRoot: return root.save(name, value)
    require(isinstance(name,str) and bool(name) and not any(x in name for x in '/\\:') and name not in {'.', '..'}, 'flat state name required')
    if os.name == 'nt':
        from support.windows_files import component
        component(name)
    target = root/name
    require(not is_link(target) and not target.is_dir(), 'symlink state refused')
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()+b'\n'
    require(len(data) <= 1024*1024, 'ledger limit reached; archive under host control, do not reset')
    fd, path = tempfile.mkstemp(prefix='.lifecycle-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(path, target)
    finally:
        primary = sys.exc_info()[1]
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as cleanup:
            if primary is not None:
                raise primary from cleanup
            raise

class Session:
    def __init__(self, host_root, policy_sha, metrics=None, *, verifiers=None, delivery_providers=None,
                 temporary_namespace=None):
        self.metrics = metrics
        # Only trusted host Python code can supply capabilities; records cannot.
        require(verifiers is None or (type(verifiers) is dict and all(
            isinstance(k,str) and callable(v) for k,v in verifiers.items())),
            'trusted verifier registry required')
        self.verifiers = dict(verifiers or {})
        require(delivery_providers is None or (type(delivery_providers) is dict and all(
            isinstance(k,str) and bool(k.strip()) and all(callable(getattr(v,name,None))
                for name in ('read','apply','query')) for k,v in delivery_providers.items())),
            'trusted delivery provider registry required')
        self.delivery_providers = dict(delivery_providers or {})
        self.accounting_trusted = False
        self.accounting_durability = 'unknown'
        self.current_attempt = None
        self._persistence_failed = False
        self.root = checked_root(host_root)
        from interfaces.lifecycle.owned_temporary import ManagedTemporaryNamespace
        require(temporary_namespace is None or (type(temporary_namespace) is ManagedTemporaryNamespace
                and temporary_namespace.matches(host_root, policy_sha)),
                'matching trusted temporary namespace required')
        self.temporary_namespace = temporary_namespace
        raw = read_bytes(self.root, 'policy.json')
        require(hashlib.sha256(raw).hexdigest() == policy_sha, 'host policy pin mismatch')
        self.pin = policy_sha
        self.policy = decode_json(raw)
        p = self.policy
        keys(p, ['schema','task_id','risk','checks','budget','humans','reviewers','high_expert_count',
                 'low_auto','allowed_actions','merge','delivery'], ['artifact_content','source_content','build_provenance','delivery_provider'])
        require(p['schema']=='lifecycle-policy/v1', 'policy schema')
        text(p['task_id']); require(p['risk'] in {'low','medium','high'}, 'risk')
        require(type(p['low_auto']) is bool, 'low_auto boolean')
        for name in ['humans','reviewers','allowed_actions']:
            require(isinstance(p[name],list) and len(p[name])==len(set(p[name])), 'unique policy list')
            for item in p[name]: text(item)
        require(type(p['high_expert_count']) is int and p['high_expert_count']>=2, 'high-risk multiple experts required')
        keys(p['budget'],['max_rounds','max_operations'])
        for v in p['budget'].values():require(type(v) is int and v>0,'explicit positive budget')
        require(isinstance(p['checks'],list) and p['checks'], 'nonempty checks')
        ids=set()
        for c in p['checks']:
            keys(c,['id','stage','gate','scope','config','required','na_reason'],['applicability'])
            require(c['id'] not in ids,'duplicate check');ids.add(c['id']);text(c['id'])
            require(c['stage'] in {'pr','merge'},'check stage')
            require(c['gate'] in {'ci','semantic','security','integration'},'gate')
            require(type(c['required']) is bool,'required boolean');text(c['config'])
            require(isinstance(c['scope'],list) and c['scope'],'scope')
            for x in c['scope']:text(x)
            require(isinstance(c['na_reason'],str),'na reason')
        require({'ci','semantic','security'} <= {c['gate'] for c in p['checks'] if c['stage']=='pr' and c['required']}, 'PR gates must be explicit (N_A needs host applicability record)')
        require(any(c['stage']=='merge' and c['required'] for c in p['checks']), 'integration checks required')
        self.state = None

    @contextmanager
    def locked(self):
        original = self.root
        self.accounting_trusted = False
        self.accounting_durability = 'unknown'
        self.current_attempt = None
        context = self.temporary_namespace.locked() if self.temporary_namespace is not None else root_lock(original)
        with context as selected:
            self.root = selected
            try:
                raw = selected.read('policy.json') if self.temporary_namespace is not None else read_bytes(selected,'policy.json')
                require(hashlib.sha256(raw).hexdigest()==self.pin, 'host policy pin mismatch')
                try: state=load(selected,'state.json')
                except FileNotFoundError: state=None
                self.state=state
                if state is not None:
                    require(state['policy_sha']==self.pin and state['task_id']==self.policy['task_id'],'state/policy mismatch; explicit migration required')
                self._persistence_failed = False
                self.accounting_trusted = True
                self.accounting_durability = 'persisted' if state is not None else 'unknown'
                yield self
            finally:
                self.root = original

    def require_usable(self):
        require(not self._persistence_failed,
                'HOLD: session persistence failed; successful locked reload required before reuse')
        if self.temporary_namespace is not None:
            from interfaces.lifecycle.owned_temporary import ManagedRoot
            require(type(self.root) is ManagedRoot and self.root._active
                    and self.root._namespace is self.temporary_namespace and self.temporary_namespace._busy,
                    'HOLD: managed session requires active matching locked context')

    def event(self, action, detail):
        self.require_usable()
        event={'at':now().isoformat(),'action':action,'detail':detail}
        if self.current_attempt is not None:event['attempt_id']=self.current_attempt['attempt_id']
        self.state['events'].append(event)
    def persist(self):
        self.require_usable()
        self.accounting_durability = 'unknown'
        try:save(self.root,'state.json',self.state)
        except BaseException:
            self._persistence_failed = True
            raise
        self.accounting_durability = 'persisted'
    def charge(self, rounds=0, operation='unspecified'):
        self.require_usable()
        require(type(rounds) is int and rounds in {0,1},'round delta must be integer 0 or 1')
        require(type(operation) is str and operation in OPERATIONS,'known lifecycle operation required')
        require(self.state is not None,'initialize handoff first')
        used=self.state.get('budget')
        require(isinstance(used,dict) and all(type(used.get(name)) is int and used[name]>=0
                for name in ['operations','rounds']),'REPLAN: invalid budget usage; explicit migration required')
        require(type(self.state.get('events')) is list,
                'REPLAN: invalid attempt history; explicit migration required')
        limit=self.policy['budget']
        require(used['operations']+1<=limit['max_operations'] and used['rounds']+rounds<=limit['max_rounds'],'REPLAN: budget exhausted')
        attempt={'schema':'lifecycle-attempt/v1','attempt_id':uuid.uuid4().hex,
                 'sequence':used['operations']+1,'operation':operation,'round_delta':rounds}
        started={'at':now().isoformat(),'action':'attempt-started','detail':copy.deepcopy(attempt)}
        used['operations']+=1;used['rounds']+=rounds
        self.current_attempt=attempt
        self.state['events'].append(started)
        self.persist() # started attempts count even if later processing fails

    def initialize(self, handoff):
        self.require_usable()
        require(self.state is None,'session already initialized; no budget reset')
        keys(handoff,['schema','task_id','subject','budget','requirements_ref','history_ref','findings'],['source_content'])
        require(handoff['schema']=='pre-pr-handoff/v1' and handoff['task_id']==self.policy['task_id'],'handoff identity')
        subject(handoff['subject']);text(handoff['requirements_ref']);text(handoff['history_ref'])
        keys(handoff['budget'],['rounds','operations'])
        for v in handoff['budget'].values():require(type(v) is int and v>=0,'invalid inherited budget')
        require(handoff['budget']['rounds']<=self.policy['budget']['max_rounds'] and handoff['budget']['operations']<=self.policy['budget']['max_operations'],'inherited budget exceeds plan')
        require(isinstance(handoff['findings'],list),'findings list')
        from interfaces.lifecycle import source_content
        if 'source_content' in handoff:
            source = source_content.capture(self,handoff['source_content'],handoff['subject'])
        else:
            source = source_content.fresh(self,None,handoff['subject'])
        self.state={'schema':'lifecycle-state/v1','task_id':handoff['task_id'],'policy_sha':self.pin,
                    'subject':handoff['subject'],'budget':copy.deepcopy(handoff['budget']),
                    'handoff':handoff,'evidence':[],'findings':{},'events':[],'round_subject':None,
                    'merge':{},'receipts':{},'delivery':{}}
        if source is not None:self.state['source_content']=source
        from interfaces.lifecycle.review import candidate
        for item in handoff['findings']:candidate(self,item)
        self.event('initialize',{'handoff_digest':digest(handoff),'inherited_budget':handoff['budget']});self.persist()
        return {'status':'NOT_RUN','recommendation':'HOLD','subject_digest':digest(self.state['subject'])}

    def authority(self):
        # Host-owned side channel, never read decisions from tool payloads.
        self.require_usable()
        data=load(self.root,'authority.json');keys(data,['schema','decisions'])
        require(data['schema']=='host-authority/v1' and isinstance(data['decisions'],list),'authority schema')
        return data['decisions']
    def authorized(self, action, binding):
        if action not in self.policy['allowed_actions']:return False
        decisions=self.authority(); latest={}
        for d in decisions:
            keys(d,['id','actor','kind','action','binding','policy_sha','decision','expires_at'])
            text(d['id']);latest[d['id']]=d
        for d in latest.values():
            if (d['kind']=='human' and d['actor'] in self.policy['humans'] and d['action']==action
                and d['binding']==binding and d['policy_sha']==self.pin and d['decision']=='approve'
                and timestamp(d['expires_at'])>=now()): return True
        return False
    def output(self, stage, checks, reasons):
        bad=any(x.get('required',True) and x['status'] not in {'PASS','N_A'} for x in checks) or bool(reasons)
        exhausted=(self.state['budget']['operations']>=self.policy['budget']['max_operations'] or
                   self.state['budget']['rounds']>=self.policy['budget']['max_rounds'])
        recommendation='REPLAN' if bad and exhausted else 'HOLD' if bad else 'READY_FOR_NEXT'
        return {'stage':stage,'subject':self.state['subject'],'policy_sha':self.pin,'checks':checks,
                'reasons':reasons,'recommendation':recommendation,'budget':self.state['budget'],
                'authorizes_action':False}
