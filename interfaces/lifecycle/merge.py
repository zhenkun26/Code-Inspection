"""Controlled bare-Git reference adapter. No remote, checkout, hooks or target app."""
import configparser
import os
from pathlib import Path
import re
import shutil
import subprocess
from interfaces.lifecycle.core import load, keys, text, digest, require, now
from support.local_files import is_link, read_bytes
from support.git_runtime import git_environment
from interfaces.lifecycle.review import evaluate

class LocalGit:
    def __init__(self, config):
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
        for path in self.root.rglob('*'):require(not is_link(path),'repository symlinks unsupported')
        self.git=shutil.which('git');require(self.git is not None,'Git unavailable')
        self.env=git_environment()
    def run(self,*args,input=None):
        result = subprocess.run([self.git,'--git-dir',str(self.root),'-c','core.hooksPath='+os.devnull,
                               '-c','core.attributesFile='+os.devnull,
                               '-c','protocol.allow=never',*args],input=input.encode('utf-8') if isinstance(input,str) else input,
                               capture_output=True,env=self.env,timeout=10)
        # Git stdin is a byte protocol: text mode changes LF to CRLF on Windows.
        for stream in ('stdout','stderr'):
            value=getattr(result,stream)
            if isinstance(value,bytes):setattr(result,stream,value.decode('utf-8'))
        return result
    def oid(self,value):
        require(isinstance(value,str) and re.fullmatch('[0-9a-f]{40}',value),'full commit OID required')
        x=self.run('cat-file','-t',value);require(x.returncode==0 and x.stdout.strip()=='commit','commit unavailable')
        return value
    def target(self):
        symbolic=self.run('symbolic-ref','-q',self.ref);require(symbolic.returncode==1,'symbolic target unsupported')
        x=self.run('rev-parse','--verify',self.ref);require(x.returncode==0,'target unavailable');return self.oid(x.stdout.strip())


def prepare(s,request):
    keys(request,['schema','base','head','integration_artifact'])
    require(request['schema']=='merge-plan/v1','merge plan schema');text(request['integration_artifact'])
    require(evaluate(s)['recommendation']=='READY_FOR_NEXT','HOLD: PR gate incomplete')
    g=LocalGit(s.policy['merge']);base=g.oid(request['base']);head=g.oid(request['head'])
    require(base==s.state['subject']['base'] and head==s.state['subject']['head'],'PR/combination identity mismatch')
    require(g.target()==base,'HOLD: target drift before preparation')
    binding=digest({'repository':str(g.root),'base':base,'head':head,'policy_sha':s.pin})
    require(s.authorized('merge-prepare',binding),'BLOCKED: prepare permission missing')
    s.charge();x=g.run('merge-tree','--write-tree',base,head)
    if x.returncode!=0:
        s.event('merge-preparation-failed',{'base':base,'head':head,'returncode':x.returncode,'stdout':x.stdout[:8192],'stderr':x.stderr[:8192],'truncated':len(x.stdout)>8192 or len(x.stderr)>8192})
        s.persist();return {'status':'FAIL' if x.returncode==1 else 'UNKNOWN','recommendation':'HOLD','exit_code':x.returncode,'reason':'combination conflict' if x.returncode==1 else 'Git preparation error; not a proven product defect'}
    tree=x.stdout.splitlines()[0];require(re.fullmatch('[0-9a-f]{40}',tree),'unknown merge-tree response')
    c=g.run('commit-tree',tree,'-p',base,'-p',head,input='Isolated lifecycle candidate\n')
    require(c.returncode==0,'candidate creation failed');candidate=g.oid(c.stdout.strip())
    identity=dict(s.state['subject'],head=candidate,worktree_digest='tree:'+tree,artifact_digest=request['integration_artifact'])
    plan={'repository':str(g.root),'target_ref':g.ref,'method':'merge_commit','base':base,'head':head,
          'candidate':candidate,'tree':tree,'subject':identity,'source_subject':dict(s.state['subject']),'policy_sha':s.pin}
    plan['id']=digest(plan);s.state['merge']=plan;s.event('merge-prepare',{'plan':plan});s.persist()
    return {'status':'NOT_RUN','recommendation':'HOLD','plan':plan,'reason':'integration evidence and independent execution authority required'}


def preflight(s):
    plan=s.state['merge'];require(plan,'merge plan required')
    result=evaluate(s,'merge',plan['subject'])
    # Revalidate prior PR decisions and findings immediately before execution.
    prior=evaluate(s)
    if prior['recommendation']!='READY_FOR_NEXT':result['reasons'].append('PR gate no longer satisfied')
    g=LocalGit(s.policy['merge'])
    if g.target()!=plan['base']:result['reasons'].append('target drift')
    if plan['source_subject']!=s.state['subject']:result['reasons'].append('candidate stale')
    if not s.authorized('merge-execute',plan['id']):result['reasons'].append('merge execution authority missing')
    if s.state['budget']['operations']>=s.policy['budget']['max_operations']:result['reasons'].append('operation budget exhausted')
    if result['reasons']:result=s.output('merge',result['checks'],result['reasons'])
    return result


def query(s):
    plan=s.state['merge'];require(plan,'merge plan required');g=LocalGit(s.policy['merge']);actual=g.target()
    receipt=s.state['receipts'].get(plan['id'])
    state='confirmed' if actual==plan['candidate'] and receipt else 'not_applied' if actual==plan['base'] else 'unknown'
    return {'operation':plan['id'],'result':state,'actual_target':actual,'receipt':receipt,'source':'local-git-ref-read','simulation':False,'isolated_reference':True}


def execute(s):
    plan=s.state['merge'];require(plan,'merge plan required')
    if plan['id'] in s.state['receipts']:return query(s)
    require(preflight(s)['recommendation']=='READY_FOR_NEXT','HOLD: merge preflight not ready')
    s.charge();g=LocalGit(s.policy['merge'])
    # The expected-old OID is checked atomically by Git, unlike check-then-merge.
    s.state['receipts'][plan['id']]={'result':'pending','requested_at':now().isoformat(),'base':plan['base'],'candidate':plan['candidate']};s.persist()
    try:x=g.run('update-ref','--no-deref',plan['target_ref'],plan['candidate'],plan['base'])
    except subprocess.TimeoutExpired:
        s.state['receipts'][plan['id']]['result']='unknown';s.persist();return query(s)
    actual=g.target();receipt=s.state['receipts'][plan['id']]
    receipt.update(result='confirmed' if x.returncode==0 and actual==plan['candidate'] else 'failed' if actual==plan['base'] else 'unknown',
                   exit_code=x.returncode,actual_target=actual,observed_at=now().isoformat())
    s.event('merge-result',receipt);s.persist();return query(s)
