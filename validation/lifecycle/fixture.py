"""Self-owned offline fixture builder. Only NEW destinations; no existing repositories."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from interfaces.lifecycle.core import digest,require
from support.git_runtime import git_environment


def write(root,name,value):
    (root/name).write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))

def create(destination):
    root=Path(destination).absolute();require(not root.exists(),'new fixture destination required');root.mkdir(parents=True)
    host=root/'host';inputs=root/'inputs';repo=root/'bare.git'
    host.mkdir();inputs.mkdir()
    git=shutil.which('git');require(git,'Git required')
    env=git_environment()
    subprocess.run([git,'init','--bare','--template=',str(repo)],check=True,capture_output=True,env=env,timeout=10)
    def g(*args,input=None):
        return subprocess.run([git,'--git-dir',str(repo),'-c','core.hooksPath='+os.devnull,*args],input=input.encode('utf-8') if isinstance(input,str) else input,capture_output=True,check=True,env=env,timeout=10).stdout.decode('utf-8').strip()
    blob=g('hash-object','-w','--stdin',input='self-owned baseline\n');tree=g('mktree',input='100644 blob '+blob+'\tnote.txt\n');base=g('commit-tree',tree,input='fixture base\n')
    blob2=g('hash-object','-w','--stdin',input='self-owned candidate\n');tree2=g('mktree',input='100644 blob '+blob2+'\tnote.txt\n');head=g('commit-tree',tree2,'-p',base,input='fixture candidate\n')
    g('update-ref','refs/heads/main',base)
    write(repo,'lifecycle-fixture.json',{'schema':'isolated-local-git/v1','repository':str(repo)})
    now=datetime.now(timezone.utc);start=(now-timedelta(minutes=2)).isoformat();end=(now-timedelta(minutes=1)).isoformat()
    checks=[dict(id=x,stage='pr',gate=x,scope=['self-owned-change'],config='check-config-1',required=True,na_reason='') for x in ['ci','semantic','security']]
    checks.append(dict(id='integration',stage='merge',gate='integration',scope=['combination'],config='integration-1',required=True,na_reason=''))
    policy={'schema':'lifecycle-policy/v1','task_id':'self-owned-fixture','risk':'medium','checks':checks,
        'budget':{'max_rounds':3,'max_operations':60},'humans':['test-human'],'reviewers':['reviewer-a','reviewer-b'],
        'high_expert_count':2,'low_auto':False,'allowed_actions':['pr-approve','merge-approve','merge-prepare','merge-execute',
        'delivery-deploy','delivery-flag-set','delivery-pause','delivery-promote','delivery-rollback','delivery-forward-fix'],
        'merge':{'repository':str(repo),'target_ref':'refs/heads/main','method':'merge_commit'},
        'delivery':{'enabled':True,'capabilities':{c:{'enabled':True,'required':c in ['deployment','observation']} for c in ['deployment','flags','channels','observation','recovery']},
        'initial_cohort':'internal','observation':{'duration_seconds':0.01,'min_samples':10,'max_value':1.0,'max_age_seconds':600,'source':'synthetic-metric','query':'error-rate-v1'},'rollback_compatibility':{'old-artifact':'schema-1'}}}
    write(host,'policy.json',policy);write(host,'authority.json',{'schema':'host-authority/v1','decisions':[]})
    identity={'base':base,'head':head,'worktree_digest':'uncommitted-fixture-snapshot','ci_config_digest':'ci-config-1','artifact_digest':'artifact-pr'}
    write(host,'handoff.json',{'schema':'pre-pr-handoff/v1','task_id':policy['task_id'],'subject':identity,'budget':{'rounds':1,'operations':2},'requirements_ref':'self-owned requirement: preserve note content','history_ref':'synthetic prePR history; not actual review','findings':[]})
    write(host,'sim-provider.json',{'schema':'local-delivery-simulation/v1','simulation':True,'revision':0,'effective_at':now.isoformat(),'artifact':'old-artifact','target':'isolated-test','cohort':'internal','config':'local-config-0','data_version':'schema-1','enabled':False,'paused':False,'channels':{},'operations':{},'capabilities':['deployment','flags','channels','observation','recovery']})
    (inputs/'raw.txt').write_bytes(('SYNTHETIC evidence, not a scanner, model review, CI or production result.\n').encode('utf-8'))
    return {'root':str(root),'host':str(host),'inputs':str(inputs),'repo':str(repo),'policy_sha':hashlib.sha256((host/'policy.json').read_bytes()).hexdigest()}

def grant(host,policy_sha,action,binding,kind='human',decision='approve',identifier=None):
    path=Path(host)/'authority.json';a=json.loads(path.read_text(encoding='utf-8'))
    a['decisions'].append(dict(id=identifier or action+'-'+binding,actor='test-human',kind=kind,action=action,binding=binding,policy_sha=policy_sha,decision=decision,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
    write(Path(host),'authority.json',a)

def evidence(inputs,identity,check,identifier=None):
    now=datetime.now(timezone.utc)
    return {'id':identifier or check['id'],'check':check['id'],'subject':identity,'config':check['config'],'scope':check['scope'],'status':'PASS','complete':True,'observed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=30)).isoformat(),'provider':'self-owned-fixture','run':'simulated-run','job':check['id'],'attempt':'1','tool_version':'fixture-1','context_id':'context-a','reviewer':'reviewer-a','raw':{'path':'raw.txt','sha256':hashlib.sha256((Path(inputs)/'raw.txt').read_bytes()).hexdigest()}}

if __name__=='__main__':
    require(len(sys.argv)==3 and sys.argv[2]=='--trusted-fixture','usage: fixture.py NEW_DIRECTORY --trusted-fixture')
    print(json.dumps(create(sys.argv[1]),indent=2))
