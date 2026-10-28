"""Run the maintained structural, regression and distribution checks locally."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.test_support import temporary_directory

TESTS = ['security-integration/test_interface.py', 'security-integration/test_helpers.py',
         'security-integration/test_local_files.py', 'lifecycle/test_lifecycle.py',
         'lifecycle/test_review_regressions.py', 'lifecycle/test_delivery_regressions.py',
         'lifecycle/test_demo.py', 'test_hygiene.py', 'test_native_files.py', 'test_codex_layout.py']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep-artifacts', action='store_true', help='retain fixtures without deleting them')
    args = parser.parse_args()
    if args.keep_artifacts:
        os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = '1'
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    # The summary/log directory is always retained as the verification receipt.
    old = os.environ.get('CODE_INSPECTION_KEEP_TEST_ARTIFACTS')
    os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = '1'
    output = Path(temporary_directory().name)
    if old is None:
        os.environ.pop('CODE_INSPECTION_KEEP_TEST_ARTIFACTS', None)
    else:
        os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = old
    commands = []
    skill_paths = list((ROOT / 'skills').iterdir())
    if (ROOT / '.agents/skills').is_dir():
        skill_paths.extend((ROOT / '.agents/skills').iterdir())
    for skill in sorted(skill_paths):
        if (skill / 'SKILL.md').is_file():
            commands.append([sys.executable, '-B', 'validation/pre-pr-review/structure_check.py',
                             str(skill.relative_to(ROOT)), '--repo-root', '.'])
    for optimized in (False, True):
        commands.extend([[sys.executable, *(['-O'] if optimized else []), '-B', 'validation/' + name]
                         for name in TESTS])
    commands.append([sys.executable, '-B', 'validation/hygiene_check.py'])
    summary = []
    for number, command in enumerate(commands):
        run = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
        (output / ('check-%02d.log' % number)).write_bytes((run.stdout + run.stderr).encode('utf-8'))
        record = {'command': command[1:], 'exit_code': run.returncode}
        summary.append(record)
        print(json.dumps(record), flush=True)
        if run.returncode:
            # Only trusted self-owned test output; retain full logs in the receipt.
            print((run.stdout + run.stderr)[-20000:], flush=True)
    (output / 'summary.json').write_bytes((json.dumps(summary, indent=2)).encode('utf-8'))
    print(json.dumps({'result': 'PASS' if all(r['exit_code'] == 0 for r in summary) else 'FAIL',
                      'receipt_directory': str(output)}))
    return int(any(r['exit_code'] != 0 for r in summary))


if __name__ == '__main__':
    sys.exit(main())
