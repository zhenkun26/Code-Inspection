"""Controlled bare-Git reference adapter. No remote, checkout, hooks or target app."""
import configparser
import copy
import os
from pathlib import Path
import re
import shutil
import subprocess
from interfaces.lifecycle.core import load, keys, text, digest, require, now, subject, timestamp
from support.local_files import is_link, read_bytes
from support.git_runtime import git_environment
from interfaces.lifecycle.review import evaluate
from interfaces.lifecycle import artifacts, source_content, provenance

class LocalGit:
    def __init__(self, config, metrics=None):
        self.metrics=metrics
        keys(config,['repository','target_ref','method'])
        require(config['method']=='merge_commit','BLOCKED: only merge_commit supported')
        self.root=Path(config['repository']).absolute();self.ref=config['target_ref']
        require(not is_link(self.root) and self.root.is_dir(),'managed bare repository required')
        marker=load(self.root,'lifecycle-fixture.json')
        require(marker=={'schema':'isolated-local-git/v1','repository':str(self.root)},'explicit isolated provider marker required')
        require(re.fullmatch(r'refs/heads/[A-Za-z0-9_-]+',self.ref),'simple target ref required')
        # Refuse configuration capable of running programs, network access, includes or aliases.
        require(not (self.root/'commondir').exists(), 'shared Git commondir unsupported')
        cp=configparser.ConfigParser(interpolation=None)
        try: cp.read_string(read_bytes(self.root,'config').decode('utf-8'))
        except configparser.Error: raise ValueError('invalid managed Git config')
        require(set(cp.sections())=={'core'},'only bare core config supported')
        require(set(cp['core'])<= {'repositoryformatversion','filemode','bare','logallrefupdates','ignorecase','precomposeunicode','symlinks'},'unsafe repository config')
        require('symlinks' not in cp['core'] or cp['core']['symlinks']=='false','only disabled Git symlinks supported')
        require(cp['core'].get('bare')=='true' and cp['core'].get('repositoryformatversion')=='0','bare SHA-1 repository required')
        require(not (self.root/'objects/info/alternates').exists(),'object alternates unsupported')
        if self.metrics is not None:self.metrics.repository_scan()
        for path in self.root.rglob('*'):
            if self.metrics is not None:self.metrics.repository_entry()
            require(not is_link(path),'repository symlinks unsupported')
        self.git=shutil.which('git');require(self.git is not None,'Git unavailable')
        self.env=git_environment()
        self.env['GIT_NO_REPLACE_OBJECTS']='1'
    def run(self,*args,input=None):
        payload=input.encode('utf-8') if isinstance(input,str) else input
        if self.metrics is not None:self.metrics.git_attempt(merge_tree_write=args[:2]==('merge-tree','--write-tree'))
        result = subprocess.run([self.git,'--git-dir',str(self.root),'-c','core.hooksPath='+os.devnull,
                               '-c','core.attributesFile='+os.devnull,
                               '-c','protocol.allow=never',*args],input=payload,
                               capture_output=True,env=self.env,timeout=10)
        # Git stdin is a byte protocol: text mode changes LF to CRLF on Windows.
        for stream in ('stdout','stderr'):
            value=getattr(result,stream)
            if isinstance(value,bytes):setattr(result,stream,value.decode('utf-8'))
        return result
    def object_oid(self,value,kind):
        require(isinstance(value,str) and re.fullmatch('[0-9a-f]{40}',value) and value!='0'*40,'full nonzero '+kind+' OID required')
        x=self.run('cat-file','-t',value);require(x.returncode==0 and x.stdout.strip()==kind,kind+' unavailable')
        return value
    def oid(self,value):return self.object_oid(value,'commit')
    def target(self):
        symbolic=self.run('symbolic-ref','-q',self.ref);require(symbolic.returncode==1,'symbolic target unsupported')
        x=self.run('rev-parse','--verify',self.ref);require(x.returncode==0,'target unavailable');return self.oid(x.stdout.strip())


