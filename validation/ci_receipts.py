"""Fixed Windows CI phases and bounded, run-bound success receipts.

Receipts are completeness checks inside the trusted workflow, not signatures or
authentication of a runner. No caller-supplied commands or receipt paths exist.
The original runners own their fresh retained fixtures and their timeout policy.
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation import mutation_check, run_checks
from validation import windows_temporary_qualification as qualification
from validation.test_support import temporary_directory

VERSION = 1
LIMIT = 24 * 1024  # One native Windows environment variable must fit UTF-16.
FILE_LIMIT = 1024 * 1024
PLATFORM = 'windows-2025-cpython-3.13'
CONTEXT_KEYS = {'sha', 'tree', 'workflow_ref', 'workflow_sha256', 'run_id',
                'run_attempt', 'platform'}
KINDS = {'maintained', 'mutations', 'observe', 'optimized', 'aggregate'}


def require(condition):
    if not condition:
        raise ValueError('invalid or incomplete CI receipt')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False)


def strict_json(raw, limit=LIMIT):
    require(type(raw) is str and len(raw) <= limit)
    require(len(raw.encode('utf-8')) <= limit)

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def constant(_):
        raise ValueError('invalid or incomplete CI receipt')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def digest(value):
    return hashlib.sha256(encode(value).encode('ascii')).hexdigest()


def valid_context(context):
    require(type(context) is dict and set(context) == CONTEXT_KEYS)
    require(all(type(value) is str for value in context.values()))
    for key in ('sha', 'tree'):
        require(re.fullmatch('[0-9a-f]{40}', context[key]) is not None)
    require(re.fullmatch('[0-9a-f]{64}', context['workflow_sha256']) is not None)
    for key in ('run_id', 'run_attempt'):
        require(re.fullmatch('[1-9][0-9]{0,19}', context[key]) is not None)
    require(context['platform'] == PLATFORM)
    require(len(context['workflow_ref']) <= 512 and re.fullmatch(
        r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/ci\.yml@refs/[A-Za-z0-9_./@+-]+',
        context['workflow_ref']) is not None)


def owned_info(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    return info


def read_owned(path, limit=FILE_LIMIT):
    """Read a fixed file in our freshly allocated, retained output directory."""
    info = owned_info(path)
    require(info.st_size <= limit)
    with path.open('rb') as handle:
        require(os.fstat(handle.fileno()) == info)
        data = handle.read(limit + 1)
    require(len(data) <= limit)
    return data


def git_value(*args):
    run = subprocess.run(['git', *args], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, timeout=10)
    require(run.returncode == 0 and len(run.stdout) <= 128)
    return run.stdout.decode('ascii').strip()


def current_context(env):
    # RUNNER_OS alone is untrusted input; inspect the actual native runtime too.
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_OS') == 'Windows')
    require(qualification.target_supported(qualification.runtime_facts()))
    sha = git_value('rev-parse', 'HEAD')
    require(env.get('GITHUB_SHA') == sha)
    clean = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    require(clean.returncode == 0)
    context = dict(sha=sha, tree=git_value('rev-parse', 'HEAD^{tree}'),
                   workflow_ref=env.get('GITHUB_WORKFLOW_REF'),
                   workflow_sha256=hashlib.sha256(read_owned(ROOT / '.github/workflows/ci.yml', LIMIT)).hexdigest(),
                   run_id=env.get('GITHUB_RUN_ID'), run_attempt=env.get('GITHUB_RUN_ATTEMPT'),
                   platform=PLATFORM)
    valid_context(context)
    return context


def maintained_ids():
    return ['native-normal', 'native-optimized'] + [
        'check-%02d-' % number + digest(command[1:])
        for number, command in enumerate(run_checks.build_commands(ROOT))]


def expected_ids(kind):
    if kind == 'maintained':
        return maintained_ids()
    if kind == 'mutations':
        return [case[0] for case in mutation_check.platform_cases('nt')]
    if kind in {'observe', 'optimized'}:
        return qualification.EXPECTED_LABELS
    require(kind == 'aggregate')
    return ['maintained', 'mutations', 'observe', 'optimized']


def validate_receipt(raw, kind, context):
    valid_context(context)
    require(kind in KINDS)
    receipt = strict_json(raw)
    require(type(receipt) is dict and set(receipt) == {'version', 'kind', 'context', 'records'})
    require(type(receipt['version']) is int and receipt['version'] == VERSION)
    require(type(receipt['kind']) is str and receipt['kind'] == kind)
    valid_context(receipt['context'])
    require(receipt['context'] == context)
    rows = receipt['records']
    require(type(rows) is list and len(rows) == len(expected_ids(kind)))
    require(all(type(row) is dict for row in rows))
    if kind in {'observe', 'optimized'}:
        # Reuse the full code-only native protocol, including every negative
        # control and the exact observation order, rather than trusting PASS.
        report = qualification.envelope(observations=rows, complete=True)
        qualification.parse_report(encode(report).encode('ascii'))
    else:
        require([row.get('id') for row in rows] == expected_ids(kind))
        for row in rows:
            if kind == 'mutations':
                require(set(row) == {'id', 'result', 'baseline_exit_code', 'exit_code'})
                require(row['result'] == 'KILLED' and type(row['baseline_exit_code']) is int
                        and row['baseline_exit_code'] == 0 and type(row['exit_code']) is int
                        and row['exit_code'] == 1)
            elif kind == 'aggregate':
                require(set(row) == {'id', 'status', 'receipt_sha256'} and row['status'] == 'PASS')
                require(type(row['receipt_sha256']) is str
                        and re.fullmatch('[0-9a-f]{64}', row['receipt_sha256']) is not None)
            else:
                require(set(row) == {'id', 'status', 'exit_code'} and row['status'] == 'PASS')
                require(type(row['exit_code']) is int and row['exit_code'] == 0)
    return receipt


def receipt_json(kind, context, records):
    raw = encode(dict(version=VERSION, kind=kind, context=context, records=records))
    validate_receipt(raw, kind, context)
    return raw


def upstream(env, context):
    require(env.get('CI_MAINTAINED_RESULT') == 'success')
    return validate_receipt(env.get('CI_MAINTAINED_RECEIPT'), 'maintained', context)


def retained_output():
    os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = '1'
    return Path(temporary_directory().name)


def successful_checks(output, commands):
    summary = strict_json(read_owned(output / 'summary.json').decode('utf-8'), FILE_LIMIT)
    require(type(summary) is list and len(summary) == len(commands))
    for number, (row, command) in enumerate(zip(summary, commands)):
        expected = {'command', 'status', 'exit_code', 'duration_seconds', 'log'}
        cases = [arg for arg in command if arg.startswith('validation.lifecycle.test_lifecycle.Lifecycle.')]
        if cases:
            expected.add('test_cases')
        require(type(row) is dict and set(row) == expected)
        require(row['command'] == command[1:] and row['status'] == 'PASS'
                and type(row['exit_code']) is int and row['exit_code'] == 0
                and row['log'] == 'check-%02d.log' % number)
        require(type(row['duration_seconds']) in (float, int)
                and math.isfinite(row['duration_seconds']) and row['duration_seconds'] >= 0)
        if cases:
            require(row['test_cases'] == cases)
        owned_info(output / ('check-%02d.log' % number))


def run_maintained(context):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', CODE_INSPECTION_KEEP_TEST_ARTIFACTS='1')
    native = [[sys.executable, *mode, '-B', 'validation/test_windows_manifests.py']
              for mode in ([], ['-O'])]
    native_output = retained_output()
    require(run_checks.run_commands(native, native_output, env) == 0)
    successful_checks(native_output, native)
    commands = run_checks.build_commands(ROOT)
    output = retained_output()
    require(run_checks.run_commands(commands, output, env) == 0)
    successful_checks(output, commands)
    return receipt_json('maintained', context, [dict(id=name, status='PASS', exit_code=0)
                                              for name in maintained_ids()])


def successful_mutations(output, cases):
    summary = strict_json(read_owned(output / 'summary.json').decode('utf-8'), FILE_LIMIT)
    require(type(summary) is list and len(summary) == len(cases))
    records = []
    for row, case in zip(summary, cases):
        require(type(row) is dict and set(row) == {'mutation', 'result', 'baseline_exit_code', 'exit_code'})
        require(row['mutation'] == case[0] and row['result'] == 'KILLED')
        require(type(row['baseline_exit_code']) is int and row['baseline_exit_code'] == 0
                and type(row['exit_code']) is int and row['exit_code'] == 1)
        # Read only our fixed filenames, never a path announced by a child.
        owned_info(output / (case[0] + '-baseline.log'))
        owned_info(output / (case[0] + '.log'))
        records.append(dict(id=case[0], result='KILLED', baseline_exit_code=0, exit_code=1))
    return records


def run_mutations(context):
    output = retained_output()
    cases = mutation_check.platform_cases('nt')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', CODE_INSPECTION_KEEP_TEST_ARTIFACTS='1')
    # The trusted existing runner classifies actual stderr as FAIL/no ERROR
    # only after its same-copy positive control passes. Its nonzero result
    # cannot be overridden by a KILLED string or a child-announced directory.
    require(mutation_check.run_cases(cases, output, env) == 0)
    return receipt_json('mutations', context, successful_mutations(output, cases))


def run_observation(context):
    require(sys.flags.optimize in (0, 1))
    # main retains its independent fresh fixture and original worker55/parent60
    # limits. Its only output is the bounded, already sanitized native report.
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        code = qualification.main(['--authorize-owned-fixture'])
    raw = captured.getvalue().encode('utf-8')
    report = qualification.parse_report(raw)
    require(code == 0 and report['complete'] is True)
    output = retained_output()
    (output / 'observation.json').write_bytes(raw)
    report = qualification.parse_report(read_owned(output / 'observation.json', qualification.OUTPUT_LIMIT))
    return receipt_json('optimized' if sys.flags.optimize else 'observe', context, report['observations'])


def aggregate(env, context, maintained):
    receipts = [('maintained', maintained)]
    for kind, prefix in [('mutations', 'CI_MUTATION'), ('observe', 'CI_OBSERVE'),
                         ('optimized', 'CI_OPTIMIZED')]:
        require(env.get(prefix + '_RESULT') == 'success')
        receipts.append((kind, validate_receipt(env.get(prefix + '_RECEIPT'), kind, context)))
    return receipt_json('aggregate', context, [dict(id=kind, status='PASS', receipt_sha256=digest(value))
                                             for kind, value in receipts])


def emit(raw, env):
    require(type(raw) is str and len(raw.encode('ascii')) <= LIMIT and '\n' not in raw and '\r' not in raw)
    line = 'receipt=' + raw + '\n'
    require(len(line.encode('utf-16-le')) < 1024 * 1024)
    path = env.get('GITHUB_OUTPUT')
    require(type(path) is str and bool(path))
    # GitHub supplies the output file; do not create an arbitrary path or relay
    # any raw filesystem location in the portable receipt.
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    with open(path, 'a', encoding='ascii', newline='\n') as handle:
        require(os.fstat(handle.fileno()).st_ino == info.st_ino)
        require(handle.write(line) == len(line))
        handle.flush()
    print(encode(dict(event='CI_RECEIPT', kind=strict_json(raw)['kind'], result='PASS')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['maintained', 'mutations', 'observe', 'aggregate'])
    args = parser.parse_args(argv)
    try:
        context = current_context(os.environ)
        if args.phase == 'maintained':
            raw = run_maintained(context)
        else:
            maintained = upstream(os.environ, context)
            if args.phase == 'mutations':
                raw = run_mutations(context)
            elif args.phase == 'observe':
                raw = run_observation(context)
            else:
                raw = aggregate(os.environ, context, maintained)
        emit(raw, os.environ)
        return 0
    except (Exception, KeyboardInterrupt):
        # No untrusted receipt contents, paths or raw exceptions reach CI output.
        print(encode(dict(event='CI_RECEIPT', phase=args.phase, result='FAIL')), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
