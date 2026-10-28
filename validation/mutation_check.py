"""Bounded regression-quality checks on retained, self-owned source copies.

This is a fixed list of known guard removals, not a general mutation framework
or a claim about comprehensive mutation coverage. Never mutates the checkout.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.test_support import temporary_directory

CASES = [
    ('merge-cas', 'interfaces/lifecycle/merge.py',
     "plan['candidate'],plan['base'])", "plan['candidate'])",
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_execute_cas_rejects_drift_after_preflight'),
    ('leaf-no-follow', 'support/local_files.py',
     'os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK', 'os.O_RDONLY | os.O_NONBLOCK',
     'validation/security-integration/test_local_files.py', 'LocalFileSafetyTests.test_symlink_evidence_rejected_even_with_matching_digest'),
    ('evidence-status', 'interfaces/lifecycle/review.py',
     "require(e['status'] in STATES,'unknown status')", 'None',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_unknown_status_raw_digest_and_duplicate_id_rejected'),
    ('evidence-expiry', 'interfaces/lifecycle/review.py',
     "if not timestamp(e['observed_at'])<=now()<=timestamp(e['expires_at']):return 'UNKNOWN'", 'if False:return \'UNKNOWN\'',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_invalid_evidence_overrides_an_otherwise_ready_baseline'),
    ('raw-digest', 'interfaces/lifecycle/review.py',
     "require(bytes_digest(read_bytes(root,e['raw']['path']))==e['raw']['sha256'],'raw evidence digest mismatch')", 'None',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_unknown_status_raw_digest_and_duplicate_id_rejected'),
    ('duplicate-evidence', 'interfaces/lifecycle/review.py',
     "require(not any(old['id']==e['id'] for old in s.state['evidence']),'evidence ID already present; new attempt needs new ID')", 'None',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_unknown_status_raw_digest_and_duplicate_id_rejected'),
    ('git-timeout', 'interfaces/lifecycle/merge.py', 'timeout=10)', 'timeout=None)',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_git_subprocess_timeout_is_enforced'),
    ('duplicate-json', 'support/local_files.py',
     "require(key not in result, 'duplicate JSON key')", 'None',
     'validation/security-integration/test_local_files.py', 'LocalFileSafetyTests.test_duplicate_keys_in_otherwise_valid_records'),
    ('unrouted-resource', 'validation/pre-pr-review/structure_check.py',
     "require({root_relative / name for name in files if name != 'SKILL.md'} <= routed,\n            'unrouted resource')", 'None',
     'validation/security-integration/test_helpers.py', 'HelperSafetyTests.test_unrouted_resource_rejected'),
]


def main():
    os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = '1'
    output = Path(temporary_directory().name)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    sources = []
    for folder in ('interfaces', 'support', 'skills', 'validation', 'docs'):
        for path in (ROOT / folder).rglob('*'):
            if (path.is_file() and not path.is_symlink() and path.suffix in {'.py', '.md', '.json', '.txt'}
                    and not any(part in {'results', 'history', 'reviews', '__pycache__'} for part in path.relative_to(ROOT).parts)):
                sources.append(path)
    cases = CASES
    if os.name == 'nt':
        cases = [x for x in CASES if x[0] != 'leaf-no-follow'] + [
            ('windows-no-reparse', 'support/windows_files.py', 'OBJ_DONT_REPARSE = 0x1000', 'OBJ_DONT_REPARSE = 0',
             'validation/test_native_files.py', 'WindowsFiles.test_junction_and_symlink_all_positions'),
            ('windows-lock', 'support/windows_files.py',
             'wincheck(LockFileEx(handle, 3, 0, 1, 0, c.byref(ov))); acquired = True', 'acquired = False',
             'validation/test_native_files.py', 'PortableFiles.test_lock_contention_and_exception_release'),
        ]
    results = []
    for name, filename, before, after, test_file, test in cases:
        copy = output / name
        for path in sources:
            target = copy / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        target = copy / filename
        source = target.read_text(encoding='utf-8')
        if source.count(before) != 1:
            raise ValueError('mutation anchor must be unique: ' + name)
        (target).write_bytes((source.replace(before, after, 1)).encode('utf-8'))
        run = subprocess.run([sys.executable, '-B', test_file, test], cwd=copy, env=env,
                             text=True, capture_output=True, timeout=90)
        (output / (name + '.log')).write_bytes((run.stdout + run.stderr).encode('utf-8'))
        # An import/runtime error is not proof that the intended invariant was tested.
        killed = run.returncode == 1 and 'FAIL:' in run.stderr and 'ERROR:' not in run.stderr
        record = {'mutation': name, 'result': 'KILLED' if killed else 'NOT_VERIFIED', 'exit_code': run.returncode}
        results.append(record)
        print(json.dumps(record), flush=True)
    (output / 'summary.json').write_bytes((json.dumps(results, indent=2)).encode('utf-8'))
    print(json.dumps({'receipt_directory': str(output), 'scope': str(len(cases)) + ' fixed platform guard removals'}))
    return int(any(r['result'] != 'KILLED' for r in results))


if __name__ == '__main__':
    sys.exit(main())
