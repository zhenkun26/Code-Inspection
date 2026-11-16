"""Allowlisted diagnostic artifacts, never raw logs, fixtures or gate authority."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPORT_ENV = 'CODE_INSPECTION_CI_REPORT_DIR'
STATUSES = {'NOT_RUN', 'RUNNING', 'PASS', 'FAIL', 'TIMEOUT', 'ERROR', 'INTERRUPTED'}


def require(condition):
    if not condition:
        raise ValueError('invalid public CI report')


def report_path(name):
    require(name in {'native', 'maintained', 'mutations'} |
            {'receipt-' + phase for phase in ('maintained', 'mutations', 'observe', 'optimized', 'aggregate')})
    root = os.environ.get(REPORT_ENV)
    return Path(root) / (name + '.json') if root else None


def child_environment(env):
    # Test processes must not inherit authority to overwrite public phase reports.
    result = dict(env)
    result.pop(REPORT_ENV, None)
    return result


def context():
    run = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                         check=True, timeout=10)
    sha = run.stdout.decode('ascii').strip()
    require(re.fullmatch('[0-9a-f]{40}', sha) is not None)
    result = dict(sha=sha, workflow_sha256=hashlib.sha256(
        (ROOT / '.github/workflows/ci.yml').read_bytes()).hexdigest(),
        distribution_manifest_sha256=hashlib.sha256((ROOT / 'SHA256SUMS').read_bytes()).hexdigest(),
        python='.'.join(str(n) for n in sys.version_info[:3]),
        platform=sys.platform if sys.platform in {'linux', 'darwin', 'win32'} else 'other')
    for name in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        value = os.environ.get(name)
        if value is not None:
            require(re.fullmatch('[1-9][0-9]{0,19}', value) is not None)
            result[name.lower()] = value
    return result


def integer(value):
    require(value is None or type(value) is int and -(2**31) <= value < 2**31)
    return value


def duration(value):
    require(value is None or type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86400)
    return None if value is None else round(value, 6)


def check_rows(commands, summary):
    from validation import run_checks
    known = run_checks.build_commands(ROOT)
    require(len(commands) == len(summary) and 0 < len(commands) <= len(known))
    result = []
    for number, (command, row) in enumerate(zip(commands, summary)):
        # Copy only source-defined commands; never paths or text from log output.
        require(command in known and row['command'] == command[1:])
        require(type(row['status']) is str and row['status'] in STATUSES)
        item = dict(id=number, command=command[1:], status=row['status'],
                    exit_code=integer(row['exit_code']), duration_seconds=duration(row['duration_seconds']))
        if 'descendant_cleanup' in row:
            require(row['descendant_cleanup'] == 'UNKNOWN')
            item['descendant_cleanup'] = 'UNKNOWN'
        result.append(item)
    return result


def mutation_rows(cases, records, active=None):
    from validation import mutation_check
    require(cases == mutation_check.platform_cases(os.name))
    require(len(records) <= len(cases))
    require(active is None or type(active) is int and active == len(records) < len(cases))
    result = []
    for index, case in enumerate(cases):
        item = dict(id=case[0], result='NOT_RUN', baseline_exit_code=None, exit_code=None)
        if index == active:
            item['result'] = 'RUNNING'
        if index < len(records):
            row = records[index]
            require(row['mutation'] == case[0] and row['result'] in {'KILLED', 'NOT_VERIFIED'})
            item.update(result=row['result'], baseline_exit_code=integer(row['baseline_exit_code']),
                        exit_code=integer(row.get('exit_code')))
        result.append(item)
    return result


class Report:
    def __init__(self, path, kind):
        require(kind in {'checks', 'mutations'})
        self.path = Path(path)
        self.kind = kind
        self.context = context()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Never silently replace a previous phase/run at initialization.
        with self.path.open('x', encoding='ascii') as handle:
            handle.write('{}\n')

    def write(self, rows):
        report = dict(schema='ci-diagnostic/v1', kind=self.kind, context=self.context,
                      records=rows, acceptance='NOT_ESTABLISHED')
        raw = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2).encode('ascii')
        require(len(raw) <= 1048576)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('xb') as handle:
            handle.write(raw)
        temporary.replace(self.path)
