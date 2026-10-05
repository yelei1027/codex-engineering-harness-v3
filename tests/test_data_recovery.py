"""Public durable data recovery after interrupted moves; fully isolated files."""
import json
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path
import test_public_lifecycle as fixtures


class DurableRecoveryCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.env['PYTHONDONTWRITEBYTECODE']='1';self.root=self.fixture.release()
        private=self.root/'private/context';private.mkdir(parents=True);(private/'memory.txt').write_text('original private bytes')
        runtime=self.root/'runtimes/test-component';runtime.mkdir(parents=True)
        (runtime/'program.py').write_text('owned software');(runtime/'memory.sqlite').write_bytes(b'database bytes')
        (runtime/'.env').write_text('PRIVATE_OPTION=retained')
        self.code('from harness_core.software import capture;capture(root,["runtimes/test-component"])')
        self.fixture.public(self.root,'install')

    def code(self,body):
        source='import sys;from pathlib import Path;sys.path.insert(0,'+repr(str(self.root))+');root=Path('+repr(str(self.root))+');'+body
        return subprocess.run([sys.executable,'-c',source],env=self.fixture.env,cwd=self.root,capture_output=True,text=True,check=True)

    def uninstall(self):
        _,removed=self.fixture.public(self.root,'uninstall','--apply');self.saved=Path(removed['preserved_data']['local_install_data'])

    def interrupt(self,point):
        script='''import os,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from harness_core import cli,movement,recovery
point=sys.argv[2]
if point=='move':
 original=movement.move
 def one(log,source,target,kind):
  result=original(log,source,target,kind)
  if kind=='DATA_RECOVERY':os.kill(os.getpid(),signal.SIGKILL)
  return result
 movement.move=one
else:
 original=recovery.write_json
 def one(path,value):
  result=original(path,value)
  if point=='service' and str(path).endswith('/.harness-install/services.json'):os.kill(os.getpid(),signal.SIGKILL)
  if point=='complete' and Path(path).parent.name=='recoveries' and value.get('status')=='DATA_RESTORED':os.kill(os.getpid(),signal.SIGKILL)
  return result
 recovery.write_json=one
sys.argv=['harness','recover-data','--apply'];cli.main()
'''
        result=subprocess.run([sys.executable,'-c',script,str(self.root),point],env=self.fixture.env,cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,-signal.SIGKILL,result.stdout+result.stderr)
        descriptor=json.loads((self.saved/'recovery.json').read_text());self.assertIn('pending_recovery',descriptor)
        self.journal=self.fixture.state/'recoveries'/(descriptor['pending_recovery']+'.json')

    def assert_restored(self):
        self.assertEqual((self.root/'private/context/memory.txt').read_text(),'original private bytes')
        self.assertEqual((self.root/'runtimes/test-component/memory.sqlite').read_bytes(),b'database bytes')
        self.assertEqual((self.root/'runtimes/test-component/.env').read_text(),'PRIVATE_OPTION=retained')
        self.assertFalse((self.root/'runtimes/test-component/program.py').exists())

    def test_public_retry_continues_after_first_move_and_preview_does_not_write(self):
        self.uninstall();self.interrupt('move');before=self.journal.read_bytes()
        _,preview=self.fixture.public(self.root,'recover-data')
        self.assertTrue(preview['continuation']);self.assertEqual(preview['already_moved'],1)
        self.assertEqual(self.journal.read_bytes(),before)
        self.fixture.public(self.root,'recover-data','--apply');self.assert_restored()
        _,again=self.fixture.public(self.root,'recover-data','--apply');self.assertEqual(again['status'],'ALREADY_RESTORED')

    def test_changed_restored_data_blocks_all_remaining_moves(self):
        self.uninstall();self.interrupt('move')
        current=self.root/'private/context/memory.txt';current.write_text('user later data')
        result,_=self.fixture.public(self.root,'recover-data','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(current.read_text(),'user later data')
        self.assertFalse((self.root/'runtimes/test-component/memory.sqlite').exists())
        self.assertEqual((self.saved/'runtime-data/runtimes/test-component/memory.sqlite').read_bytes(),b'database bytes')

    def test_completion_record_before_descriptor_can_be_finalized_without_repeating_moves(self):
        self.uninstall();self.interrupt('complete')
        self.assertEqual(json.loads(self.journal.read_text())['status'],'DATA_RESTORED')
        _,finished=self.fixture.public(self.root,'recover-data','--apply')
        self.assertEqual(finished['already_moved'],3);self.assert_restored()
        self.assertNotIn('pending_recovery',json.loads((self.saved/'recovery.json').read_text()))

    def test_service_metadata_write_can_resume_and_preserves_exact_layout(self):
        import shutil
        shutil.copytree(fixtures.ROOT/'templates/services',self.root/'templates/services')
        self.code('from harness_core.sidecars import prepare_runtime;prepare_runtime(root,"powercontext")')
        self.fixture.executable(self.fixture.tools/'docker','#!/bin/sh\nif [ "$1" = inspect ] || [ "$2" = inspect ]; then echo "error: no such object: fixture" >&2; exit 1; fi\nexit 0\n')
        self.uninstall();expected={**json.loads((self.saved/'services.json').read_text()),'recipes':{}}
        self.interrupt('service');metadata=self.root/'.harness-install/services.json';before=metadata.read_bytes()
        self.fixture.public(self.root,'recover-data','--apply')
        self.assertEqual(metadata.read_bytes(),before);self.assertEqual(json.loads(before),expected);self.assert_restored()

    def test_missing_pending_journal_is_a_conflict_not_a_new_restore(self):
        self.uninstall();self.interrupt('move');self.journal.unlink()
        result,_=self.fixture.public(self.root,'recover-data','--apply',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'runtimes/test-component/memory.sqlite').exists())
        self.assertEqual((self.saved/'runtime-data/runtimes/test-component/memory.sqlite').read_bytes(),b'database bytes')
