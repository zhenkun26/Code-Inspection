"""Local lifecycle reference runner. Never calls a remote or arbitrary shell command."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from interfaces.lifecycle.core import Session,load,digest,require,keys
from interfaces.lifecycle import review,merge,delivery,source_content
from interfaces.lifecycle.accounting import InvocationMetrics,budget_report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host-root',required=True,help='host-controlled policy, authority and ledger directory')
    p.add_argument('--policy-sha',required=True,help='SHA256 supplied by trusted user/host, not by reviewed content')
    p.add_argument('--input-root',help='separate untrusted evidence directory')
    p.add_argument('--record',help='relative file; host commands read host-root, ingest/observe read input-root')
    p.add_argument('--operation',help='exact planned delivery operation ID')
    p.add_argument('command',choices=['init','begin-review','ingest','import-security','adjudicate','advance','pr-status','export',
        'merge-prepare','merge-preflight','merge-execute','merge-query','delivery-plan','delivery-execute',
        'delivery-query','observe','delivery-status','temp-report','source-capture'])
    args=p.parse_args()
    metrics=InvocationMetrics();s=None
    try:
        if args.command=='temp-report':
            from interfaces.lifecycle.temporary import report
            out=report(args.host_root,args.policy_sha)
            print(json.dumps(out,ensure_ascii=True,indent=2,allow_nan=False));return 0
        s=Session(args.host_root,args.policy_sha,metrics=metrics)
        with s.locked():
            if args.command=='source-capture':
                require(args.record,'host-controlled source capture record required')
                record=load(s.root,args.record);keys(record,['schema','subject'])
                require(record['schema']=='source-capture/v1','source capture schema')
                out=source_content.build(s,record['subject'])
            elif args.command=='init':
                require(args.record,'host handoff record required');out=s.initialize(load(s.root,args.record))
            else:
                require(s.state is not None,'initialize first')
                c=args.command
                if c in {'ingest','observe','import-security'}:
                    require(args.input_root and args.record,'evidence root and record required')
                    payload=load(args.input_root,args.record)
                    out=review.ingest(s,args.input_root,payload) if c=='ingest' else review.import_security(s,args.input_root,payload) if c=='import-security' else delivery.observe(s,payload)
                elif c=='begin-review':
                    source_content.current(s)
                    s.charge(rounds=1,operation='begin-review');s.state['round_subject']=digest(s.state['subject']);s.event('begin-review',{'subject':s.state['subject']});s.persist();out=review.evaluate(s)
                elif c in {'adjudicate','advance','merge-prepare','delivery-plan'}:
                    require(args.record,'host-controlled record required');record=load(s.root,args.record)
                    out={'adjudicate':review.adjudicate,'advance':review.advance,'merge-prepare':merge.prepare,'delivery-plan':delivery.plan}[c](s,record)
                elif c=='pr-status':out=review.evaluate(s)
                elif c=='merge-preflight':out=merge.preflight(s)
                elif c=='merge-execute':out=merge.execute(s)
                elif c=='merge-query':out=merge.query(s)
                elif c=='delivery-execute':require(args.operation,'operation required');out=delivery.execute(s,args.operation)
                elif c=='delivery-query':require(args.operation,'operation required');out=delivery.query(s,args.operation)
                elif c=='delivery-status':out=delivery.evaluate(s)
                elif c=='export':out={'schema':'lifecycle-handoff/v1','state':s.state,'pr_decision':review.evaluate(s),'publication':'NOT_RUN'}
        out['budget_report']=budget_report(s,metrics);out['authorizes_action']=False
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False));return 0
    except (ValueError,OSError,UnicodeError,TypeError,OverflowError,RecursionError,KeyError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status':'UNKNOWN','recommendation':'REPLAN' if str(exc).startswith('REPLAN:') else 'HOLD',
                          'reason':str(exc),'authorizes_action':False,
                          'budget_report':budget_report(s,metrics)},ensure_ascii=False,allow_nan=False));return 2

if __name__=='__main__':sys.exit(main())