def prepare(s,request):
    s.require_usable()
    keys(request,['schema','base','head','integration_artifact'],['artifact_content','build_provenance'])
    require(request['schema']=='merge-plan/v1','merge plan schema');text(request['integration_artifact'])
    artifacts.policy_config(s)
    provenance.config(s)
    require(not provenance.required(s,'merge-execute') or 'build_provenance' in request,
            'UNKNOWN: required build provenance descriptor missing')
    require(not artifacts.required(s,'merge-execute') or 'artifact_content' in request,
            'UNKNOWN: artifact content proof required; replan with a file descriptor')
    require(evaluate(s)['recommendation']=='READY_FOR_NEXT','HOLD: PR gate incomplete')
    source=copy.deepcopy(s.state.get('source_content'))
    source_content.require_committed(source,s.state['subject'])
    g=LocalGit(s.policy['merge'],metrics=getattr(s,'metrics',None));base=g.oid(request['base']);head=g.oid(request['head'])
    require(base==s.state['subject']['base'] and head==s.state['subject']['head'],'PR/combination identity mismatch')
    require(g.target()==base,'HOLD: target drift before preparation')
    binding=digest({'repository':str(g.root),'base':base,'head':head,'policy_sha':s.pin})
    require(s.authorized('merge-prepare',binding),'BLOCKED: prepare permission missing')
    s.charge(operation='merge-prepare');x=g.run('merge-tree','--write-tree',base,head)
    if x.returncode!=0:
        s.event('merge-preparation-failed',{'base':base,'head':head,'returncode':x.returncode,'stdout':x.stdout[:8192],'stderr':x.stderr[:8192],'truncated':len(x.stdout)>8192 or len(x.stderr)>8192})
        s.persist();return {'status':'FAIL' if x.returncode==1 else 'UNKNOWN','recommendation':'HOLD','exit_code':x.returncode,'reason':'combination conflict' if x.returncode==1 else 'Git preparation error; not a proven product defect'}
    tree=x.stdout.splitlines()[0];require(re.fullmatch('[0-9a-f]{40}',tree),'unknown merge-tree response')
    c=g.run('commit-tree',tree,'-p',base,'-p',head,input='Isolated lifecycle candidate\n')
    require(c.returncode==0,'candidate creation failed');candidate=g.oid(c.stdout.strip())
    content=None
    if 'artifact_content' in request:
        content,_=artifacts.capture(s,request['artifact_content'],s.state['subject'],tree)
        require(request['integration_artifact']==artifacts.identity(content),
                'UNKNOWN: integration artifact must match the file SHA256 identity')
    identity=dict(s.state['subject'],head=candidate,worktree_digest='tree:'+tree,
                  artifact_digest=artifacts.identity(content) if content is not None else request['integration_artifact'])
    plan={'repository':str(g.root),'target_ref':g.ref,'method':'merge_commit','base':base,'head':head,
          'candidate':candidate,'tree':tree,'subject':identity,'source_subject':dict(s.state['subject']),'policy_sha':s.pin}
    if content is not None:plan.update(schema='merge-proposal/v2',artifact_content=content)
    if source is not None:plan.update(schema='merge-proposal/v3',source_content=source)
    if 'build_provenance' in request:
        plan['build_provenance']=provenance.capture(s,request['build_provenance'],
            plan['source_subject'],tree,content,'merge-execute',plan)
        plan['schema']='merge-proposal/v4'
    plan['id']=digest(plan);s.state['merge']=plan;s.event('merge-prepare',{'plan':plan});s.persist()
    return {'status':'NOT_RUN','recommendation':'HOLD','plan':plan,'reason':'integration evidence and independent execution authority required'}


