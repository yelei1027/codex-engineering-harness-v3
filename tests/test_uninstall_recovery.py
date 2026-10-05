"""Actual process interruption through uninstall and the installed rescue entry."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
import test_public_lifecycle as fixtures


class UninstallRecoveryCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.bundle(self.fixture.release());result=self.fixture.install_release()
        self.assertEqual(result.returncode,0,result.stderr)
        self.root=self.fixture.target
        self.fixture.public(self.root,'prepare',self.fixture.repo,'--apply','--profile','L1')
        self.database=self.root/'private/context-mode/memory.db'
        self.database.parent.mkdir(parents=True);self.database.write_bytes(b'uninstall crash memory')
        self.launcher=self.fixture.base/'bin/harness'

    def interrupt(self):
        script='''import os,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from harness_core import cli,movement
original=movement.move
root=Path(sys.argv[1])
def crash(log,source,target,kind):
 result=original(log,source,target,kind)
 if Path(source)==root/'bin/harness':os.kill(os.getpid(),signal.SIGKILL)
 return result
movement.move=crash
sys.argv=['harness','uninstall','--apply'];cli.main()
'''
        result=subprocess.run([sys.executable,'-c',script,str(self.root)],cwd=self.fixture.repo,env=self.fixture.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,-signal.SIGKILL,result.stdout+result.stderr)
        self.assertFalse((self.root/'bin/harness').exists());self.assertFalse(self.database.exists())
        self.journal=next((self.fixture.state/'uninstalls').glob('*.json'))
        self.record=json.loads(self.journal.read_text());self.identifier=self.record['id']

    def rescue(self,apply=False):
        return subprocess.run([sys.executable,str(self.launcher),'uninstall-recover',self.identifier,*(['--apply'] if apply else [])],cwd=self.fixture.repo,env=self.fixture.env,capture_output=True,text=True)

    def test_global_rescue_restores_removed_entry_data_and_project_config(self):
        accepted=(self.fixture.state/'accepted.json').read_bytes()
        project=(self.fixture.repo/'.harness/state/project.json').read_bytes()
        config=(self.fixture.repo/'.codex/config.toml').read_bytes()
        self.interrupt()
        preview=self.rescue();self.assertEqual(preview.returncode,0,preview.stdout+preview.stderr)
        self.assertEqual(json.loads(preview.stdout)['status'],'UNINSTALL_RECOVERY_PLANNED')
        self.assertFalse(self.database.exists())
        result=self.rescue(True);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'FILES_RESTORED')
        self.assertEqual(self.database.read_bytes(),b'uninstall crash memory')
        self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),accepted)
        self.assertEqual((self.fixture.repo/'.harness/state/project.json').read_bytes(),project)
        self.assertEqual((self.fixture.repo/'.codex/config.toml').read_bytes(),config)
        self.assertEqual(self.rescue(True).returncode,0)

    def test_recovery_conflict_preserves_new_entry_and_all_retained_data(self):
        self.interrupt();entry=self.root/'bin/harness';entry.write_text('user replacement after crash')
        result=self.rescue(True);self.assertNotEqual(result.returncode,0)
        self.assertEqual(entry.read_text(),'user replacement after crash');self.assertFalse(self.database.exists())
        retained=Path(self.record['plan']['preserved_data']['local_install_data'])
        self.assertEqual((retained/'private/context-mode/memory.db').read_bytes(),b'uninstall crash memory')
        self.assertFalse((self.fixture.repo/'.harness/state/project.json').exists())

    def test_changed_rescue_software_is_refused_before_execution(self):
        self.interrupt();runtime=Path(self.record['recovery_runtime']['directory'])
        sentinel=self.fixture.base/'unexpected-execution'
        (runtime/'entry.py').write_text('from pathlib import Path;Path('+repr(str(sentinel))+').touch()')
        result=self.rescue(True);self.assertNotEqual(result.returncode,0)
        self.assertFalse(sentinel.exists());self.assertFalse(self.database.exists())

    def test_rescue_rejects_unknown_import_artifacts_before_execution(self):
        self.interrupt();runtime=Path(self.record['recovery_runtime']['directory'])
        extra=runtime/'harness_core/__pycache__/common.cpython-312.pyc'
        extra.parent.mkdir();extra.write_bytes(b'unknown later import artifact')
        result=self.rescue(True);self.assertNotEqual(result.returncode,0)
        self.assertIn('Unknown recovery files',result.stderr)
        self.assertEqual(extra.read_bytes(),b'unknown later import artifact')
        self.assertFalse(self.database.exists())

    def test_copied_other_operation_runtime_is_refused(self):
        self.interrupt();runtime=Path(self.record['recovery_runtime']['directory'])
        receipt=json.loads((runtime/'receipt.json').read_text());receipt['operation_id']='different-operation'
        (runtime/'receipt.json').write_text(json.dumps(receipt))
        result=self.rescue(True);self.assertNotEqual(result.returncode,0)
        self.assertIn('Recovery operation identity differs',result.stderr)
        self.assertFalse(self.database.exists())

    def test_normal_uninstall_error_cleans_owned_recovery_software(self):
        script="import sys\nsys.path.insert(0,sys.argv[1])\nfrom harness_core import cli\nfrom harness_core.common import HarnessError\ndef expected_error(repo):raise HarnessError('fixture ordinary setup error')\ncli.disable=expected_error\nsys.argv=['harness','uninstall','--apply'];cli.main()\n"
        result=subprocess.run([sys.executable,'-c',script,str(self.root)],cwd=self.fixture.repo,env=self.fixture.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        record=json.loads(next((self.fixture.state/'uninstalls').glob('*.json')).read_text())
        self.assertEqual(record['status'],'FILES_RESTORED')
        self.assertFalse(Path(record['recovery_runtime']['directory']).exists())
        self.assertEqual(self.database.read_bytes(),b'uninstall crash memory')
        self.assertTrue((self.fixture.repo/'.harness/state/project.json').exists())
