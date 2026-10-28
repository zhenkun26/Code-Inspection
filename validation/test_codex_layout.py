"""Offline complete-checkout relocation tests, not native host discovery."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from support.local_files import read_bytes
from support.git_runtime import git_environment
from validation import hygiene_check
from validation.test_support import temporary_directory

spec=importlib.util.spec_from_file_location('layout_structure', ROOT/'validation/pre-pr-review/structure_check.py')
structure=importlib.util.module_from_spec(spec);spec.loader.exec_module(structure)
ENTRY=Path('.agents/skills/code-inspection')

class CodexLayout(unittest.TestCase):
    def copy_distribution(self, target):
        names=[line.split('  ',1)[1] for line in read_bytes(ROOT,'SHA256SUMS').decode('utf-8').splitlines()]+['SHA256SUMS']
        errors=hygiene_check.check_manifest(ROOT,names)
        self.assertEqual(errors,[])
        target.mkdir()
        for name in names:
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(read_bytes(ROOT,name))
        return names

    def test_complete_copy_keeps_all_routes_and_distribution(self):
        with temporary_directory() as directory:
            target=Path(directory)/'copy 中文';names=self.copy_distribution(target)
            subprocess.run(['git','init','--template=',str(target)],env=git_environment(),capture_output=True,check=True,timeout=10)
            self.assertEqual(hygiene_check.check_manifest(target,names),[])
            for skill in [target/ENTRY,*sorted((target/'skills').iterdir())]:
                self.assertEqual(structure.check(skill,repo_root=target)['result'],'PASS')
            with patch.object(hygiene_check,'ROOT',target):
                result=hygiene_check.check()
            self.assertEqual(result['result'],'PASS',result)

    def test_missing_shared_contract_is_rejected(self):
        with temporary_directory() as directory:
            target=Path(directory)/'copy';names=self.copy_distribution(target)
            (target/'skills/pre-pr-review/references/contract.md').unlink()
            with self.assertRaises((ValueError,OSError)):
                structure.check(target/'skills/pr-in-review',repo_root=target)
            self.assertTrue(hygiene_check.check_manifest(target,names))

    def test_detached_entry_is_not_a_standalone_package(self):
        with temporary_directory() as directory:
            target=Path(directory)/'copy';entry=target/ENTRY;entry.mkdir(parents=True)
            (entry/'SKILL.md').write_bytes(read_bytes(ROOT,ENTRY/'SKILL.md'))
            with self.assertRaises((ValueError,OSError)):
                structure.check(entry,repo_root=target)

if __name__=='__main__':unittest.main(verbosity=2)