def validate_plan(s,plan=None):
    """Validate historical intent and physical Git facts without current PR gates."""
    s.require_usable()
    if plan is None:plan=s.state['merge']
    require(plan,'merge plan required')
    keys(plan,['repository','target_ref','method','base','head','candidate','tree',
               'subject','source_subject','policy_sha','id'],['schema','artifact_content','source_content','build_provenance'])
    require((plan.get('schema')=='merge-proposal/v4' if 'build_provenance' in plan else
             plan.get('schema')=='merge-proposal/v3' if 'source_content' in plan else
             plan.get('schema')=='merge-proposal/v2' if 'artifact_content' in plan else
             'schema' not in plan),
            'merge content proposal schema mismatch')
    plan=copy.deepcopy(plan)
    for name in ['repository','target_ref','method','base','head','candidate','tree','policy_sha','id']:text(plan[name])
    subject(plan['subject']);subject(plan['source_subject'])
    require(plan['id']==digest({k:v for k,v in plan.items() if k!='id'}),'merge plan digest mismatch')
    g=LocalGit(s.policy['merge'],metrics=getattr(s,'metrics',None))
    require(plan['repository']==str(g.root) and plan['target_ref']==g.ref and
            plan['method']==s.policy['merge']['method'] and plan['policy_sha']==s.pin,'merge plan policy binding mismatch')
    for name in ['base','head','candidate']:g.oid(plan[name])
    g.object_oid(plan['tree'],'tree')
    source=plan['source_subject']
    require(source['base']==plan['base'] and source['head']==plan['head'],'merge source identity mismatch')
    expected=dict(source,head=plan['candidate'],worktree_digest='tree:'+plan['tree'],
                  artifact_digest=plan['subject']['artifact_digest'])
    require(plan['subject']==expected,'merge combination identity mismatch')
    if 'artifact_content' in plan:
        artifacts.validate(plan['artifact_content'],source,plan['tree'],plan['subject']['artifact_digest'])
    if 'build_provenance' in plan:
        provenance.validate(s,plan['build_provenance'],source,plan['tree'],
                            plan.get('artifact_content'),'merge-execute',plan)
    if 'source_content' in plan:
        source_content.require_committed(plan['source_content'],source)
        # This query returns an ASCII object ID, never arbitrary commit bytes.
        head=g.run('rev-parse','--verify',plan['head']+'^{tree}')
        require(head.returncode==0
                and head.stdout.strip()==plan['source_content']['manifest']['head_tree'],
                'source manifest committed tree mismatch')
    result=g.run('cat-file','commit',plan['candidate'])
    require(result.returncode==0,'candidate commit unavailable')
    headers=result.stdout.split('\n\n',1)[0].splitlines()
    tree_headers=[line for line in headers if line.split(' ',1)[0]=='tree']
    parent_headers=[line for line in headers if line.split(' ',1)[0]=='parent']
    require(tree_headers==['tree '+plan['tree']] and headers[0]==tree_headers[0],
            'candidate tree mismatch')
    require(parent_headers==['parent '+plan['base'],'parent '+plan['head']],
            'candidate ordered parents mismatch')
    return plan,g


def validate_receipt(s,plan):
    require(isinstance(s.state['receipts'],dict),'merge receipts mapping required')
    if plan['id'] not in s.state['receipts']:return None
    receipt=s.state['receipts'][plan['id']]
    require(isinstance(receipt,dict) and receipt.get('schema')=='merge-receipt/v2',
            'legacy or invalid merge receipt requires explicit migration')
    keys(receipt,['schema','operation','repository','target_ref','policy_sha','base','candidate','result','requested_at'],
         ['exit_code','actual_target','observed_at','diagnostics'])
    receipt=copy.deepcopy(receipt)
    for name in ['operation','repository','target_ref','policy_sha','base','candidate','result']:text(receipt[name])
    require(receipt['operation']==plan['id'] and all(receipt[name]==plan[name] for name in
            ['repository','target_ref','policy_sha','base','candidate']),'merge receipt intent mismatch')
    require(receipt['result'] in {'pending','unknown','confirmed','failed'},'merge receipt result invalid')
    timestamp(receipt['requested_at'])
    if 'exit_code' in receipt:
        require(type(receipt['exit_code']) is int,'merge receipt exit code invalid')
        require(receipt['exit_code']==0 or receipt['result']=='failed','known Git failure requires failed merge receipt')
    if 'actual_target' in receipt:
        require(isinstance(receipt['actual_target'],str) and re.fullmatch('[0-9a-f]{40}',receipt['actual_target']) and
                receipt['actual_target']!='0'*40,'merge receipt actual target invalid')
    if 'observed_at' in receipt:
        require(timestamp(receipt['observed_at'])>=timestamp(receipt['requested_at']),'merge receipt observation precedes request')
    if 'diagnostics' in receipt:text(receipt['diagnostics'])
    if receipt['result']=='confirmed':
        require(receipt.get('exit_code')==0 and receipt.get('actual_target')==plan['candidate'] and
                'observed_at' in receipt,'contradictory confirmed merge receipt')
    return receipt


