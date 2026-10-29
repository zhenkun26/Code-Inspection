"""In-memory delivery regressions; no filesystem cleanup or real Git/provider calls."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from interfaces.lifecycle import delivery
from interfaces.lifecycle.core import Session, digest


class MemorySession:
    output=Session.output

    def __init__(self,clock):
        self.pin='synthetic-policy';self.root=None;self.grants=set()
        identity={k:'reviewed-'+k for k in ['base','head','worktree_digest','ci_config_digest','artifact_digest']}
        checks=[dict(id=g,stage='pr',gate=g,required=True,na_reason='',scope=['all'],config='cfg')
                for g in ['ci','semantic','security']]
        self.policy={'risk':'low','low_auto':True,'checks':checks,
            'budget':{'max_operations':100,'max_rounds':5},
            'delivery':{'enabled':True,'capabilities':{c:{'enabled':True,'required':False}
                for c in ['deployment','flags','channels','observation','recovery']},
                'initial_cohort':'internal','observation':{'duration_seconds':10,'min_samples':1,
                    'max_value':1,'max_age_seconds':600,'source':'synthetic','query':'errors'},
                'rollback_compatibility':{'old-artifact':'v1'}}}
        self.state={'subject':identity,'round_subject':digest(identity),'evidence':[],
            'findings':{},'delivery':{},'merge':{},'events':[],'budget':{'operations':0,'rounds':1}}
        for c in checks:
            self.state['evidence'].append(dict(id=c['id'],check=c['id'],subject=identity,
                config='cfg',scope=['all'],status='PASS',complete=True,
                observed_at=(clock-timedelta(minutes=5)).isoformat(),
                expires_at=(clock+timedelta(hours=1)).isoformat(),provider='synthetic',
                run='1',attempt='1',job=c['id'],reviewer='test',context_id='test'))

    # This storage-free double tests delivery rules; real Session durability is
    # exercised by the separate filesystem fault regressions.
    def require_usable(self):pass
    def charge(self, rounds=0, operation='unspecified'):
        self.state['budget']['operations']+=1;self.state['budget']['rounds']+=rounds
    def persist(self):pass
    def event(self,action,detail):self.state['events'].append({'action':action,'detail':copy.deepcopy(detail)})
    def authorized(self,action,binding):return (action,binding) in self.grants


class DeliveryRegressions(unittest.TestCase):
    def setUp(self):
        self.clock=datetime.now(timezone.utc)
        self.s=MemorySession(self.clock)
        self.provider={'schema':'local-delivery-simulation/v1','simulation':True,
            'revision':0,'artifact':'old-artifact','target':'test','cohort':'internal',
            'config':'initial-config','data_version':'v1','enabled':False,'paused':False,
            'channels':{},'operations':{},'capabilities':['deployment','flags','channels','observation','recovery'],
            'effective_at':(self.clock-timedelta(seconds=20)).isoformat()}
        # Replace storage and the Git query boundary only. Policy, PR gates, provider
        # schema validation, plans, execution, observations and receipts stay real.
        self.merge_result='confirmed'
        for replacement in [patch.object(delivery,'load',side_effect=lambda root,name:copy.deepcopy(self.provider)),
                            patch.object(delivery,'save',side_effect=self.save),
                            patch.object(delivery,'now',side_effect=lambda:self.clock),
                            patch.object(delivery,'merge_validate',side_effect=lambda s,plan:(copy.deepcopy(plan),None)),
                            patch.object(delivery,'merge_receipt',side_effect=lambda s,plan:{'result':'confirmed'}),
                            patch.object(delivery,'merge_query',side_effect=lambda s:{'result':self.merge_result})]:
            replacement.start();self.addCleanup(replacement.stop)

    def save(self,root,name,value):
        self.assertEqual(name,'sim-provider.json');self.provider=copy.deepcopy(value)

    def merged(self):
        self.s.state['merge']={'id':'synthetic-merge-plan','source_subject':copy.deepcopy(self.s.state['subject']),
            'subject':dict(self.s.state['subject'],artifact_digest='combined-artifact')}
        self.s.event('merge-prepare',{'plan':self.s.state['merge']})

    def proposal(self,action,parameters):
        return delivery.plan(self.s,{'schema':'delivery-plan/v1','action':action,'parameters':parameters})

    def execute(self,proposal):
        self.s.grants.add(('delivery-'+proposal['action'],proposal['id']))
        return delivery.execute(self.s,proposal['id'])

    def deploy(self):
        self.merged()
        return self.execute(self.proposal('deploy',{'artifact':'combined-artifact'}))

    def observe(self):
        self.clock+=timedelta(seconds=20)
        record={k:self.provider[k] for k in ['artifact','target','cohort','config']}
        record.update(schema='observation/v1',window_start=self.provider['effective_at'],
            window_end=(datetime.fromisoformat(self.provider['effective_at'])+timedelta(seconds=10)).isoformat(),
            source='synthetic',query='errors',samples=10,value=.1,complete=True,observed_at=self.clock.isoformat())
        self.assertEqual(delivery.observe(self.s,record)['status'],'PASS')

    def test_initial_unrelated_artifact_cannot_promote_or_enable(self):
        self.observe()
        for action,parameters in [('promote',{'artifact':'old-artifact','channel':'stable'}),
                                  ('flag-set',{'enabled':True,'cohort':'internal'})]:
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'current merge relation'):
                self.proposal(action,parameters)
        self.assertEqual(self.provider['operations'],{})

    def test_matching_artifact_without_deployment_receipt_is_not_enough(self):
        self.merged();self.provider['artifact']='combined-artifact';self.observe()
        for action,parameters in [('promote',{'artifact':'combined-artifact','channel':'stable'}),
                                  ('flag-set',{'enabled':True,'cohort':'internal'})]:
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'confirmed deployment'):
                self.proposal(action,parameters)

    def test_confirmed_deploy_enable_observe_promote_and_repeat(self):
        deployed=self.deploy();deploy_id=deployed['operation']
        enabled=self.proposal('flag-set',{'enabled':True,'cohort':'internal'})
        self.assertEqual(enabled['source'],{'merge_plan':'synthetic-merge-plan','deployment_operation':deploy_id})
        self.execute(enabled);self.observe()
        promoted=self.proposal('promote',{'artifact':'combined-artifact','channel':'stable'})
        out=self.execute(promoted)
        self.assertEqual(out['state']['channels']['stable'],'combined-artifact')
        revision=out['state']['revision'];self.s.state['merge']={}
        self.assertEqual(self.execute(promoted)['state']['revision'],revision)

    def test_deploy_records_exact_merge_source(self):
        self.merged();proposal=self.proposal('deploy',{'artifact':'combined-artifact'})
        self.assertEqual(proposal['source'],{'merge_plan':'synthetic-merge-plan'})
        self.s.state['merge']['id']='replacement-merge'
        with self.assertRaisesRegex(ValueError,'merge relation changed'):self.execute(proposal)

    def test_promote_rechecks_deployment_source_at_execution(self):
        deployed=self.deploy();self.observe()
        proposal=self.proposal('promote',{'artifact':'combined-artifact','channel':'stable'})
        self.s.state['delivery']['plans'][deployed['operation']]['source']={'merge_plan':'unrelated-plan'}
        with self.assertRaisesRegex(ValueError,'proposal digest mismatch'):self.execute(proposal)
        self.assertEqual(self.provider['channels'],{})

    def test_enable_rechecks_merge_confirmation_at_execution(self):
        self.deploy();proposal=self.proposal('flag-set',{'enabled':True,'cohort':'internal'})
        self.merge_result='unknown'
        with self.assertRaisesRegex(ValueError,'actual merge receipt'):self.execute(proposal)
        self.assertFalse(self.provider['enabled'])

    def test_current_merge_artifact_must_match_provider(self):
        self.deploy();self.s.state['merge']['subject']['artifact_digest']='replacement-artifact'
        with self.assertRaisesRegex(ValueError,'artifact must match|delivery merge source mismatch'):
            self.proposal('flag-set',{'enabled':True,'cohort':'internal'})

    def test_old_deploy_receipt_does_not_survive_subject_change(self):
        self.deploy();self.s.state['subject']=dict(self.s.state['subject'],head='new-head')
        self.s.state['round_subject']=digest(self.s.state['subject'])
        for record in self.s.state['evidence']:record['subject']=self.s.state['subject']
        self.s.state['merge']['source_subject']=copy.deepcopy(self.s.state['subject'])
        with self.assertRaisesRegex(ValueError,'confirmed deployment|delivery merge source mismatch'):
            self.proposal('flag-set',{'enabled':True,'cohort':'internal'})

    def test_recovery_stays_available_without_merge_and_with_blocker(self):
        self.provider['enabled']=True
        self.s.state['findings']['blocking']={'id':'blocking','blocking':True,'alias':None}
        for action,parameters in [('flag-set',{'enabled':False,'cohort':'internal'}),
                                  ('pause',{}),('rollback',{'artifact':'old-artifact'})]:
            with self.subTest(action=action):
                result=self.execute(self.proposal(action,parameters))
                self.assertEqual(result['result']['result'],'confirmed')
        self.assertFalse(self.provider['enabled']);self.assertTrue(self.provider['paused'])

    def test_expansion_still_requires_observation_and_exact_authority(self):
        self.deploy()
        with self.assertRaisesRegex(ValueError,'valid observation'):
            self.proposal('flag-set',{'enabled':True,'cohort':'public'})
        self.observe();proposal=self.proposal('flag-set',{'enabled':True,'cohort':'public'})
        with self.assertRaisesRegex(ValueError,'authority missing'):
            delivery.execute(self.s,proposal['id'])
        self.assertEqual(self.execute(proposal)['state']['cohort'],'public')

    def test_disabled_module_is_not_entered_or_ready(self):
        self.s.policy['delivery']['enabled']=False
        result=delivery.evaluate(self.s)
        self.assertEqual(result['checks'][0]['status'],'NOT_RUN')
        self.assertEqual(result['recommendation'],'HOLD')
        self.assertIn('no online acceptance',result['checks'][0]['reason'])


if __name__=='__main__':unittest.main()
