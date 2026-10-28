"""Acceptance facts for explicitly trusted, self-generated fixtures only.

Python >=3.9 and Git required. Not a general repository runner or sandbox.
All executable Python is compared with built-in constants, then executed from
verified bytes in an isolated subprocess; fixture policy cannot authorize code.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.local_files import is_link, checked_root, read_bytes, read_json, require
from make_fixtures import APP, BUG, TESTS, REQUIREMENTS, TIMEOUT_SECONDS, safe_environment, git


def inspect(base, trusted_fixture=False):
    require(trusted_fixture is True, 'explicit --trusted-fixture acknowledgement required')
    base = checked_root(base)
    entries = list(base.rglob('*'))
    require(len(entries) <= 256 and not any(is_link(p) for p in entries), 'fixture size/symlink boundary')
    a, b = base / 'case-a', base / 'case-b'
    sources = {}
    for label in ('case-a', 'case-b'):
        sources[label] = read_bytes(base, label + '/pricing.py').decode('utf-8')
        require(read_bytes(base, label + '/test_pricing.py').decode('utf-8') == TESTS, 'unknown test source')
        require(read_bytes(base, label + '/requirements.md').decode('utf-8') == REQUIREMENTS, 'unknown requirements')
        config = read_bytes(base, label + '/.git/config').decode('utf-8')
        lines = [line.strip().lower() for line in config.splitlines() if line.strip()]
        allowed = {'[core]', 'repositoryformatversion = 0', 'filemode = true', 'filemode = false', 'bare = false',
                   'logallrefupdates = true', 'ignorecase = true', 'precomposeunicode = true', 'symlinks = false'}
        require(set(lines) <= allowed and '[core]' in lines and 'bare = false' in lines, 'unexpected Git config')
        require(not (base / label / '.git/commondir').exists(), 'shared Git directory not allowed')
        require(not (base / label / '.git/objects/info/alternates').exists(), 'Git alternates not allowed')
    require(sources['case-a'] == BUG, 'unknown executable fixture source')
    expected_b = APP.replace('ordered = sorted', '# Local implementation revision\n    ordered = sorted')
    require(sources['case-b'] == expected_b, 'unknown case-b source')
    payload = {'items': [{'unit_cents': 120, 'quantity': 2}, {'unit_cents': 80, 'quantity': 1}], 'coupon_percent': 10}
    # Execute the already verified built-in string, never import/run a mutable path.
    command = [sys.executable, '-I', '-B', '-c', sources['case-a']]
    execution = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                               check=True, timeout=TIMEOUT_SECONDS, env=safe_environment())
    actual = json.loads(execution.stdout)['total_cents']
    expected = 320 - 320 * 10 // 100
    require(actual == 310 and expected == 288, 'fixture defect no longer present')
    probe = "import json\nnamespace={'__name__':'fixture'}\nexec(" + repr(BUG) + ",namespace)\nrows=" + repr(payload['items']) + "\nbefore=json.dumps(rows)\nnamespace['total'](rows,10)\nprint(json.dumps({'unchanged':json.dumps(rows)==before}))"
    mutation = subprocess.run([sys.executable, '-I', '-B', '-c', probe], capture_output=True, text=True,
                              check=True, timeout=TIMEOUT_SECONDS, env=safe_environment())
    require(json.loads(mutation.stdout)['unchanged'] is True, 'discussion rebuttal no longer valid')
    previous = read_json(base, 'case-b/previous-evidence.json')
    current_hash = hashlib.sha256(sources['case-b'].encode()).hexdigest()
    require(previous['subject']['pricing.py'] != current_hash, 'previous evidence must be stale')
    policy = read_json(base, 'case-b/policy.json')
    require(policy['budget']['rounds_used'] == policy['budget']['max_rounds'] == 3, 'budget fixture changed')
    require(policy['authorization']['verify'] is False, 'verification boundary changed')
    require('python-3.12-target-acceptance' in policy['required'], 'target requirement changed')
    for root in (a, b):
        try:
            git(root, 'rev-parse', '--verify', 'HEAD')
        except subprocess.CalledProcessError:
            pass
        else:
            raise ValueError('fixture unexpectedly has a commit')
        require(git(root, 'diff', '--no-ext-diff', '--no-textconv', '--', 'pricing.py').stdout, 'unstaged diff required')
    return {
        'case_a': {'execution': 'isolated Python -c of source matched to built-in BUG', 'input': payload,
                   'stdout': execution.stdout, 'expected_total': expected, 'actual_total': actual,
                   'discussion_claim_mutates_input': False},
        'case_b': {'current_source_hash': current_hash, 'previous_source_hash': previous['subject']['pricing.py'],
                   'previous_PASS_is_for_different_source': True, 'rounds_remaining': 0, 'verify_authorized': False,
                   'target_requirement': 'python-3.12-target-acceptance', 'product_executed_by_this_checker': False},
        'both': {'unborn_HEAD': True, 'unstaged_diff_present': True},
        'limits': 'self-owned fixtures only; timeout is not a sandbox or hard resource budget',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture_directory')
    parser.add_argument('--trusted-fixture', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(Path(args.fixture_directory), args.trusted_fixture), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        print(json.dumps({'result': 'REJECTED', 'reason': str(error)}, ensure_ascii=False))
        sys.exit(2)