def _preflight(s,plan,g,check_content=True):
    s.require_usable()
    result=evaluate(s,'merge',plan['subject'],source_identity=plan['source_subject'])
    # Revalidate prior PR decisions and findings immediately before execution.
    prior=evaluate(s)
    if prior['recommendation']!='READY_FOR_NEXT':result['reasons'].append('PR gate no longer satisfied')
    if g.target()!=plan['base']:result['reasons'].append('target drift')
    if plan['source_subject']!=s.state['subject']:result['reasons'].append('candidate stale')
    if plan.get('source_content')!=s.state.get('source_content'):result['reasons'].append('source content relation changed')
    combined=g.run('merge-tree','--write-tree',plan['base'],plan['head'])
    require(combined.returncode==0 and combined.stdout.splitlines() and combined.stdout.splitlines()[0]==plan['tree'],
            'merge combination tree mismatch or unavailable')
    if check_content:
        try:
            snapshot=artifacts.fresh(s,'merge-execute',plan.get('artifact_content'))
            if snapshot is not None:
                result['checks'].append({'id':'artifact-content','status':'PASS',
                                         'scope':'host-declared single-file snapshot association'})
        except (ValueError,OSError) as exc:
            result['checks'].append({'id':'artifact-content','status':'UNKNOWN','reason':str(exc)})
            result['reasons'].append('artifact content unavailable or inconsistent')
    if check_content:
        try:
            provenance.fresh(s,plan.get('build_provenance'),plan['source_subject'],
                             plan['tree'],plan.get('artifact_content'),'merge-execute',plan)
            if 'build_provenance' in plan:
                result['checks'].append({'id':'build-provenance','status':'PASS',
                                         'scope':'trusted-host-verifier contract'})
        except (ValueError,OSError):
            result['checks'].append({'id':'build-provenance','status':'UNKNOWN'})
            result['reasons'].append('build provenance unavailable or rejected')
    if not s.authorized('merge-execute',plan['id']):result['reasons'].append('merge execution authority missing')
    if s.state['budget']['operations']>=s.policy['budget']['max_operations']:result['reasons'].append('operation budget exhausted')
    if result['reasons']:result=s.output('merge',result['checks'],result['reasons'])
    return result


def preflight(s):
    s.require_usable()
    plan,g=validate_plan(s);receipt=validate_receipt(s,plan)
    return _preflight(s,plan,g,check_content=receipt is None)


def _query(plan,g,receipt):
    actual=g.target()
    state='confirmed' if actual==plan['candidate'] and receipt is not None and receipt['result'] in {'pending','unknown','confirmed'} else 'not_applied' if actual==plan['base'] else 'unknown'
    return {'operation':plan['id'],'result':state,'actual_target':actual,'receipt':receipt,
            'source':'local-git-ref-read','simulation':False,'isolated_reference':True,
            'content_binding':artifacts.report(plan.get('artifact_content')),
            'source_binding':source_content.report(plan.get('source_content')),
            'provenance_binding':provenance.report(plan.get('build_provenance'))}


def query(s):
    s.require_usable()
    plan,g=validate_plan(s);receipt=validate_receipt(s,plan)
    return _query(plan,g,receipt)


def execute(s):
    s.require_usable()
    plan,g=validate_plan(s);receipt=validate_receipt(s,plan)
    if receipt is not None:return _query(plan,g,receipt)
    require(_preflight(s,plan,g)['recommendation']=='READY_FOR_NEXT','HOLD: merge preflight not ready')
    s.charge(operation='merge-execute')
    # The expected-old OID is checked atomically by Git, unlike check-then-merge.
    receipt={'schema':'merge-receipt/v2','operation':plan['id'],
             **{name:plan[name] for name in ['repository','target_ref','policy_sha','base','candidate']},
             'result':'pending','requested_at':now().isoformat()}
    s.state['receipts'][plan['id']]=receipt
    s.event('merge-requested',{'operation':plan['id']});s.persist()
    try:x=g.run('update-ref','--no-deref',plan['target_ref'],plan['candidate'],plan['base'])
    except (subprocess.TimeoutExpired,OSError) as exc:
        receipt.update(result='unknown',diagnostics=type(exc).__name__+': '+str(exc)[:2048]);s.persist()
        return _query(plan,g,receipt)
    try:actual=g.target()
    except (ValueError,OSError,subprocess.TimeoutExpired) as exc:
        receipt.update(result='failed' if x.returncode!=0 else 'unknown',exit_code=x.returncode,
                       diagnostics=type(exc).__name__+': '+str(exc)[:2048]);s.persist()
        raise
    receipt.update(result='failed' if x.returncode!=0 or actual==plan['base'] else 'confirmed' if actual==plan['candidate'] else 'unknown',
                   exit_code=x.returncode,actual_target=actual,observed_at=now().isoformat())
    s.event('merge-result',receipt);s.persist();return _query(plan,g,receipt)
