"""Run the maintained structural, regression and distribution checks locally."""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.test_support import temporary_directory

TESTS = ['security-integration/test_interface.py', 'security-integration/test_helpers.py',
         'security-integration/test_local_files.py', 'lifecycle/test_lifecycle.py',
         'lifecycle/test_review_regressions.py', 'lifecycle/test_delivery_regressions.py',
         'lifecycle/test_merge_integrity.py', 'lifecycle/test_delivery_integrity.py',
         'lifecycle/test_applicability.py', 'lifecycle/test_budget_accounting.py',
         'lifecycle/test_budget_attempts.py', 'lifecycle/test_artifact_content.py',
         'lifecycle/test_build_provenance.py', 'lifecycle/test_host_delivery.py',
         'lifecycle/test_temporary_inspection.py', 'lifecycle/test_owned_temporary.py',
         'lifecycle/test_source_content.py', 'test_source_manifests.py',
         'test_bounded_process.py', 'test_windows_manifests.py',
         'lifecycle/test_demo.py', 'test_hygiene.py', 'test_native_files.py', 'test_codex_layout.py',
         'test_check_runner.py']


def lifecycle_shards(source):
    """Partition the direct Lifecycle methods; refuse unsupported discovery shapes."""
    tree = ast.parse(Path(source).read_text(encoding='utf-8'))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    error = 'unsupported Lifecycle discovery shape; update the coverage-preserving partition'
    if any((isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'load_tests')
           or (isinstance(node, ast.Name) and node.id == 'load_tests') for node in ast.walk(tree)):
        raise ValueError(error)
    if len(classes) != 1:
        raise ValueError(error)
    cls = classes[0]
    if (cls.name != 'Lifecycle' or cls.decorator_list or cls.keywords or len(cls.bases) != 1
            or ast.dump(cls.bases[0]) != ast.dump(ast.Attribute(value=ast.Name(id='unittest', ctx=ast.Load()),
                                                               attr='TestCase', ctx=ast.Load()))):
        raise ValueError(error)
    methods = []
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            methods.append(node)
        elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                  and isinstance(node.value.value, str)):
            raise ValueError(error)
    if len({node.name for node in methods}) != len(methods):
        raise ValueError(error)
    tests = [node for node in methods if node.name.startswith('test_')]
    if (len(tests) < 2 or any(node.decorator_list for node in tests)
            or any(isinstance(node, ast.Name) and node.id == 'Lifecycle' for node in ast.walk(tree))
            or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name.startswith('test_') and node not in tests for node in ast.walk(cls))):
        raise ValueError(error)
    names = sorted('validation.lifecycle.test_lifecycle.Lifecycle.' + node.name for node in tests)
    return [names[::2], names[1::2]]


def build_commands(root):
    commands = []
    shards = lifecycle_shards(root / 'validation/lifecycle/test_lifecycle.py')
    skill_paths = list((root / 'skills').iterdir())
    if (root / '.agents/skills').is_dir():
        skill_paths.extend((root / '.agents/skills').iterdir())
    for skill in sorted(skill_paths):
        if (skill / 'SKILL.md').is_file():
            commands.append([sys.executable, '-B', 'validation/pre-pr-review/structure_check.py',
                             str(skill.relative_to(root)), '--repo-root', '.'])
    for optimized in (False, True):
        python = [sys.executable, *(['-O'] if optimized else []), '-B']
        for name in TESTS:
            if name == 'lifecycle/test_lifecycle.py':
                commands.extend([python + ['-m', 'unittest', '-v', *cases] for cases in shards])
            else:
                commands.append(python + ['validation/' + name])
    commands.append([sys.executable, '-B', 'validation/hygiene_check.py'])
    return commands


def run_commands(commands, output, env, timeout=180):
    """Retain output as it arrives; a timeout stops checks with cleanup unknown."""
    print(json.dumps({'receipt_directory': str(output)}), flush=True)
    summary = []
    for number, command in enumerate(commands):
        record = {'command': command[1:], 'status': 'NOT_RUN', 'exit_code': None,
                  'duration_seconds': None, 'log': 'check-%02d.log' % number}
        cases = [arg for arg in command if arg.startswith('validation.lifecycle.test_lifecycle.Lifecycle.')]
        if cases:
            record['test_cases'] = cases
        summary.append(record)

    def checkpoint():
        temporary = output / 'summary.json.tmp'
        temporary.write_bytes(json.dumps(summary, indent=2).encode('utf-8'))
        temporary.replace(output / 'summary.json')

    checkpoint()
    try:
        for command, record in zip(commands, summary):
            record['status'] = 'RUNNING'
            checkpoint()
            print(json.dumps({'event': 'START', 'command': record['command'],
                              'timeout_seconds': timeout, 'log': record['log']}), flush=True)
            started = time.monotonic()
            log_path = output / record['log']
            try:
                with log_path.open('wb') as log:
                    try:
                        run = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                             stderr=subprocess.STDOUT, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        record.update(status='TIMEOUT', exit_code=124, timeout_seconds=timeout,
                                      descendant_cleanup='UNKNOWN')
                    except OSError as exc:
                        log.write((type(exc).__name__ + ': ' + str(exc) + '\n').encode('utf-8'))
                        record.update(status='ERROR', exit_code=125)
                    else:
                        record.update(status='PASS' if run.returncode == 0 else 'FAIL', exit_code=run.returncode)
            finally:
                record['duration_seconds'] = time.monotonic() - started
                checkpoint()
            print(json.dumps(record), flush=True)
            if record['exit_code'] != 0:
                # Only trusted self-owned test output; raw bytes remain in the receipt.
                with log_path.open('rb') as log:
                    log.seek(0, os.SEEK_END)
                    log.seek(max(0, log.tell() - 20000))
                    print(log.read(20000).decode('utf-8', errors='replace'), flush=True)
            if record['status'] == 'TIMEOUT':
                # subprocess.run kills its direct child, not a proven descendant tree.
                break
    finally:
        checkpoint()
        failed = any(record['exit_code'] != 0 for record in summary)
        print(json.dumps({'result': 'FAIL' if failed else 'PASS', 'receipt_directory': str(output)}), flush=True)
    return int(failed)


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
    return run_commands(build_commands(ROOT), output, env)


if __name__ == '__main__':
    sys.exit(main())
