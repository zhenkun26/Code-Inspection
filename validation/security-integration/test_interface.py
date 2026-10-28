"""Bounded synthetic interface tests. No scanner, network or product execution."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.test_support import temporary_directory
CLI = ROOT / 'interfaces/security-tools/normalize_result.py'


def samples():
    now = datetime.now(timezone.utc)
    subject = dict(base='base-fixture', head='head-fixture', worktree_digest='fixture-worktree',
                   ci_config_digest='fixture-ci', artifact_digest='fixture-artifact')
    request = dict(schema='security-request/v1', request_id='simulated-001',
                   policy_binding='user-designated-synthetic-policy', required=True,
                   expected_subject=subject, expected_tool=dict(name='self-owned-sample', version='1'),
                   expected_config_digest='fixture-config', required_scope=['fixture.txt'],
                   accepted_data_destination='none', valid_from_utc=(now-timedelta(hours=1)).isoformat(),
                   valid_until_utc=(now+timedelta(hours=1)).isoformat())
    raw = b'SIMULATED tool record; no actual security scanner executed.\n'
    result = dict(schema='security-result/v1', request_id=request['request_id'], subject=subject.copy(),
                  tool=request['expected_tool'].copy(), config_digest='fixture-config', scope=['fixture.txt'],
                  complete=True, execution=dict(outcome='completed', exit_code=0), findings=[],
                  raw_evidence=[dict(path='raw.txt', sha256=hashlib.sha256(raw).hexdigest())],
                  data_destination='none', license=dict(identifier='self-owned-test-data',
                  source='local fixture; no third-party material', review_state='reviewed'),
                  observed_at_utc=now.isoformat())
    return request, result, raw


class Interface(unittest.TestCase):
    def invoke(self, change=None, expected='UNKNOWN', exitcode=0, missing=False, required=True, setup=None):
        for optimized in (False, True):
            with self.subTest(optimized=optimized), temporary_directory() as directory:
                root=Path(directory); request,result,raw=samples(); request['required']=required
                if change: change(request,result)
                (root/'request.json').write_bytes((json.dumps(request)).encode('utf-8'));(root/'result.json').write_bytes((json.dumps(result)).encode('utf-8'))
                (root/'raw.txt').write_bytes(raw)
                if setup: setup(root)
                command=[sys.executable]+(['-O'] if optimized else [])+['-B',str(CLI),'--root',str(root),'--request','request.json']
                if not missing: command+=['--result','result.json']
                p=subprocess.run(command,capture_output=True,text=True,timeout=10)
                self.assertEqual(p.returncode,exitcode,p.stderr+p.stdout)
                record=json.loads(p.stdout);self.assertEqual(record['status'],expected,record)
                self.assertFalse(record['authorizes_progress'])
                if exitcode == 0:
                    self.assertEqual(record['request_binding'], request)
                    self.assertEqual(record['config_digest'], request['expected_config_digest'])
                    self.assertEqual(record['input_records']['request']['sha256'], hashlib.sha256((root/'request.json').read_bytes()).hexdigest())
                    if not missing and 'result' in record['input_records']:
                        self.assertEqual(record['input_records']['result']['sha256'], hashlib.sha256((root/'result.json').read_bytes()).hexdigest())

    def test_valid_simulated_record(self): self.invoke(expected='PASS')
    def test_absent_required(self): self.invoke(missing=True,expected='BLOCKED')
    def test_absent_optional(self): self.invoke(missing=True,required=False,expected='NOT_RUN')
    def test_unknown_schema(self): self.invoke(lambda q,r:r.update(schema='mystery'),exitcode=2)
    def test_command_config_rejected(self): self.invoke(lambda q,r:q.update(command='do not execute'),exitcode=2)
    def test_version(self): self.invoke(lambda q,r:r['tool'].update(version='2'))
    def test_revision(self): self.invoke(lambda q,r:r['subject'].update(head='other'))
    def test_config(self): self.invoke(lambda q,r:r.update(config_digest='other'))
    def test_external_destination(self): self.invoke(lambda q,r:r.update(data_destination='unapproved'))
    def test_incomplete(self): self.invoke(lambda q,r:r.update(complete=False))
    def test_coverage(self): self.invoke(lambda q,r:r.update(scope=[]))
    def test_stale(self): self.invoke(lambda q,r:r.update(observed_at_utc='2000-01-01T00:00:00+00:00'))
    def test_expired_request(self): self.invoke(lambda q,r:q.update(valid_from_utc='2000-01-01T00:00:00+00:00',valid_until_utc='2001-01-01T00:00:00+00:00'))
    def test_timeout(self): self.invoke(lambda q,r:r.update(execution=dict(outcome='timeout',exit_code=None),complete=False))
    def test_error(self): self.invoke(lambda q,r:r.update(execution=dict(outcome='error',exit_code=2),complete=False))
    def test_unknown(self): self.invoke(lambda q,r:r.update(execution=dict(outcome='unknown',exit_code=None),complete=False))
    def test_nonzero(self): self.invoke(lambda q,r:r.update(execution=dict(outcome='completed',exit_code=1)))
    def test_not_run(self): self.invoke(lambda q,r:r.update(execution=dict(outcome='not_run',exit_code=None),complete=False,scope=[],raw_evidence=[]),expected='NOT_RUN')
    def test_blocked(self): self.invoke(lambda q,r:r.update(execution=dict(outcome='blocked',exit_code=None),complete=False,scope=[],raw_evidence=[]),expected='BLOCKED')
    def test_candidate(self): self.invoke(lambda q,r:r.update(findings=[dict(id='F1',location='fixture.txt:1',trigger='synthetic',impact='synthetic',evidence_ref='raw.txt',tool_severity='high')]))
    def test_missing_evidence(self): self.invoke(lambda q,r:r.update(raw_evidence=[]))
    def test_bad_digest(self): self.invoke(lambda q,r:r['raw_evidence'][0].update(sha256='incorrect'),exitcode=2)
    def test_license_unknown(self): self.invoke(lambda q,r:r['license'].update(review_state='unknown'))
    def test_duplicate_json(self): self.invoke(setup=lambda root:(root/'request.json').write_bytes(('{"schema":1,"schema":2}').encode('utf-8')),exitcode=2)
    def test_timestamp_underflow(self):
        self.invoke(lambda q,r:q.update(valid_from_utc='0001-01-01T00:00:00+01:00'),exitcode=2)
    def test_timestamp_overflow(self):
        self.invoke(lambda q,r:r.update(observed_at_utc='9999-12-31T23:59:59-01:00'),exitcode=2)
    def test_deep_request_json(self):
        self.invoke(setup=lambda root:(root/'request.json').write_bytes(('['*2000+'0'+']'*2000).encode('utf-8')),exitcode=2)
    def test_deep_result_json(self):
        self.invoke(setup=lambda root:(root/'result.json').write_bytes(('['*2000+'0'+']'*2000).encode('utf-8')),exitcode=2)
    def test_escape(self): self.invoke(lambda q,r:r['raw_evidence'][0].update(path='../outside'),exitcode=2)
    def test_absolute_path(self): self.invoke(lambda q,r:r['raw_evidence'][0].update(path='/etc/hosts'),exitcode=2)
    def test_symlink(self):
        def setup(root):
            (root/'linked-raw.txt').symlink_to(root/'raw.txt')
        self.invoke(lambda q,r:r['raw_evidence'][0].update(path='linked-raw.txt'),setup=setup,exitcode=2)
    def test_directory_symlink(self):
        def setup(root): (root/'linked').symlink_to(root,target_is_directory=True)
        self.invoke(lambda q,r:r['raw_evidence'][0].update(path='linked/raw.txt'),setup=setup,exitcode=2)
    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'N_A: POSIX FIFO; native reparse controls are separate')
    def test_fifo(self):
        import os
        def setup(root): os.mkfifo(root/'fifo.txt')
        self.invoke(lambda q,r:r['raw_evidence'][0].update(path='fifo.txt'),setup=setup,exitcode=2)


if __name__ == '__main__': unittest.main(verbosity=2)
