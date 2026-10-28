"""Offline real-bare-Git + simulated-provider + record contract integration tests."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from validation.lifecycle.fixture import create,write,grant,evidence
from interfaces.lifecycle.core import digest
from interfaces.lifecycle.merge import LocalGit
from validation.test_support import temporary_directory

ROOT=Path(__file__).resolve().parents[2]
CLI=ROOT/'interfaces/lifecycle/cli.py'
class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=temporary_directory();self.addCleanup(self.tmp.cleanup)
        self.f=create(Path(self.tmp.name)/'fixture');self.host=Path(self.f['host']);self.inputs=Path(self.f['inputs'])
        self.policy=json.loads((self.host/'policy.json').read_text(encoding='utf-8'));self.pin=self.f['policy_sha']
        self.subject=json.loads((self.host/'handoff.json').read_text(encoding='utf-8'))['subject'];self.log=[]
    def run_cli(self,command,record=None,operation=None,code=0):
        cmd=[sys.executable]+(['-O'] if sys.flags.optimize else [])+['-B',str(CLI),'--host-root',str(self.host),'--policy-sha',self.pin,'--input-root',str(self.inputs)]
        if record:cmd+=['--record',record]
        if operation:cmd+=['--operation',operation]
        cmd.append(command);p=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
        self.log.append({'command':cmd,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        self.assertEqual(p.returncode,code,p.stderr+p.stdout);out=json.loads(p.stdout)
        if code:self.assertFalse(out['authorizes_action'])
        return out
    def edit_policy(self,fn):
        fn(self.policy);write(self.host,'policy.json',self.policy);self.pin=hashlib.sha256((self.host/'policy.json').read_bytes()).hexdigest()
    def initialize(self):self.run_cli('init','handoff.json')
    def batch(self,items=None,findings=None):
        write(self.inputs,'batch.json',dict(schema='review-batch/v1',evidence=items if items is not None else [evidence(self.inputs,self.subject,c) for c in self.policy['checks'] if c['stage']=='pr'],findings=findings or []))
        return self.run_cli('ingest','batch.json')
    def approval(self,action,binding,**kw):grant(self.host,self.pin,action,binding,**kw)
    def ready(self):
        self.initialize();self.run_cli('begin-review');self.batch();self.approval('pr-approve',digest(self.subject))
        self.assertEqual(self.run_cli('pr-status')['recommendation'],'READY_FOR_NEXT')
    def prepare(self):
        self.ready();binding=digest({'repository':self.f['repo'],'base':self.subject['base'],'head':self.subject['head'],'policy_sha':self.pin})
        self.approval('merge-prepare',binding)
        write(self.host,'merge.json',dict(schema='merge-plan/v1',base=self.subject['base'],head=self.subject['head'],integration_artifact='artifact-combined'))
        return self.run_cli('merge-prepare','merge.json')['plan']
    def merge_ready(self):
        plan=self.prepare();c=next(c for c in self.policy['checks'] if c['stage']=='merge')
        self.batch([evidence(self.inputs,plan['subject'],c)])
        self.approval('merge-approve',digest(plan['subject']));self.approval('merge-execute',plan['id'])
        return plan
    def merged(self):
        plan=self.merge_ready();receipt=self.run_cli('merge-execute');self.assertEqual(receipt['result'],'confirmed');return plan
    def action(self,action,params,execute=True):
        write(self.host,'action.json',dict(schema='delivery-plan/v1',action=action,parameters=params))
        plan=self.run_cli('delivery-plan','action.json')
        if not execute:return plan
        self.approval('delivery-'+action,plan['id']);return self.run_cli('delivery-execute',operation=plan['id'])
    def observation(self,**changes):
        state=json.loads((self.host/'sim-provider.json').read_text(encoding='utf-8'));o=self.policy['delivery']['observation']
        import time
        remaining=(datetime.fromisoformat(state['effective_at'])+timedelta(seconds=o['duration_seconds'])-datetime.now(timezone.utc)).total_seconds()
        if remaining>0:time.sleep(min(remaining+.002,.05)) # Only the self-owned 10 ms fixture window.
        record={k:state[k] for k in ['artifact','target','cohort','config']}
        record.update(schema='observation/v1',window_start=state['effective_at'],window_end=(datetime.fromisoformat(state['effective_at'])+timedelta(seconds=o['duration_seconds'])).isoformat(),source=o['source'],query=o['query'],samples=20,value=.1,complete=True,observed_at=datetime.now(timezone.utc).isoformat());record.update(changes)
        write(self.inputs,'observation.json',record);return self.run_cli('observe','observation.json')
    def finding(self):
        return dict(id='F1',subject=self.subject.copy(),location='off-diff/caller:7',trigger='explicit synthetic trigger',impact='synthetic contract failure',severity='high',confidence_basis='raw example',attribution='pre_existing',attribution_basis='base comparison',source='fixture',evidence_refs=['semantic'])
    def decide(self,**changes):
        r=dict(schema='finding-decision/v1',actor='reviewer-a',subject=self.subject,finding='F1',fact='refuted',disposition='declined',blocking=False,reason='reviewed counterevidence',evidence_refs=['semantic'],duplicate_of=None);r.update(changes);write(self.host,'decision.json',r);return r

    def test_full_cli_lifecycle(self):
        self.merged();d=self.action('deploy',{'artifact':'artifact-combined'});self.assertTrue(d['simulation']);self.assertFalse(d['state']['enabled'])
        self.action('flag-set',{'enabled':True,'cohort':'internal'});self.assertEqual(self.observation()['status'],'PASS')
        p=self.action('promote',{'artifact':'artifact-combined','channel':'stable'});self.assertEqual(p['state']['channels']['stable'],'artifact-combined')
        paused=self.action('pause',{});self.assertTrue(paused['state']['enabled']);self.assertTrue(paused['state']['paused'])
        off=self.action('flag-set',{'enabled':False,'cohort':'internal'});self.assertFalse(off['state']['enabled'])
        rolled=self.action('rollback',{'artifact':'old-artifact'});self.assertEqual(rolled['state']['artifact'],'old-artifact')
        fix=self.action('forward-fix',{'finding_ids':['incident-1'],'handoff_ref':'authorized repair task'});self.assertEqual(fix['state']['artifact'],'old-artifact')
        exported=self.run_cli('export');self.assertEqual(exported['publication'],'NOT_RUN');self.assertGreater(exported['state']['budget']['operations'],2)
        dest=Path(self.tmp.name)/'results';dest.mkdir(exist_ok=True)
        write(dest,'e2e'+('-optimized' if sys.flags.optimize else '')+'.json',{'kind':'self-owned CLI + real isolated Git + simulated delivery','runs':self.log})
    def test_missing_human_not_agent(self):
        self.initialize();self.run_cli('begin-review');self.batch();self.approval('pr-approve',digest(self.subject),kind='agent');self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
    def test_human_revoked(self):
        self.ready();self.approval('pr-approve',digest(self.subject),decision='revoke');self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
    def test_missing_partial_stale_and_failed(self):
        self.initialize();self.assertEqual(self.run_cli('pr-status')['checks'][0]['status'],'NOT_RUN')
        for i,change in enumerate([{'complete':False},{'status':'FAIL'},{'subject':dict(self.subject,head='other')},{'expires_at':'2000-01-01T00:00:00Z'}]):
            e=evidence(self.inputs,self.subject,self.policy['checks'][0],str(i));e.update(change);out=self.batch([e]);self.assertNotEqual(out['recommendation'],'READY_FOR_NEXT')
    def test_high_risk_experts_and_human(self):
        self.edit_policy(lambda p:p.update(risk='high'));self.ready_without_assertion()
        self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
        e=evidence(self.inputs,self.subject,self.policy['checks'][1],'expert-b');e.update(reviewer='reviewer-b',context_id='context-b');self.batch([e]);self.assertEqual(self.run_cli('pr-status')['recommendation'],'READY_FOR_NEXT')
    def ready_without_assertion(self):
        self.initialize();self.run_cli('begin-review');self.batch();self.approval('pr-approve',digest(self.subject))
    def test_budget_no_reset(self):
        self.edit_policy(lambda p:p['budget'].update(max_rounds=1));self.initialize();self.run_cli('begin-review',code=2);self.run_cli('init','handoff.json',code=2)
        st=json.loads((self.host/'state.json').read_text(encoding='utf-8'));self.assertEqual(st['budget']['rounds'],1)
    def test_absence_does_not_resolve_and_refutation(self):
        self.ready();self.batch([], [self.finding()]);self.batch([]);self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
        self.decide();self.assertEqual(self.run_cli('adjudicate','decision.json')['recommendation'],'READY_FOR_NEXT')
        self.batch([], [self.finding()]);self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
    def test_reason_only_cannot_close(self):
        self.ready();self.batch([], [self.finding()]);self.decide(fact='confirmed',disposition='deferred');self.run_cli('adjudicate','decision.json',code=2)
    def test_advance_invalidates_without_budget_reset(self):
        self.ready();old=json.loads((self.host/'state.json').read_text(encoding='utf-8'))['budget'].copy()
        write(self.host,'advance.json',dict(schema='subject-change/v1',subject=dict(self.subject,head='changed'),impact='changed dependency affects callers',reused_evidence=[]))
        out=self.run_cli('advance','advance.json');self.assertEqual(out['recommendation'],'HOLD');self.assertEqual(out['budget']['rounds'],old['rounds']);self.assertEqual(out['checks'][0]['status'],'NOT_RUN')
    def test_import_cannot_contain_approval(self):
        self.initialize();write(self.inputs,'bad.json',dict(schema='review-batch/v1',evidence=[],findings=[],approval='human'));self.run_cli('ingest','bad.json',code=2)
    def test_bad_policy_pin(self):
        self.pin='bad';self.run_cli('init','handoff.json',code=2)
    def test_no_empty_required_gates(self):
        self.edit_policy(lambda p:p.update(checks=[]));self.run_cli('init','handoff.json',code=2)
    def test_symlink_and_deep_json(self):
        self.initialize();(self.inputs/'link.json').symlink_to(self.host/'authority.json');self.run_cli('ingest','link.json',code=2)
        (self.inputs/'deep.json').write_bytes(('['*2000+'0'+']'*2000).encode('utf-8'));self.run_cli('ingest','deep.json',code=2)
    def test_merge_missing_integration_and_authority(self):
        self.prepare();self.run_cli('merge-execute',code=2)
    def test_merge_cas_drift(self):
        plan=self.merge_ready();g=LocalGit(self.policy['merge']);self.assertEqual(g.run('update-ref',g.ref,plan['head'],plan['base']).returncode,0)
        self.run_cli('merge-execute',code=2);self.assertEqual(g.target(),plan['head'])
    def test_real_git_cas_rejects_wrong_expected(self):
        plan=self.prepare();g=LocalGit(self.policy['merge']);x=g.run('update-ref',g.ref,plan['candidate'],plan['head']);self.assertNotEqual(x.returncode,0);self.assertEqual(g.target(),plan['base'])
    def test_merge_repeat_receipt(self):
        plan=self.merged();self.assertEqual(self.run_cli('merge-execute')['actual_target'],plan['candidate'])
    def test_unsupported_merge_method(self):
        self.edit_policy(lambda p:p['merge'].update(method='squash'));self.ready();write(self.host,'merge.json',dict(schema='merge-plan/v1',base=self.subject['base'],head=self.subject['head'],integration_artifact='x'));self.run_cli('merge-prepare','merge.json',code=2)
    def test_deploy_without_merge(self):
        self.ready();write(self.host,'action.json',dict(schema='delivery-plan/v1',action='deploy',parameters={'artifact':'artifact-combined'}));self.run_cli('delivery-plan','action.json',code=2)
    def test_delivery_unapproved_revoked_and_drift(self):
        self.merged();p=self.action('deploy',{'artifact':'artifact-combined'},False);self.run_cli('delivery-execute',operation=p['id'],code=2)
        self.approval('delivery-deploy',p['id']);self.approval('delivery-deploy',p['id'],decision='revoke');self.run_cli('delivery-execute',operation=p['id'],code=2)
        self.approval('delivery-deploy',p['id']);state=json.loads((self.host/'sim-provider.json').read_text(encoding='utf-8'));state['revision']+=1;write(self.host,'sim-provider.json',state);self.run_cli('delivery-execute',operation=p['id'],code=2)
    def test_delivery_idempotency_and_lost_receipt(self):
        self.merged();p=self.action('deploy',{'artifact':'artifact-combined'},False);self.approval('delivery-deploy',p['id']);out=self.run_cli('delivery-execute',operation=p['id'])
        state=json.loads((self.host/'state.json').read_text(encoding='utf-8'));state['delivery']['requests'][p['id']]={'result':'pending'};write(self.host,'state.json',state)
        again=self.run_cli('delivery-execute',operation=p['id']);self.assertEqual(out['state']['revision'],again['state']['revision']);self.assertEqual(again['result']['result'],'confirmed')
    def test_required_missing_capability(self):
        self.initialize();state=json.loads((self.host/'sim-provider.json').read_text(encoding='utf-8'));state['capabilities']=[];write(self.host,'sim-provider.json',state)
        self.assertIn('BLOCKED',[x['status'] for x in self.run_cli('delivery-status')['checks']])
    def test_observation_bad_target_samples_and_nan(self):
        self.initialize();self.assertEqual(self.observation(target='wrong')['status'],'UNKNOWN');self.assertEqual(self.observation(samples=0)['status'],'UNKNOWN')
        self.assertEqual(self.observation(complete=False)['status'],'UNKNOWN')
        # Raw JSON NaN is rejected before any modeled PASS.
        (self.inputs/'nan.json').write_bytes(('{"value":NaN}').encode('utf-8'));self.run_cli('observe','nan.json',code=2)
    def test_observation_fail_and_promotion_rebuild(self):
        self.initialize();self.assertEqual(self.observation(value=10)['status'],'FAIL')
        write(self.host,'action.json',dict(schema='delivery-plan/v1',action='promote',parameters={'artifact':'rebuilt','channel':'stable'}));self.run_cli('delivery-plan','action.json',code=2)
    def test_rollback_incompatible(self):
        self.initialize();write(self.host,'action.json',dict(schema='delivery-plan/v1',action='rollback',parameters={'artifact':'unknown-old'}));self.run_cli('delivery-plan','action.json',code=2)

    def test_merge_nonsha_subject_drift(self):
        for field in ['worktree_digest','ci_config_digest','artifact_digest']:
            with self.subTest(field=field):
                if field!='worktree_digest':self.tearDown();self.setUp()
                plan=self.merge_ready();self.subject=dict(self.subject,**{field:'changed'})
                write(self.host,'advance.json',dict(schema='subject-change/v1',subject=self.subject,impact='identity changed',reused_evidence=[]))
                self.run_cli('advance','advance.json');self.run_cli('begin-review')
                self.batch([evidence(self.inputs,self.subject,c,'new-'+c['id']) for c in self.policy['checks'] if c['stage']=='pr'])
                self.approval('pr-approve',digest(self.subject));out=self.run_cli('merge-preflight')
                self.assertIn('candidate stale',out['reasons']);self.assertNotEqual(out['recommendation'],'READY_FOR_NEXT')
    def test_round_cannot_be_omitted(self):
        self.initialize();write(self.inputs,'batch.json',dict(schema='review-batch/v1',evidence=[evidence(self.inputs,self.subject,self.policy['checks'][1])],findings=[]));self.run_cli('ingest','batch.json',code=2)
    def test_refutation_invalidated(self):
        self.ready();self.batch([], [self.finding()]);self.decide();self.run_cli('adjudicate','decision.json')
        write(self.host,'advance.json',dict(schema='subject-change/v1',subject=dict(self.subject,worktree_digest='new'),impact='changed affected caller',reused_evidence=[]))
        self.run_cli('advance','advance.json');st=json.loads((self.host/'state.json').read_text(encoding='utf-8'));self.assertTrue(st['findings']['F1']['blocking']);self.assertEqual(len(st['findings']['F1']['decisions']),1)
    def test_expert_pass_superseded(self):
        self.edit_policy(lambda p:p.update(risk='high'));self.ready_without_assertion()
        a=evidence(self.inputs,self.subject,self.policy['checks'][1],'a-failed');a['status']='FAIL'
        b=evidence(self.inputs,self.subject,self.policy['checks'][1],'b-pass');b.update(reviewer='reviewer-b',context_id='context-b')
        self.batch([a,b]);self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
    def test_shared_commondir_rejected(self):
        (Path(self.f['repo'])/'commondir').write_bytes(('../other.git').encode('utf-8'))
        with self.assertRaises(ValueError):LocalGit(self.policy['merge'])
    def test_symbolic_ref_rejected(self):
        g=LocalGit(self.policy['merge']);self.assertEqual(g.run('update-ref','refs/heads/other',self.subject['base']).returncode,0)
        self.assertEqual(g.run('symbolic-ref','refs/heads/main','refs/heads/other').returncode,0)
        with self.assertRaises(ValueError):g.target()
    def test_symbolic_switch_does_not_update_other(self):
        plan=self.prepare();g=LocalGit(self.policy['merge']);g.run('update-ref','refs/heads/other',plan['base']);g.run('symbolic-ref',g.ref,'refs/heads/other')
        # Use actual reference update mode, proving it cannot redirect to the other branch.
        g.run('update-ref','--no-deref',g.ref,plan['candidate'],plan['base'])
        self.assertEqual(g.run('rev-parse','refs/heads/other').stdout.strip(),plan['base'])
    def test_invalid_config_and_B_binding(self):
        self.initialize();write(self.inputs,'B.json',{'schema':'security-evidence/v1','authorizes_progress':False,'status':'PASS','request_binding':[1]})
        write(self.inputs,'import.json',{'schema':'security-import/v1','check':'security','record':'B.json'});self.run_cli('import-security','import.json',code=2)
        (Path(self.f['repo'])/'config').write_bytes(('not valid git config').encode('utf-8'))
        with self.assertRaises(ValueError):LocalGit(self.policy['merge'])
    def test_human_expiration(self):
        self.ready();a=json.loads((self.host/'authority.json').read_text(encoding='utf-8'));a['decisions'][-1]['expires_at']='2000-01-01T00:00:00Z';write(self.host,'authority.json',a)
        self.assertEqual(self.run_cli('pr-status')['recommendation'],'HOLD')
    def test_delivery_module_disabled(self):
        self.edit_policy(lambda p:p['delivery'].update(enabled=False));self.initialize();out=self.run_cli('delivery-status');self.assertEqual(out['checks'][0]['status'],'NOT_RUN');self.assertEqual(out['recommendation'],'HOLD');self.assertIn('no online acceptance',out['checks'][0]['reason'])
    def test_new_blocker_stops_planned_promotion_but_allows_off(self):
        self.merged();self.action('deploy',{'artifact':'artifact-combined'});self.assertEqual(self.observation()['status'],'PASS')
        proposal=self.action('promote',{'artifact':'artifact-combined','channel':'stable'},False);self.approval('delivery-promote',proposal['id'])
        self.batch([], [self.finding()]);self.run_cli('delivery-execute',operation=proposal['id'],code=2)
        self.assertEqual(self.action('flag-set',{'enabled':False,'cohort':'internal'})['result']['result'],'confirmed')
        self.assertNotEqual(self.run_cli('delivery-status')['recommendation'],'READY_FOR_NEXT')
    def test_pre_effect_observation_rejected(self):
        self.initialize();r=self.observation(window_start='2000-01-01T00:00:00Z',window_end='2000-01-01T00:00:01Z');self.assertEqual(r['status'],'UNKNOWN')
    def test_old_channel_receipt_not_current(self):
        self.edit_policy(lambda p:p['delivery']['capabilities']['channels'].update(required=True));self.merged();self.action('deploy',{'artifact':'artifact-combined'});self.observation();self.action('promote',{'artifact':'artifact-combined','channel':'stable'})
        self.action('rollback',{'artifact':'old-artifact'});out=self.run_cli('delivery-status');self.assertNotEqual(next(c for c in out['checks'] if c['id']=='channels')['status'],'PASS')
    def test_B_bridge_with_existing_normalizer(self):
        self.initialize();self.run_cli('begin-review')
        sys.path.insert(0,str(ROOT/'validation/security-integration'))
        # Build the established B format from its known example; no scanner invoked.
        original=ROOT/'interfaces/security-tools/examples'
        q=json.loads((original/'request.json').read_text(encoding='utf-8'));r=json.loads((original/'result.json').read_text(encoding='utf-8'));raw=(original/'raw.txt').read_bytes()
        now=datetime.now(timezone.utc);q.update(expected_subject=self.subject,expected_config_digest='check-config-1',required_scope=['self-owned-change'],valid_from_utc=(now-timedelta(minutes=1)).isoformat(),valid_until_utc=(now+timedelta(minutes=5)).isoformat())
        r.update(subject=self.subject,config_digest='check-config-1',scope=['self-owned-change'],observed_at_utc=now.isoformat());write(self.inputs,'b-request.json',q);write(self.inputs,'b-result.json',r);(self.inputs/'raw.txt').write_bytes(raw)
        cmd=[sys.executable]+(['-O'] if sys.flags.optimize else [])+['-B',str(ROOT/'interfaces/security-tools/normalize_result.py'),'--root',str(self.inputs),'--request','b-request.json','--result','b-result.json']
        x=subprocess.run(cmd,text=True,capture_output=True,timeout=10);self.assertEqual(x.returncode,0,x.stderr);(self.inputs/'B.json').write_bytes((x.stdout).encode('utf-8'))
        write(self.inputs,'bridge.json',{'schema':'security-import/v1','check':'security','record':'B.json'});out=self.run_cli('import-security','bridge.json');self.assertEqual(next(c for c in out['checks'] if c['id']=='security')['status'],'PASS');self.assertEqual(out['recommendation'],'HOLD')
    def test_merge_tool_error_is_not_conflict(self):
        from unittest.mock import patch
        from interfaces.lifecycle.core import Session
        from interfaces.lifecycle import merge
        self.ready();binding=digest({'repository':self.f['repo'],'base':self.subject['base'],'head':self.subject['head'],'policy_sha':self.pin});self.approval('merge-prepare',binding)
        real=LocalGit.run
        def error(g,*args,**kw):
            if args[0]=='merge-tree':return subprocess.CompletedProcess(args,129,'','synthetic unsupported option')
            return real(g,*args,**kw)
        with Session(self.host,self.pin).locked() as s,patch.object(LocalGit,'run',error):
            out=merge.prepare(s,{'schema':'merge-plan/v1','base':self.subject['base'],'head':self.subject['head'],'integration_artifact':'a'})
        self.assertEqual(out['status'],'UNKNOWN')
    def test_merge_timeout_receipt_query(self):
        from unittest.mock import patch
        from interfaces.lifecycle.core import Session
        from interfaces.lifecycle import merge
        self.merge_ready();real=LocalGit.run
        def lost(g,*args,**kw):
            result=real(g,*args,**kw)
            if args[0]=='update-ref':raise subprocess.TimeoutExpired(args,10)
            return result
        with Session(self.host,self.pin).locked() as s,patch.object(LocalGit,'run',lost):out=merge.execute(s)
        self.assertEqual(out['result'],'confirmed');self.assertEqual(self.run_cli('merge-query')['result'],'confirmed')
    def test_semantic_duplicate_preserves_observations(self):
        self.ready();f=self.finding();other=dict(f,id='F2');self.batch([], [f,other])
        self.decide(fact='confirmed',disposition='open',blocking=True,duplicate_of='F2');self.run_cli('adjudicate','decision.json')
        self.decide(finding='F2');self.assertEqual(self.run_cli('adjudicate','decision.json')['recommendation'],'READY_FOR_NEXT')
        state=json.loads((self.host/'state.json').read_text(encoding='utf-8'));self.assertEqual(len(state['findings']),2)

    def test_real_combination_conflict(self):
        g=LocalGit(self.policy['merge'])
        blob=g.run('hash-object','-w','--stdin',input='different self-owned target change\n').stdout.strip()
        tree=g.run('mktree',input='100644 blob '+blob+'\tnote.txt\n').stdout.strip()
        target=g.run('commit-tree',tree,'-p',self.subject['base'],input='conflicting fixture branch\n').stdout.strip()
        self.assertEqual(g.run('update-ref',g.ref,target,self.subject['base']).returncode,0)
        self.subject['base']=target;h=json.loads((self.host/'handoff.json').read_text(encoding='utf-8'));h['subject']=self.subject;write(self.host,'handoff.json',h)
        self.ready();binding=digest({'repository':self.f['repo'],'base':target,'head':self.subject['head'],'policy_sha':self.pin});self.approval('merge-prepare',binding)
        write(self.host,'merge.json',dict(schema='merge-plan/v1',base=target,head=self.subject['head'],integration_artifact='a'))
        out=self.run_cli('merge-prepare','merge.json');self.assertEqual(out['status'],'FAIL');self.assertEqual(out['exit_code'],1);self.assertEqual(g.target(),target)
    def test_operation_budget_stops_before_action(self):
        self.edit_policy(lambda p:p['budget'].update(max_operations=2));self.initialize();out=self.run_cli('begin-review',code=2);self.assertEqual(out['recommendation'],'REPLAN')

    def test_future_subject_cannot_preload_review(self):
        self.ready();future=dict(self.subject,worktree_digest='future')
        e=evidence(self.inputs,future,self.policy['checks'][1],'future-semantic')
        write(self.inputs,'batch.json',dict(schema='review-batch/v1',evidence=[e],findings=[]));self.run_cli('ingest','batch.json',code=2)
        write(self.host,'advance.json',dict(schema='subject-change/v1',subject=future,impact='new behavior',reused_evidence=[]));self.run_cli('advance','advance.json')
        self.approval('pr-approve',digest(future));out=self.run_cli('pr-status');self.assertNotEqual(out['recommendation'],'READY_FOR_NEXT');self.assertIn('current subject review round not registered',out['reasons'])
    def test_required_capabilities_finish_together(self):
        self.edit_policy(lambda p:[p['delivery']['capabilities'][k].update(required=True) for k in ['flags','channels']])
        self.merged();self.action('deploy',{'artifact':'artifact-combined'});self.action('flag-set',{'enabled':True,'cohort':'internal'});self.observation();self.action('promote',{'artifact':'artifact-combined','channel':'stable'});self.observation()
        out=self.run_cli('delivery-status');self.assertEqual(out['recommendation'],'READY_FOR_NEXT',out)

    def test_execute_cas_rejects_drift_after_preflight(self):
        from unittest.mock import patch
        from interfaces.lifecycle.core import Session
        from interfaces.lifecycle import merge
        plan=self.merge_ready();real=LocalGit.run
        def drift(g,*args,**kwargs):
            if args[0]=='update-ref':
                changed=real(g,'update-ref',g.ref,plan['head'],plan['base'])
                self.assertEqual(changed.returncode,0,changed.stderr)
            return real(g,*args,**kwargs)
        with Session(self.host,self.pin).locked() as s,patch.object(LocalGit,'run',drift):
            out=merge.execute(s)
        self.assertNotEqual(out['result'],'confirmed')
        self.assertNotEqual(out['receipt']['exit_code'],0)
        self.assertEqual(LocalGit(self.policy['merge']).target(),plan['head'])

    def test_git_runtime_preserves_home_but_rejects_injected_config(self):
        import os
        from unittest.mock import patch
        from support.git_runtime import git_environment
        with patch.dict(os.environ,{'GIT_CONFIG_COUNT':'999','GIT_DIR':'/untrusted','PYTHONPATH':'/untrusted'}):
            env=git_environment()
        self.assertEqual(env.get('HOME'),os.environ.get('HOME'))
        self.assertNotIn('GIT_CONFIG_COUNT',env);self.assertNotIn('GIT_DIR',env)
        self.assertNotIn('PYTHONPATH',env);self.assertEqual(env['GIT_CONFIG_GLOBAL'],os.devnull)

    def test_git_subprocess_timeout_is_enforced(self):
        from unittest.mock import patch
        g=LocalGit(self.policy['merge'])
        with patch('interfaces.lifecycle.merge.subprocess.run') as run:
            g.run('rev-parse','--verify',g.ref)
        self.assertEqual(run.call_args.kwargs['timeout'],10)
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_user_attributes_do_not_change_merge_conflict(self):
        g=LocalGit(self.policy['merge'])
        config=Path(self.tmp.name)/'user-config';(config/'git').mkdir(parents=True)
        attributes=config/'git/attributes';(attributes).write_bytes(('note.txt merge=union\n').encode('utf-8'))
        g.env['XDG_CONFIG_HOME']=str(config)
        blob=g.run('hash-object','-w','--stdin',input='conflicting target\n').stdout.strip()
        tree=g.run('mktree',input='100644 blob '+blob+'\tnote.txt\n').stdout.strip()
        target=g.run('commit-tree',tree,'-p',self.subject['base'],input='target\n').stdout.strip()
        # Positive control proves this attributes fixture would resolve the conflict.
        control=g.run('-c','core.attributesFile='+str(attributes),'merge-tree','--write-tree',target,self.subject['head'])
        self.assertEqual(control.returncode,0,repr((control.stdout,control.stderr)))
        isolated=g.run('merge-tree','--write-tree',target,self.subject['head'])
        self.assertEqual(isolated.returncode,1,isolated.stdout+isolated.stderr)

    def test_interpolation_like_git_config_is_structured_error(self):
        self.prepare()
        path=Path(self.f['repo'])/'config'
        (path).write_bytes((path.read_text(encoding='utf-8').replace('bare = true','bare = tr%(x)sue')).encode('utf-8'))
        out=self.run_cli('merge-query',code=2)
        self.assertEqual(out['status'],'UNKNOWN')
        self.assertIn('bare SHA-1 repository required',out['reason'])

    def test_invalid_evidence_overrides_an_otherwise_ready_baseline(self):
        self.ready()
        for i,(change,expected) in enumerate([
            ({'complete':False},'UNKNOWN'),({'status':'FAIL'},'FAIL'),
            ({'expires_at':'2000-01-01T00:00:00Z'},'UNKNOWN'),
            ({'scope':['different-scope']},'UNKNOWN')]):
            with self.subTest(change=change):
                e=evidence(self.inputs,self.subject,self.policy['checks'][0],'invalid-'+str(i))
                e.update(change);out=self.batch([e])
                self.assertEqual(next(c for c in out['checks'] if c['id']=='ci')['status'],expected)
                self.assertEqual(out['recommendation'],'HOLD')
                restored=evidence(self.inputs,self.subject,self.policy['checks'][0],'restored-'+str(i))
                self.assertEqual(self.batch([restored])['recommendation'],'READY_FOR_NEXT')

    def test_unknown_status_raw_digest_and_duplicate_id_rejected(self):
        self.ready()
        for identifier,change,reason in [
            ('unknown',{'status':'UNRECOGNIZED'},'unknown status'),
            ('digest',{'raw':{'path':'raw.txt','sha256':'0'*64}},'raw evidence digest mismatch'),
            ('ci',{},'evidence ID already present')]:
            with self.subTest(reason=reason):
                e=evidence(self.inputs,self.subject,self.policy['checks'][0],identifier);e.update(change)
                write(self.inputs,'invalid.json',{'schema':'review-batch/v1','evidence':[e],'findings':[]})
                out=self.run_cli('ingest','invalid.json',code=2)
                self.assertIn(reason,out['reason'])
                self.assertEqual(self.run_cli('pr-status')['recommendation'],'READY_FOR_NEXT')

    def test_delivery_query_cli_reads_confirmed_operation(self):
        self.merged();receipt=self.action('deploy',{'artifact':'artifact-combined'})
        queried=self.run_cli('delivery-query',operation=receipt['operation'])
        self.assertEqual(queried['result']['result'],'confirmed')
        self.assertEqual(queried['state']['revision'],receipt['state']['revision'])

if __name__=='__main__':unittest.main(verbosity=2)
