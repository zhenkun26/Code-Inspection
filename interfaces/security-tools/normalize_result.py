"""Read-only optional security-result interface v1. Never invokes a scanner.

CLI exit 0 means a normalized record was produced, not that a PR is approved.
Exit 2 means invalid/unsafe/unreadable input; stdout still reports UNKNOWN.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.local_files import checked_root, decode_json, digest, read_bytes, require


def keys(obj, required, optional=()):
    require(isinstance(obj, dict), 'object required')
    require(set(required) <= set(obj) <= set(required) | set(optional), 'unknown or missing fields')


def strings(values):
    require(isinstance(values, list) and all(isinstance(x, str) and x.strip() for x in values),
            'nonempty string list required')
    require(len(values) == len(set(values)), 'duplicate list entry')


def nonempty(value):
    require(isinstance(value, str) and bool(value.strip()), 'nonempty string required')


def normalize(root, request_name, result_name=None):
    root = checked_root(root)
    request_bytes = read_bytes(root, request_name)
    request = decode_json(request_bytes)
    keys(request, ['schema', 'request_id', 'policy_binding', 'required', 'expected_subject',
                   'expected_tool', 'expected_config_digest', 'required_scope',
                   'accepted_data_destination', 'valid_from_utc', 'valid_until_utc'])
    require(request['schema'] == 'security-request/v1', 'unsupported request schema')
    nonempty(request['request_id']); nonempty(request['policy_binding'])
    require(type(request['required']) is bool, 'required must be boolean')
    keys(request['expected_subject'], ['base', 'head', 'worktree_digest', 'ci_config_digest', 'artifact_digest'])
    for v in request['expected_subject'].values(): nonempty(v)
    keys(request['expected_tool'], ['name', 'version'])
    for v in request['expected_tool'].values(): nonempty(v)
    nonempty(request['expected_config_digest']); nonempty(request['accepted_data_destination'])
    strings(request['required_scope']); require(bool(request['required_scope']), 'scope cannot be empty')
    from datetime import datetime, timezone
    def timestamp(value):
        nonempty(value)
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(d.tzinfo is not None, 'timestamp timezone required')
        return d.astimezone(timezone.utc)
    valid_from = timestamp(request['valid_from_utc'])
    valid_until = timestamp(request['valid_until_utc'])
    require(valid_from <= valid_until, 'invalid validity window')
    now = datetime.now(timezone.utc)
    output = {'schema': 'security-evidence/v1', 'request_id': request['request_id'],
              'subject': request['expected_subject'],
              'request_binding': request,
              'input_records': {'request': {'path': request_name, 'sha256': digest(request_bytes)}},
              'config_digest': request['expected_config_digest'], 'status': 'UNKNOWN', 'reasons': [],
              'findings': [], 'raw_evidence': [], 'authorizes_progress': False,
              'limitations': ['Record consistency only; no proof of authorship or tool effectiveness.']}
    if now < valid_from or now > valid_until:
        output['reasons'] = ['trusted request outside validity window']; return output
    if result_name is None:
        output['status'] = 'BLOCKED' if request['required'] else 'NOT_RUN'
        output['reasons'] = ['optional interface not configured; A core remains usable']
        return output
    result_bytes = read_bytes(root, result_name)
    result = decode_json(result_bytes)
    output['input_records']['result'] = {'path': result_name, 'sha256': digest(result_bytes)}
    keys(result, ['schema', 'request_id', 'subject', 'tool', 'config_digest', 'scope', 'complete',
                  'execution', 'findings', 'raw_evidence', 'data_destination', 'license', 'observed_at_utc'])
    require(result['schema'] == 'security-result/v1', 'unsupported result schema')
    keys(result['execution'], ['outcome', 'exit_code'])
    outcome = result['execution']['outcome']
    require(outcome in {'completed', 'not_run', 'blocked', 'timeout', 'error', 'unknown'}, 'unknown outcome')
    require(result['execution']['exit_code'] is None or type(result['execution']['exit_code']) is int,
            'invalid exit_code')
    require(type(result['complete']) is bool, 'complete must be boolean')
    strings(result['scope']); keys(result['license'], ['identifier', 'source', 'review_state'])
    for value in result['license'].values(): nonempty(value)
    require(result['license']['review_state'] in {'reviewed', 'unknown'}, 'invalid license review state')
    require(isinstance(result['findings'], list) and isinstance(result['raw_evidence'], list), 'lists required')
    observed = timestamp(result['observed_at_utc'])
    reasons = output['reasons']
    if observed < valid_from or observed > now or observed > valid_until: reasons.append('invalid observation time')
    # Request lifetime is an explicit, trusted freshness policy, not an arbitrary fixed age.
    for field, expected in [('request_id',request['request_id']), ('subject',request['expected_subject']),
                            ('tool',request['expected_tool']), ('config_digest',request['expected_config_digest']),
                            ('data_destination',request['accepted_data_destination'])]:
        if result[field] != expected: reasons.append(field + ' mismatch')
    if outcome == 'completed' and not set(request['required_scope']) <= set(result['scope']): reasons.append('required coverage missing')
    if outcome == 'completed' and not result['complete']: reasons.append('incomplete result')
    if result['license']['review_state'] != 'reviewed': reasons.append('license handling unknown')
    ids = set()
    for finding in result['findings']:
        keys(finding, ['id', 'location', 'trigger', 'impact', 'evidence_ref', 'tool_severity'])
        for value in finding.values(): nonempty(value)
        require(finding['id'] not in ids, 'duplicate finding id'); ids.add(finding['id'])
        output['findings'].append(dict(finding, fact_state='candidate', disposition='unreviewed'))
    refs = set()
    for evidence in result['raw_evidence']:
        keys(evidence, ['path', 'sha256'])
        nonempty(evidence['path']); nonempty(evidence['sha256'])
        actual = digest(read_bytes(root, evidence['path']))
        require(actual == evidence['sha256'], 'raw evidence digest mismatch')
        require(evidence['path'] not in refs, 'duplicate raw evidence');refs.add(evidence['path'])
        output['raw_evidence'].append(evidence)
    require(all(f['evidence_ref'] in refs for f in output['findings']), 'finding evidence not present')
    output.update(tool=result['tool'], execution=result['execution'], complete=result['complete'],
                  scope=result['scope'], license=result['license'], data_destination=result['data_destination'],
                  observed_at_utc=result['observed_at_utc'])
    if reasons:
        return output
    if outcome in {'not_run', 'blocked'}:
        output['status'] = {'not_run':'NOT_RUN','blocked':'BLOCKED'}[outcome]
        reasons.append('tool did not complete');return output
    if outcome != 'completed' or result['execution']['exit_code'] != 0:
        reasons.append('execution error/timeout/unknown or nonzero raw exit status');return output
    if not refs:
        reasons.append('completed run lacks readable raw evidence');return output
    if output['findings']:
        reasons.append('candidate findings require verification; not confirmed vulnerabilities');return output
    output['status'] = 'PASS'
    reasons.append('matched complete tool evidence with no reported candidates; limited to declared coverage')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--result')
    args = parser.parse_args()
    try:
        output = normalize(args.root, args.request, args.result)
    except (ValueError, OSError, UnicodeError, TypeError, OverflowError, RecursionError) as exc:
        print(json.dumps({'schema':'security-evidence/v1','status':'UNKNOWN','reasons':[str(exc)],
                          'authorizes_progress':False}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
