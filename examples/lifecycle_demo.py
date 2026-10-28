"""Run a fresh offline example: real isolated Git, synthetic review/approval/delivery.

Only accepts a NEW output directory outside this checkout. Never resumes a host
session or loads project-supplied commands. All artifacts are retained on failure.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from interfaces.lifecycle.core import digest
from interfaces.lifecycle.merge import LocalGit
from support.local_files import require
from validation.lifecycle.fixture import create, evidence, grant, write


def run_demo(destination=None, with_delivery=False, simulate_approvals=False):
    require(simulate_approvals is True, 'explicit --simulate-approvals required; never real approval')
    require(type(with_delivery) is bool, 'delivery opt-in must be boolean')
    if destination is None:
        base = Path(tempfile.gettempdir()).resolve()
        require(base != ROOT and ROOT not in base.parents, 'temporary root must be outside checkout')
        output = Path(tempfile.mkdtemp(prefix='code-inspection-demo-', dir=base))
    else:
        output = Path(destination).absolute()
        require(not output.exists() and not output.is_symlink(), 'NEW output directory required')
        output = output.resolve()
        require(output != ROOT and ROOT not in output.parents, 'output must be outside checkout')
        output.mkdir(parents=True)
    log = output / 'commands'
    log.mkdir()
    summary = {'schema': 'offline-demo/v1', 'result': 'INCOMPLETE', 'output': str(output),
               'review_and_approvals': 'SYNTHETIC, not model review or human authorization',
               'git': 'REAL isolated bare Git, no remote, no checkout',
               'delivery': 'LOCAL SIMULATION' if with_delivery else 'NOT_RUN (not selected)',
               'steps': [], 'source_hashes': {}}
    sources = ['examples/lifecycle_demo.py', 'validation/lifecycle/fixture.py',
               'support/local_files.py', 'support/git_runtime.py']
    sources += ['interfaces/lifecycle/' + x + '.py' for x in ['core', 'review', 'merge', 'delivery', 'cli']]
    summary['source_hashes'] = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in sources}

    def call(command, record=None, operation=None):
        argv = [sys.executable, *(['-O'] if sys.flags.optimize else []), '-B',
                str(ROOT / 'interfaces/lifecycle/cli.py'), '--host-root', str(host),
                '--policy-sha', pin, '--input-root', str(inputs)]
        if record:
            argv += ['--record', record]
        if operation:
            argv += ['--operation', operation]
        argv.append(command)
        index = len(summary['steps'])
        name = '%02d-%s.json' % (index, command)
        try:
            run = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            write(log, name, {'argv': argv, 'exit_code': None, 'status': 'UNKNOWN',
                             'timeout_seconds': 20, 'stdout': decoded(exc.stdout),
                             'stderr': decoded(exc.stderr), 'reason': 'query outcome; never blindly retry'})
            summary['steps'].append({'command': command, 'log': 'commands/' + name,
                                     'exit_code': None, 'status': 'UNKNOWN'})
            raise
        write(log, name, {'argv': argv, 'exit_code': run.returncode,
                         'stdout': run.stdout, 'stderr': run.stderr})
        summary['steps'].append({'command': command, 'log': 'commands/' + name,
                                 'exit_code': run.returncode})
        require(run.returncode == 0, 'CLI error; inspect ' + str(log / name))
        return json.loads(run.stdout)

    def expect(value, **fields):
        for name, expected in fields.items():
            require(value.get(name) == expected, '%s expected %r, got %r' % (name, expected, value.get(name)))

    def batch(entries):
        write(inputs, 'batch.json', {'schema': 'review-batch/v1', 'evidence': entries, 'findings': []})
        return call('ingest', 'batch.json')

    def action(name, parameters):
        write(host, 'action.json', {'schema': 'delivery-plan/v1', 'action': name, 'parameters': parameters})
        plan = call('delivery-plan', 'action.json')
        grant(host, pin, 'delivery-' + name, plan['id'])  # Fixture-only simulated person.
        result = call('delivery-execute', operation=plan['id'])
        expect(result, simulation=True)
        expect(result['result'], result='confirmed')
        return plan, result

    def observe(complete=True):
        state = json.loads((host / 'sim-provider.json').read_text(encoding='utf-8'))
        config = policy['delivery']['observation']
        end = datetime.fromisoformat(state['effective_at']) + timedelta(seconds=config['duration_seconds'])
        remaining = (end - datetime.now(timezone.utc)).total_seconds()
        require(remaining < 1, 'demo does not run production observation windows')
        if remaining > 0:
            time.sleep(remaining + .002)  # Only the fixture's 10 ms window.
        record = {k: state[k] for k in ['artifact', 'target', 'cohort', 'config']}
        record.update(schema='observation/v1', window_start=state['effective_at'], window_end=end.isoformat(),
                      source=config['source'], query=config['query'], samples=20, value=.1,
                      complete=complete, observed_at=datetime.now(timezone.utc).isoformat())
        write(inputs, 'observation.json', record)
        return call('observe', 'observation.json')

    try:
        fixture = create(output / 'fixture')
        host, inputs = Path(fixture['host']), Path(fixture['inputs'])
        policy = json.loads((host / 'policy.json').read_text(encoding='utf-8'))
        policy['delivery']['enabled'] = with_delivery
        if not with_delivery:
            for capability in policy['delivery']['capabilities'].values():
                capability.update(enabled=False, required=False)
        write(host, 'policy.json', policy)
        pin = hashlib.sha256((host / 'policy.json').read_bytes()).hexdigest()
        summary['configuration'] = dict(host_root=str(host), input_root=str(inputs), policy_sha=pin)

        # Pre-PR: a real, tiny local content check. No pretend AI/security review.
        handoff = json.loads((host / 'handoff.json').read_text(encoding='utf-8'))
        identity = handoff['subject']
        git = LocalGit(policy['merge'])
        old = git.run('show', identity['base'] + ':note.txt')
        new = git.run('show', identity['head'] + ':note.txt')
        require(old.returncode == new.returncode == 0, 'fixture content unavailable: '+repr([(x.returncode,x.stdout[:1024],x.stderr[:1024]) for x in (old,new)]))
        require(old.stdout == 'self-owned baseline\n' and new.stdout == 'self-owned candidate\n',
                'self-owned note requirement failed')
        snapshot = output / 'pre-pr-workspace'
        snapshot.mkdir()
        (snapshot / 'note.txt').write_bytes(new.stdout.encode())
        manifest = {'note.txt': hashlib.sha256((snapshot / 'note.txt').read_bytes()).hexdigest()}
        identity['worktree_digest'] = digest(manifest)
        identity['ci_config_digest'] = digest(policy['checks'])
        identity['artifact_digest'] = manifest['note.txt']
        pre_pr = {'kind': 'real deterministic content check on self-owned fixture',
                  'requirement': 'Replace the exact baseline note with the exact candidate note.',
                  'expected': 'self-owned candidate\n', 'actual': new.stdout, 'status': 'PASS',
                  'subject': identity, 'uncommitted_snapshot_manifest': manifest,
                  'limitations': 'No model review, application journey or real security scan performed.'}
        write(output, 'pre-pr-record.json', pre_pr)
        handoff.update(requirements_ref=str(output / 'pre-pr-record.json'),
                       history_ref=str(output / 'pre-pr-record.json'))
        write(host, 'handoff.json', handoff)
        expect(call('init', 'handoff.json'), recommendation='HOLD')
        expect(call('begin-review'), recommendation='HOLD')
        expect(call('pr-status'), recommendation='HOLD')

        checks = [c for c in policy['checks'] if c['stage'] == 'pr']
        entries = [evidence(inputs, identity, c) for c in checks]
        entries[-1].update(status='UNKNOWN', complete=False)
        incomplete = batch(entries)
        expect(incomplete, recommendation='HOLD')
        require(any(c['id'] == 'security' and c['status'] == 'UNKNOWN' for c in incomplete['checks']),
                'incomplete security evidence must remain UNKNOWN')
        replacement = evidence(inputs, identity, checks[-1], 'security-complete')
        expect(batch([replacement]), recommendation='HOLD')  # No simulated person yet.
        grant(host, pin, 'pr-approve', digest(identity))
        expect(call('pr-status'), recommendation='READY_FOR_NEXT')

        binding = digest(dict(repository=fixture['repo'], base=identity['base'],
                              head=identity['head'], policy_sha=pin))
        grant(host, pin, 'merge-prepare', binding)
        write(host, 'merge.json', dict(schema='merge-plan/v1', base=identity['base'],
                                      head=identity['head'], integration_artifact='demo-combination-artifact'))
        prepared = call('merge-prepare', 'merge.json')
        expect(prepared, recommendation='HOLD')
        plan = prepared['plan']
        expect(call('merge-preflight'), recommendation='HOLD')
        integration = next(c for c in policy['checks'] if c['stage'] == 'merge')
        batch([evidence(inputs, plan['subject'], integration)])  # Synthetic integration claim.
        grant(host, pin, 'merge-approve', digest(plan['subject']))
        grant(host, pin, 'merge-execute', plan['id'])
        expect(call('merge-preflight'), recommendation='READY_FOR_NEXT')
        merged = call('merge-execute')
        expect(merged, result='confirmed', simulation=False, isolated_reference=True)
        expect(call('merge-query'), result='confirmed')

        if with_delivery:
            action('deploy', {'artifact': plan['subject']['artifact_digest']})
            action('flag-set', {'enabled': True, 'cohort': 'internal'})
            expect(observe(complete=False), status='UNKNOWN')
            expect(call('delivery-status'), recommendation='HOLD')
            expect(observe(), status='PASS')
            promotion, _ = action('promote', {'artifact': plan['subject']['artifact_digest'], 'channel': 'stable'})
            expect(call('delivery-query', operation=promotion['id'])['result'], result='confirmed')
            expect(observe(), status='PASS')  # Promotion changed simulated config/window.
            expect(call('delivery-status'), recommendation='READY_FOR_NEXT')
        else:
            skipped = call('delivery-status')
            expect(skipped, recommendation='HOLD')
            require(skipped['checks'][0]['status'] == 'NOT_RUN', 'unselected stage is not accepted online')
        exported = call('export')
        expect(exported, publication='NOT_RUN')
        summary.update(result='PASS', subject=identity, budget=exported['state']['budget'],
                       merge_candidate=plan['candidate'], final_handoff='handoff.json')
        write(output, 'handoff.json', exported)
        return summary
    except Exception as exc:
        summary.update(result='FAIL', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        write(output, 'summary.json', summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='NEW directory outside this checkout; otherwise fresh system temp')
    parser.add_argument('--simulate-approvals', action='store_true', help='explicit fixture-only approval simulation')
    parser.add_argument('--with-delivery', action='store_true', help='opt into local simulated delivery')
    args = parser.parse_args()
    try:
        summary = run_demo(args.output, args.with_delivery, args.simulate_approvals)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'result': 'FAIL', 'error': str(exc), 'note': 'Any created output is retained.'}))
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
