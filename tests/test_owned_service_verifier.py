"""Verifier command provenance: only current owned resources, never legacy defaults."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import test_public_lifecycle as fixtures
from harness_core.common import HarnessError


class OwnedVerifierCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.release();spec=importlib.util.spec_from_file_location('owned_verifier',fixtures.ROOT/'scripts/services-verify.py');self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module);self.module.ROOT=self.root
        self.layout={'namespace':'codex-harness-owned-fixture','ports':{'powercontext':28080,'screenshot-backend':27001,'screenshot-frontend':25173}}
        self.urls={'powercontext':'http://127.0.0.1:28080','screenshot_backend':'http://127.0.0.1:27001','screenshot_frontend':'http://127.0.0.1:25173'}
        (self.root/'services-binding.json').write_text(json.dumps({'service_urls':self.urls}));self.before=(self.root/'services-binding.json').read_bytes();self.calls=[]

    def execute(self,argv,env):
        self.calls.append(argv);self.assertEqual(env['PYTHONDONTWRITEBYTECODE'],'1')
        reports=self.root/'private/service-reports'
        if Path(argv[-1]).name=='services-smoke.py':(reports/'powercontext-smoke.json').write_text(json.dumps({'status':'PASS','handoff':{'independent_recall':{'status':'PASS'}}}))
        if Path(argv[-1]).name=='services-mcp-smoke.py':(reports/'powercontext-mcp.json').write_text('{"status":"PASS","tools":[]}')
        if argv[:2]==['docker','cp'] and Path(argv[-1]).name=='screenshot-smoke.json':Path(argv[-1]).write_text('{"status":"PASS","generation":{"status":"OPTIONAL_PROVIDER_BLOCK"}}')
        return '{"ok":true}'

    def patches(self):
        from contextlib import ExitStack
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.dict('os.environ',self.fixture.env))
        stack.enter_context(patch.object(self.module.sidecars,'layout',return_value=self.layout))
        stack.enter_context(patch.object(self.module.sidecars,'_manage',return_value={}))
        stack.enter_context(patch.object(self.module.sidecars,'inspect_owned',side_effect=lambda root,group:[{'state':'running'}]*(1 if group=='powercontext' else 2)))
        stack.enter_context(patch.object(self.module.sidecars,'inspect_volumes',return_value=None))
        stack.enter_context(patch.object(self.module.sidecars,'verify_image_tags',return_value=None))
        stack.enter_context(patch.object(self.module.sidecars,'unowned_resource_consumers',return_value=[]))
        stack.enter_context(patch.object(self.module,'execute',side_effect=self.execute))
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
        stack.enter_context(patch.object(self.module,'urlopen',return_value=Response()))
        return stack

    def test_command_resources_are_owned_and_binding_runtime_remain_unchanged(self):
        self.patches();result=self.module.verify();self.assertEqual(result['status'],'PASS')
        docker=[a for a in self.calls if a[0]=='docker'];self.assertTrue(docker)
        for argv in docker:self.assertTrue(any(self.layout['namespace'] in a for a in argv))
        self.assertFalse((self.root/'runtimes/powercontext/bin/codex').exists())
        self.assertEqual((self.root/'services-binding.json').read_bytes(),self.before)
        self.assertEqual(result['service_urls'],self.urls)

    def test_unknown_consumer_blocks_before_any_exec_or_copy(self):
        self.patches()
        with patch.object(self.module.sidecars,'unowned_resource_consumers',return_value=[{'container':'unknown'}]):
            with self.assertRaises(HarnessError):self.module.verify()
        self.assertEqual(self.calls,[]);self.assertEqual((self.root/'services-binding.json').read_bytes(),self.before)

    def test_unsafe_outputs_block_before_service_calls_and_preserve_external_data(self):
        import os
        import shutil
        self.patches()
        outside=self.fixture.base/'outside';outside.mkdir()
        victim=outside/'user-data';victim.write_text('USER ORIGINAL')
        private=self.root/'private';private.mkdir(exist_ok=True)
        reports=private/'service-reports'
        for relative in ['service-reports/powercontext-smoke.json',
                         'service-reports/powercontext-mcp.json',
                         'service-reports/screenshot-preview.png',
                         'service-reports/document-previews/formal-1440-full.png',
                         'service-smoke/recall-fixture.json',
                         'service-smoke', 'service-reports/document-previews']:
            with self.subTest(relative=relative):
                for directory in ['service-reports','service-smoke']:
                    path=private/directory
                    if path.is_symlink():path.unlink()
                    elif path.exists():shutil.rmtree(path)
                target=private/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.symlink_to(outside if relative in ['service-smoke','service-reports/document-previews'] else victim)
                with self.assertRaises(HarnessError):self.module.verify(document_layout=True)
                self.assertEqual(self.calls,[]);self.assertEqual(victim.read_text(),'USER ORIGINAL')
                self.assertEqual(sorted(p.name for p in outside.iterdir()),['user-data'])
        # A FIFO must also fail without opening it or starting a service.
        for directory in ['service-reports','service-smoke']:
            path=private/directory
            if path.is_symlink():path.unlink()
            elif path.exists():shutil.rmtree(path)
        reports.mkdir();os.mkfifo(reports/'powercontext-smoke.json')
        with self.assertRaises(HarnessError):self.module.verify()
        self.assertEqual(self.calls,[])

    def test_hardlinked_outputs_preserve_external_inode_before_calls(self):
        import os
        self.patches()
        victim=self.fixture.base/'user-data';victim.write_text('USER ORIGINAL')
        for relative in ['service-reports/powercontext-smoke.json',
                         'service-reports/screenshot-preview.png',
                         'service-reports/document-previews/formal-1440-full.png',
                         'service-smoke/recall-fixture.json']:
            with self.subTest(relative=relative):
                target=self.root/'private'/relative;target.parent.mkdir(parents=True,exist_ok=True)
                os.link(victim,target)
                with self.assertRaises(HarnessError):self.module.verify(document_layout=True)
                self.assertEqual(self.calls,[]);self.assertEqual(victim.read_text(),'USER ORIGINAL')
                target.unlink()

    def test_standalone_smokes_reject_leaf_before_network_or_client_import(self):
        import shutil
        import subprocess
        import sys
        for script,leaf in [('services-smoke.py','powercontext-smoke.json'),
                            ('services-mcp-smoke.py','powercontext-mcp.json')]:
            with self.subTest(script=script):
                shutil.copyfile(fixtures.ROOT/'scripts'/script,self.root/'scripts'/script)
                reports=self.root/'private/service-reports';reports.mkdir(parents=True,exist_ok=True)
                victim=self.fixture.base/leaf;victim.write_text('USER ORIGINAL')
                (reports/leaf).symlink_to(victim)
                result=subprocess.run([sys.executable,str(self.root/'scripts'/script)],env={**self.fixture.env,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Refuse unsafe service output',result.stderr)
                self.assertEqual(victim.read_text(),'USER ORIGINAL')
                (reports/leaf).unlink()
