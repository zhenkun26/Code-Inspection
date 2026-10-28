"""Bounded regression tests for self-owned validation helpers; no scanners/network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.test_support import temporary_directory

HELPERS = ROOT / 'validation/pre-pr-review'
sys.path.insert(0, str(HELPERS))
import make_fixtures


def run(name, *args, optimized=False):
    return subprocess.run([sys.executable, *(['-O'] if optimized else []), '-B', str(HELPERS / name),
                           *map(str, args)], text=True, capture_output=True, timeout=20)


class HelperSafetyTests(unittest.TestCase):
    def make_skill(self, root, case):
        skill = root / case / 'sample-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_bytes(('---\nname: sample-skill\ndescription: Local test.\n---\n# Entry\n').encode('utf-8'))
        return skill

    def test_normal_and_optimized_boundaries(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized), temporary_directory() as directory:
                root = Path(directory)
                skill = self.make_skill(root, 'basic')
                entry = skill / 'SKILL.md'
                good = '---\nname: sample-skill\ndescription: Local test.\n---\n# Entry\n'
                (entry).write_bytes((good).encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 0)
                (entry).write_bytes((good.replace('name: sample-skill', 'name: wrong')).encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 2)
                (entry).write_bytes((good + '\n[missing](missing.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 2)
                external = root / 'outside.md'; (external).write_bytes(('innocuous local sample').encode('utf-8'))
                (entry).write_bytes((good + '\n[out](../outside.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 2)
                (entry).write_bytes((good).encode('utf-8'))
                (skill / 'symlink.md').symlink_to(external)
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 2)
                directory_skill = self.make_skill(root, 'directory-symlink')
                (directory_skill / 'directory-link').symlink_to(root, target_is_directory=True)
                self.assertEqual(run('structure_check.py', directory_skill, optimized=optimized).returncode, 2)
                linked = root / 'linked-skill'; linked.symlink_to(directory_skill, target_is_directory=True)
                self.assertEqual(run('structure_check.py', linked, optimized=optimized).returncode, 2)
                fixture = root / 'fixtures'
                self.assertEqual(run('make_fixtures.py', fixture, optimized=optimized).returncode, 2)
                self.assertFalse(fixture.exists())
                made = run('make_fixtures.py', fixture, '--trusted-fixture', optimized=optimized)
                self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
                self.assertEqual(run('make_fixtures.py', fixture, '--trusted-fixture', optimized=optimized).returncode, 2)
                self.assertEqual(run('check_fixture_facts.py', fixture, optimized=optimized).returncode, 2)
                facts = run('check_fixture_facts.py', fixture, '--trusted-fixture', optimized=optimized)
                self.assertEqual(facts.returncode, 0, facts.stdout + facts.stderr)
                self.assertEqual(json.loads(facts.stdout)['case_a']['actual_total'], 310)
                marker = root / 'must-not-exist'
                (fixture / 'case-a/pricing.py').write_bytes(('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\n').encode('utf-8'))
                self.assertEqual(run('check_fixture_facts.py', fixture, '--trusted-fixture', optimized=optimized).returncode, 2)
                self.assertFalse(marker.exists(), 'unknown fixture was executed')
                linked_fixture = root / 'linked-fixture'
                (linked_fixture / 'case-a').mkdir(parents=True)
                (linked_fixture / 'case-a/pricing.py').symlink_to(external)
                self.assertEqual(run('check_fixture_facts.py', linked_fixture, '--trusted-fixture', optimized=optimized).returncode, 2)

    def test_unrouted_resource_rejected(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized), temporary_directory() as directory:
                root = Path(directory)
                skill = self.make_skill(root, 'unrouted')
                (skill / 'orphan.md').write_bytes(('# Valid but unreferenced resource\n').encode('utf-8'))
                result = run('structure_check.py', skill, optimized=optimized)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['reason'], 'unrouted resource')
                self.assertEqual(run('structure_check.py', skill, '--repo-root', root,
                                     optimized=optimized).returncode, 2)
                (skill / 'orphan.md').write_bytes(('# Unreachable cycle\n[self](orphan.md)\n').encode('utf-8'))
                result = run('structure_check.py', skill, optimized=optimized)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['reason'], 'unrouted resource')
                entry = skill / 'SKILL.md'
                (entry).write_bytes((entry.read_text(encoding='utf-8') + '\n[resource](orphan.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 0)

    def test_all_repository_skill_entries(self):
        for optimized in (False, True):
            for name in ['pre-pr-review', 'pr-in-review', 'merge-review', 'delivery-review']:
                with self.subTest(optimized=optimized, skill=name):
                    result = run('structure_check.py', ROOT / 'skills' / name,
                                 '--repo-root', ROOT, optimized=optimized)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)['result'], 'PASS')

    def test_repository_boundary_is_explicit_and_no_follow(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized), temporary_directory() as directory:
                root = Path(directory)
                repo = root / 'repo'; repo.mkdir()
                skill = self.make_skill(repo, 'skills')
                shared = repo / 'shared'; shared.mkdir()
                (shared / 'contract.md').write_bytes(('# Shared contract\n').encode('utf-8'))
                entry = skill / 'SKILL.md'; good = entry.read_text(encoding='utf-8')
                (entry).write_bytes((good + '\n[contract](../../shared/contract.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, optimized=optimized).returncode, 2)
                valid = run('structure_check.py', skill, '--repo-root', repo, optimized=optimized)
                self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
                (root / 'outside.md').write_bytes(('# Outside selected repository\n').encode('utf-8'))
                (entry).write_bytes((good + '\n[escape](../../../outside.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, '--repo-root', repo, optimized=optimized).returncode, 2)
                (repo / 'linked.md').symlink_to(shared / 'contract.md')
                (entry).write_bytes((good + '\n[linked](../../linked.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, '--repo-root', repo, optimized=optimized).returncode, 2)
                (repo / 'linked-directory').symlink_to(shared, target_is_directory=True)
                (entry).write_bytes((good + '\n[linked](../../linked-directory/contract.md)\n').encode('utf-8'))
                self.assertEqual(run('structure_check.py', skill, '--repo-root', repo, optimized=optimized).returncode, 2)
                (repo / 'linked-skills').symlink_to(repo / 'skills', target_is_directory=True)
                self.assertEqual(run('structure_check.py', repo / 'linked-skills/sample-skill',
                                     '--repo-root', repo, optimized=optimized).returncode, 2)
                self.assertEqual(run('structure_check.py', skill, '--repo-root', shared,
                                     optimized=optimized).returncode, 2)

    def test_real_timeout_of_own_benign_runner(self):
        # Deliberately replace a trusted built-in constant in test memory only.
        old_source, old_timeout = make_fixtures.APP, make_fixtures.TIMEOUT_SECONDS
        try:
            make_fixtures.APP = 'import time\ntime.sleep(2)\n'
            make_fixtures.TIMEOUT_SECONDS = 0.03
            with self.assertRaises(subprocess.TimeoutExpired):
                make_fixtures.known_test_run(make_fixtures.APP, make_fixtures.TESTS)
        finally:
            make_fixtures.APP, make_fixtures.TIMEOUT_SECONDS = old_source, old_timeout

    def test_git_and_python_environment_isolation(self):
        original = os.environ.get('GIT_CONFIG_COUNT')
        os.environ['GIT_CONFIG_COUNT'] = '999'
        try:
            environment = make_fixtures.safe_environment()
            self.assertNotIn('GIT_CONFIG_COUNT', environment)
            self.assertEqual(environment['GIT_CONFIG_GLOBAL'], os.devnull)
            self.assertFalse(any(k.startswith('PYTHON') for k in environment))
        finally:
            if original is None: os.environ.pop('GIT_CONFIG_COUNT', None)
            else: os.environ['GIT_CONFIG_COUNT'] = original


if __name__ == '__main__':
    unittest.main(verbosity=2)
