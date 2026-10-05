"""Memory plane remains separate from software-root migration, with real HTTP fixtures."""
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
from harness_core import cli,memory,sidecars
from harness_core.common import canonical,sha


class MemoryServicePinCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.env['PYTHONDONTWRITEBYTECODE']='1'
        self.old=self.fixture.release('old');self.new=self.fixture.release('new','3.2.0')
        self.calls=[]
        case=self
        class Server(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def answer(self,value):
                data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self):
                case.calls.append((self.server.label,'GET',self.path))
                if self.path=='/health/ready':self.answer({'status':'ready'})
                elif self.path=='/v1/scopes/old-scope':self.answer({'scope_id':'old-scope'})
                else:self.send_error(404)
            def do_POST(self):
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])));case.calls.append((self.server.label,'POST',self.path))
                if self.path=='/v1/handoff/continue':self.answer({'status':'resolved','scope_id':value['scope_id'],'selected_revision':value['revision'],'marker':'old retained handoff'})
                else:self.send_error(400)
        self.servers=[]
        for name,root in [('old',self.old),('new',self.new)]:
            server=ThreadingHTTPServer(('127.0.0.1',0),Server);server.label=name;threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown);self.servers.append(server)
            shutil.copytree(fixtures.ROOT/'templates/services',root/'templates/services')
            with patch.dict(os.environ,self.fixture.env):
                sidecars.prepare_runtime(root,'powercontext')
                owned=sidecars.layout(root);owned['ports']['powercontext']=server.server_port
                from harness_core.common import write_json
                write_json(sidecars.layout_path(root),owned)
            (root/'services-binding.json').write_text(json.dumps({'service_urls':{'powercontext':f'http://127.0.0.1:{server.server_port}'}}))
        self.fixture.public(self.old,'install');self.fixture.public(self.old,'prepare',self.fixture.repo,'--apply','--profile','L1')
        self.url=f'http://127.0.0.1:{self.servers[0].server_port}'
        with patch.dict(os.environ,self.fixture.env):namespace=sidecars.layout(self.old)['data_namespace']
        self.bound=self.fixture.repo/'.harness/state/memory.json';self.bound.write_text(json.dumps({'repo':str(self.fixture.repo),'url':self.url,'scope_id':'old-scope','service_root':str(self.old),'data_namespace':namespace}))
        ref=self.fixture.repo/'.harness/task/retained/powercontext-handoff.json';ref.parent.mkdir(parents=True);ref.write_text(json.dumps({'repo':str(self.fixture.repo),'url':self.url,'scope_id':'old-scope','revision':{'family':'handoff','artifact_id':'handoff','revision':1}}))
        # Fixture-only accepted registration; no claim of real candidate acceptance.
        from harness_core.installations import register
        from harness_core.transaction import Transaction
        with patch.dict(os.environ,self.fixture.env),patch.object(cli,'ROOT',self.new):
            wanted={'root':str(self.new),'version':'3.2.0','release_hash':cli.release_hash(self.new)}
            with Transaction('Fixture accepted registration') as tx:
                register(tx,wanted,{'root':str(self.old),'version':'3.1.1','release_hash':cli.release_hash(self.old)})
                tx.change(self.fixture.state/'accepted.json',canonical(wanted)+b'\n')
        self.calls.clear()

    def test_public_migration_and_recall_pin_original_service_and_exact_revision(self):
        before=self.bound.read_bytes();_,plan=self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old)
        self.assertEqual(plan['memory']['service_root'],str(self.old));self.assertEqual(self.calls,[])
        _,result=self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old,'--apply')
        self.assertEqual(result['memory']['status'],'MEMORY_PINNED_TO_ORIGINAL_SERVICE');self.assertEqual(self.bound.read_bytes(),before)
        _,recalled=self.fixture.public(self.new,'context','recall',self.fixture.repo,'--task','retained')
        self.assertEqual(recalled['status'],'RECALLED');self.assertEqual(recalled['reference']['scope_id'],'old-scope')
        self.assertEqual({c[0] for c in self.calls},{'old'});self.assertFalse(any(c[2]=='/v1/scopes' for c in self.calls))
        self.fixture.public(self.new,'rollback',result['transaction_id']);self.assertEqual(self.bound.read_bytes(),before)

    def test_changed_namespace_refuses_without_rebinding_or_creating_new_scope(self):
        self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old,'--apply')
        value=json.loads(self.bound.read_text());value['data_namespace']='codex-harness-'+('f'*16);self.bound.write_text(json.dumps(value));before=self.bound.read_bytes()
        result,_=self.fixture.public(self.new,'context','recall',self.fixture.repo,'--task','retained',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.bound.read_bytes(),before);self.assertEqual(self.calls,[])

    def test_unknown_original_service_root_refuses_before_network_use(self):
        self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old,'--apply')
        value=json.loads(self.bound.read_text());value['service_root']=str(self.fixture.base/'unregistered');self.bound.write_text(json.dumps(value));before=self.bound.read_bytes()
        result,_=self.fixture.public(self.new,'context','recall',self.fixture.repo,'--task','retained',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.bound.read_bytes(),before);self.assertEqual(self.calls,[])

    def test_legacy_binding_infers_one_verified_root_without_mutating_binding(self):
        value=json.loads(self.bound.read_text());value.pop('service_root');value.pop('data_namespace');self.bound.write_text(json.dumps(value));before=self.bound.read_bytes()
        self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old,'--apply')
        _,result=self.fixture.public(self.new,'context','recall',self.fixture.repo,'--task','retained')
        self.assertEqual(result['status'],'RECALLED');self.assertEqual(self.bound.read_bytes(),before)

    def test_equal_endpoint_keeps_saved_original_service_provenance(self):
        (self.new/'services-binding.json').write_text(json.dumps({'service_urls':{'powercontext':self.url}}))
        before=self.bound.read_bytes()
        _,plan=self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old)
        self.assertEqual(plan['memory']['service_root'],str(self.old));self.assertEqual(self.calls,[])
        self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old,'--apply')
        _,result=self.fixture.public(self.new,'context','recall',self.fixture.repo,'--task','retained')
        self.assertEqual(result['status'],'RECALLED');self.assertEqual(self.bound.read_bytes(),before)
        self.assertEqual({call[0] for call in self.calls},{'old'})

    def test_equal_endpoint_does_not_hide_unknown_saved_service_root(self):
        (self.new/'services-binding.json').write_text(json.dumps({'service_urls':{'powercontext':self.url}}))
        self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old,'--apply')
        value=json.loads(self.bound.read_text());value['service_root']=str(self.fixture.base/'unregistered')
        self.bound.write_text(json.dumps(value));before=self.bound.read_bytes()
        result,_=self.fixture.public(self.new,'context','recall',self.fixture.repo,'--task','retained',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.bound.read_bytes(),before);self.assertEqual(self.calls,[])

    def test_equal_endpoint_legacy_migration_uses_verified_source_hint(self):
        (self.new/'services-binding.json').write_text(json.dumps({'service_urls':{'powercontext':self.url}}))
        value=json.loads(self.bound.read_text());value.pop('service_root');value.pop('data_namespace')
        self.bound.write_text(json.dumps(value));before=self.bound.read_bytes()
        _,plan=self.fixture.public(self.new,'migrate',self.fixture.repo,'--from',self.old)
        self.assertEqual(plan['memory']['service_root'],str(self.old));self.assertEqual(self.calls,[])
        self.assertEqual(self.bound.read_bytes(),before)
