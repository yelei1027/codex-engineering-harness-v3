"""Public lifecycle behavior with an isolated manager identity response fixture."""
import json
import os
import sys
import unittest
from pathlib import Path
import test_public_lifecycle as fixtures
from harness_core.common import sha
from harness_core.software import process_identity


class ManagedMCPCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.release()
        (self.root/'machine-binding.json').write_text(json.dumps({'codex_binary':'/bin/echo','mcp_servers':{'echo':{'command':'/bin/echo','args':['fixture'],'env':{'COMPONENT_OPTION':'preserved'},'env_vars':['COMPONENT_SESSION']}}}))
        self.fixture.public(self.root,'install');self.fixture.public(self.root,'prepare',self.fixture.repo,'--apply','--profile','L1')
        self.unit='harness-mcp-'+sha(str(self.root).encode())[:16]+'-aabbccddeeff.scope'
        self.receipt=self.fixture.state/'mcp-processes/fixture.json';self.receipt.parent.mkdir(parents=True)
        self.marker=self.fixture.base/'stop-called'
        self.expected={'Id':self.unit,'ControlGroup':'/fixture/'+self.unit,'InvocationID':'original-invocation','Description':'Harness MCP fixture','Transient':'yes'}

    def manager(self,values):
        text='\n'.join(key+'='+value for key,value in values.items())
        self.fixture.executable(self.fixture.tools/'systemctl','#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\nif "stop" in sys.argv:Path('+repr(str(self.marker))+').touch()\nelse:print('+repr(text)+')\n')

    def record(self,**extra):
        doc={'id':'fixture','root':str(self.root),'repo':str(self.fixture.repo),'server':'echo','unit':self.unit,'scope_identity':self.expected,'status':'RUNNING'}
        doc.update(extra);self.receipt.write_text(json.dumps(doc))

    def test_public_prepare_registers_scoped_stdio_with_native_prompt(self):
        import tomllib
        server=tomllib.loads((self.fixture.repo/'.codex/config.toml').read_text())['mcp_servers']['echo']
        self.assertEqual(server['command'],sys.executable)
        self.assertEqual(server['args'],[str(self.root/'bin/harness'),'mcp-run',str(self.fixture.repo),'--server','echo'])
        self.assertEqual(server['env']['COMPONENT_OPTION'],'preserved')
        self.assertEqual(server['env']['HARNESS_STATE_DIR'],str(self.fixture.state))
        self.assertEqual(server['env_vars'],['COMPONENT_SESSION','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'])
        self.assertEqual(server['default_tools_approval_mode'],'prompt')

    def test_uninstall_refuses_different_manager_invocation_before_detaching(self):
        self.record();self.manager({**self.expected,'LoadState':'loaded','ActiveState':'active','InvocationID':'later-user-invocation'})
        before=(self.fixture.state/'accepted.json').read_bytes();project=(self.fixture.repo/'.harness/state/project.json').read_bytes()
        result,_=self.fixture.public(self.root,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse(self.marker.exists())
        self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),before)
        self.assertEqual((self.fixture.repo/'.harness/state/project.json').read_bytes(),project)

    def test_public_mcp_start_shares_reader_lock_and_is_blocked_by_mutator(self):
        import subprocess
        from unittest.mock import patch
        from harness_core.common import lock
        marker=self.fixture.base/'scope-start-attempted'
        self.fixture.executable(self.fixture.tools/'systemd-run','#!/usr/bin/env python3\nfrom pathlib import Path\nPath('+repr(str(marker))+').touch()\nraise SystemExit(9)\n')
        command=[sys.executable,str(self.root/'bin/harness'),'mcp-run',str(self.fixture.repo),'--server','echo']
        with patch.dict(os.environ,self.fixture.env):
            for name in ['preparation.lock','services.lock']:
                with self.subTest(lock=name):
                    with lock(self.root/'.harness-install'/name,shared=True):
                        result=subprocess.run(command,env=self.fixture.env,capture_output=True,text=True)
                    self.assertTrue(marker.exists(),result.stderr)
                    self.assertNotEqual(result.returncode,0) # Deliberately unavailable fixture scope manager.
                    marker.unlink()
                    with lock(self.root/'.harness-install'/name):
                        result=subprocess.run(command,env=self.fixture.env,capture_output=True,text=True)
                    self.assertFalse(marker.exists());self.assertIn('Another Harness operation',result.stderr)

    def test_pending_live_launcher_blocks_uninstall_without_creating_new_journal(self):
        self.record(status='STARTING',scope_identity=None,launcher_pid=os.getpid(),launcher_identity=process_identity(os.getpid()))
        self.manager({'LoadState':'not-found'})
        result,_=self.fixture.public(self.root,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse(self.marker.exists())
        self.assertFalse((self.fixture.state/'uninstalls').exists())
        self.assertTrue((self.fixture.repo/'.harness/state/project.json').is_file())

    def test_process_status_is_read_only_and_ignores_another_root(self):
        self.record(root=str(self.fixture.base/'another-root'))
        before=self.receipt.read_bytes();_,result=self.fixture.public(self.root,'processes','status')
        self.assertEqual(result['active_scopes'],[]);self.assertEqual(self.receipt.read_bytes(),before)

    def test_failed_mcp_start_has_no_controller_json_on_protocol_stdout(self):
        import subprocess
        self.fixture.public(self.root,'disable',self.fixture.repo)
        result=subprocess.run([sys.executable,str(self.root/'bin/harness'),'mcp-run',str(self.fixture.repo),'--server','echo'],env=self.fixture.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,'');self.assertTrue(result.stderr.strip())

    def test_cancelled_start_during_manager_lookup_does_not_execute_component(self):
        import subprocess
        from harness_core.common import canonical
        marker=self.fixture.base/'component-executed'
        server={'command':sys.executable,'args':['-c','from pathlib import Path;Path('+repr(str(marker))+').touch()']}
        (self.root/'machine-binding.json').write_text(json.dumps({'mcp_servers':{'echo':server}}))
        self.record(status='STARTING',scope_identity=None,launcher_pid=os.getpid(),launcher_identity=process_identity(os.getpid()),description='Harness MCP fixture',binding_hash=sha(canonical(server)))
        values={**self.expected,'ActiveState':'active','LoadState':'loaded'}
        script='import sys;sys.path.insert(0,'+repr(str(self.root))+');from pathlib import Path;from harness_core import processes;from harness_core.common import read_json,write_json,HarnessError;path=Path('+repr(str(self.receipt))+');\ndef manager(unit):\n doc=read_json(path);doc["status"]="STARTUP_FAILED";write_json(path,doc);return '+repr(values)+'\nprocesses.properties=manager\ntry:processes.worker(path)\nexcept HarnessError as error:print(str(error),file=sys.stderr);sys.exit(2)'
        result=subprocess.run([sys.executable,'-c',script],env=self.fixture.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertFalse(marker.exists());self.assertEqual(result.stdout,'')
        self.assertIn('no longer active',result.stderr)
