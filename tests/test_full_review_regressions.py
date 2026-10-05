"""Public maintenance regressions from the complete immutable-source review."""
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import test_public_lifecycle as fixtures
from harness_core import rescue,sidecars,software


class FullReviewRegressionCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.release();self.fixture.env['PYTHONDONTWRITEBYTECODE']='1'

    def test_refresh_journal_failure_restores_original_profile_and_overlay(self):
        self.fixture.public(self.root,'install');self.fixture.public(self.root,'prepare',self.fixture.repo,'--apply','--profile','L1')
        project=self.fixture.repo/'.harness/state/project.json';before=project.read_bytes()
        config=self.fixture.repo/'.codex/config.toml';config_before=config.read_bytes()
        unknown=self.fixture.state/'refreshes';unknown.write_text('unknown user file')
        result,_=self.fixture.public(self.root,'refresh',self.fixture.repo,'--profile','L2',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(project.read_bytes(),before);self.assertEqual(config.read_bytes(),config_before)
        self.assertFalse((self.fixture.repo/'.codex/hooks.json').exists());self.assertEqual(unknown.read_text(),'unknown user file')

    def test_recovery_cleanup_preserves_unknown_fifo(self):
        with patch.dict(os.environ,self.fixture.env):
            record=rescue.prepare(self.root,'fifo-fixture');directory=Path(record['directory']);path=directory/'user-pipe';os.mkfifo(path)
            self.assertEqual(rescue.cleanup(record),'PRESERVED_CHANGED_RECOVERY_RUNTIME');self.assertTrue(path.exists());self.assertTrue(directory.is_dir())

    def test_recovery_cleanup_preserves_unknown_empty_directory(self):
        with patch.dict(os.environ,self.fixture.env):
            record=rescue.prepare(self.root,'directory-fixture');directory=Path(record['directory']);path=directory/'user-empty';path.mkdir()
            self.assertEqual(rescue.cleanup(record),'PRESERVED_CHANGED_RECOVERY_RUNTIME');self.assertTrue(path.is_dir())

    def test_official_basic_smoke_then_public_uninstall_remains_usable(self):
        case=self
        class Server(BaseHTTPRequestHandler):
            marker=''
            def log_message(self,*args):pass
            def answer(self,data):
                raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):self.answer({'status':'ready'})
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path=='/v1/scopes':self.answer({'scope_id':'fixture-scope'})
                elif self.path=='/v1/work/handoffs/prepare-current':Server.marker=body['source_id'];self.answer({'handoff':body['handoff']})
                elif self.path=='/v1/handoff/commit':self.answer({'reference':{'family':'handoff','artifact_id':'handoff','revision':1}})
                elif self.path=='/v1/handoff/continue':self.answer({'status':'resolved','marker':Server.marker})
                else:self.send_error(404)
        server=ThreadingHTTPServer(('127.0.0.1',0),Server);self.addCleanup(server.server_close);self.addCleanup(server.shutdown);threading.Thread(target=server.serve_forever,daemon=True).start()
        shutil.copytree(fixtures.ROOT/'templates/services',self.root/'templates/services')
        for name in ['services-smoke.py','services-recall.py']:shutil.copyfile(fixtures.ROOT/'scripts'/name,self.root/'scripts'/name)
        self.fixture.executable(self.fixture.tools/'docker','#!/bin/sh\nif [ "$1" = inspect ] || [ "$2" = inspect ]; then echo "error: no such object: fixture" >&2; exit 1; fi\nexit 0\n')
        with patch.dict(os.environ,self.fixture.env):sidecars.prepare_runtime(self.root,'powercontext');software.capture(self.root,['runtimes/powercontext'])
        (self.root/'services-binding.json').write_text(json.dumps({'service_urls':{'powercontext':'http://127.0.0.1:'+str(server.server_port)}}))
        self.fixture.public(self.root,'install')
        run=subprocess.run([sys.executable,str(self.root/'scripts/services-smoke.py')],env=self.fixture.env,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr);self.assertEqual(json.loads(run.stdout)['status'],'PASS')
        self.assertTrue((self.root/'private/service-smoke/recall-fixture.json').is_file());self.assertFalse((self.root/'runtimes/powercontext/recall-fixture.json').exists())
        _,preview=self.fixture.public(self.root,'uninstall');self.assertEqual(preview['status'],'UNINSTALL_PLANNED')
        _,removed=self.fixture.public(self.root,'uninstall','--apply');self.assertEqual(removed['status'],'UNINSTALLED')
        retained=Path(removed['preserved_data']['local_install_data'])
        self.assertTrue((retained/'private/service-reports/powercontext-smoke.json').is_file())
        self.assertFalse((self.root/'reports').exists())
        self.fixture.public(self.root,'install');self.fixture.public(self.root,'recover-data','--apply')
        self.assertTrue((self.root/'private/service-reports/powercontext-smoke.json').is_file())
