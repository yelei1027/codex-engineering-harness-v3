"""Public multi-namespace recovery using Docker identity fixtures and real local HTTP."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import test_public_lifecycle as fixtures
from harness_core import cli,installations,memory_services,sidecars,software
from harness_core.common import write_json
from harness_core.transaction import Transaction

DOCKER='''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['MEMORY_DOCKER_FIXTURE']);d=json.loads(p.read_text());a=sys.argv[1:]
d['calls'].append(a)
def finish(value=None,code=0):
 p.write_text(json.dumps(d))
 if value is not None:print(value if isinstance(value,str) else json.dumps(value))
 if code:print('error: no such object: fixture',file=sys.stderr)
 sys.exit(code)
if a[:2]==['image','inspect']:
 if a[2] not in d['images']:finish(code=1)
 finish([{'Id':d['images'][a[2]]}])
if a[:2]==['image','rm']:d['images'].pop(a[2],None);finish('removed')
if a[:2]==['volume','inspect']:
 if a[2] not in d['volumes']:finish(code=1)
 finish([{'Labels':d['volumes'][a[2]]}])
if a[:2]==['ps','-aq']:finish(' '.join(d['containers']))
if a[:2]==['compose','version']:finish('Docker Compose fixture')
if a[:2]==['inspect','--format']:
 lines=[]
 for name in a[3:]:
  c=d['containers'][name];v=[json.dumps('/'+name)]
  if '.Config.Image' in a[2]:v.append(json.dumps(c['Config']['Image']))
  v.append(json.dumps(c['Mounts']));lines.append(' '.join(v))
 finish('\\n'.join(lines))
if a[0]=='inspect':
 if a[1] not in d['containers']:finish(code=1)
 finish([d['containers'][a[1]]])
if a[0]=='compose':
 project=a[a.index('-p')+1];recipe=a[a.index('-f')+1];action=a[a.index('-f')+2:]
 ns=os.environ['HARNESS_SERVICE_NAMESPACE'];name=ns+'-powercontext'
 if action[0]=='up':
  fail=os.environ.get('MEMORY_FAIL_ONCE')
  if fail and not Path(fail).exists():Path(fail).write_text('failed');finish(code=9)
  tag=os.environ['HARNESS_POWERCONTEXT_IMAGE'];identifier=d['images'].get(tag,tag)
  ports={'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':os.environ['POWERCONTEXT_PORT']}]}
  d['containers'][name]={'Name':'/'+name,'Image':identifier,'Config':{'Image':tag,'Labels':{'com.docker.compose.project':project,'com.docker.compose.project.config_files':recipe}},'Mounts':[{'Name':os.environ['HARNESS_VOLUME_NAMESPACE']+'-powercontext-data','Destination':'/data'}],'HostConfig':{'PortBindings':ports},'NetworkSettings':{'Ports':ports},'State':{'Status':'running'}}
 elif action[0]=='down':d['containers'].pop(name,None)
 elif action[0]=='stop':
  if name in d['containers']:d['containers'][name]['State']['Status']='exited';d['containers'][name]['NetworkSettings']['Ports']=None
 finish('fixture compose')
finish('')
'''


class RetainedMemoryServicesCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.env['PYTHONDONTWRITEBYTECODE']='1'
        self.docker=self.fixture.base/'docker.json';self.fixture.env['MEMORY_DOCKER_FIXTURE']=str(self.docker)
        self.fixture.executable(self.fixture.tools/'docker',DOCKER)
        self.docker.write_text(json.dumps({'images':{},'volumes':{},'containers':{},'calls':[]}))
        self.old=[self.fixture.release('old1'),self.fixture.release('old2')]
        self.new=self.fixture.release('new','3.2.0');self.repos=[self.fixture.repo,self.fixture.base/'project2']
        self.repos[1].mkdir();subprocess.run(['git','init','-q',str(self.repos[1])],check=True)
        self.originals=[]
        for index,root in enumerate(self.old):
            self.prepare_service(root)
            if index==0:self.fixture.public(root,'install')
            else:
                with patch.dict(os.environ,self.fixture.env),Transaction('Fixture prepared registration') as tx:
                    installations.register_preparing(tx,{'root':str(root),'version':'3.1.1','release_hash':cli.release_hash(root)})
            self.fixture.public(root,'prepare',self.repos[index],'--apply','--profile','L1')
            with patch.dict(os.environ,self.fixture.env):layout=sidecars.layout(root)
            bound=self.repos[index]/'.harness/state/memory.json'
            bound.write_text(json.dumps({'repo':str(self.repos[index]),'service_root':str(root),'data_namespace':layout['data_namespace'],
                                         'url':'http://127.0.0.1:'+str(layout['ports']['powercontext']),'scope_id':'scope-'+str(index)}))
            ref=self.repos[index]/'.harness/task/retained/powercontext-handoff.json';ref.parent.mkdir(parents=True)
            ref.write_text(json.dumps({'repo':str(self.repos[index]),'url':json.loads(bound.read_text())['url'],'scope_id':'scope-'+str(index),
                                      'revision':{'family':'handoff','artifact_id':'handoff','revision':index+1}}))
            self.originals.append((bound,bound.read_bytes(),ref,ref.read_bytes()))
        self.fixture.public(self.old[0],'uninstall','--apply')
        self.prepare_service(self.new);self.fixture.public(self.new,'install')
        with patch.dict(os.environ,self.fixture.env):layout=sidecars.layout(self.new)
        write_json(self.new/'machine-binding.json',{'codex_binary':'/bin/echo','mcp_servers':{'powercontext':{'url':'http://127.0.0.1:'+str(layout['ports']['powercontext'])+'/mcp/'}}})
        self.calls=[]

    def data(self):return json.loads(self.docker.read_text())
    def edit_data(self,callback):
        value=self.data();callback(value);self.docker.write_text(json.dumps(value))

    def prepare_service(self,root):
        shutil.copytree(fixtures.ROOT/'templates/services',root/'templates/services')
        with patch.dict(os.environ,self.fixture.env):
            sidecars.prepare_runtime(root,'powercontext');value=sidecars.layout(root)
            identifier='sha256:'+('d'*64)
            self.edit_data(lambda d:(d['images'].update({value['images']['powercontext']:identifier}),
                                     d['volumes'].update({value['data_namespace']+'-powercontext-data':{'com.harness.state_id':value['state_id'],'com.harness.data_namespace':value['data_namespace']}})))
            sidecars.record_images(root,'powercontext');software.capture(root,['runtimes/powercontext'])

    def http(self,index):
        bound=json.loads(self.originals[index][1]);port=int(bound['url'].rsplit(':',1)[1]);case=self
        class Server(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def answer(self,value):
                data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self):
                case.calls.append(('GET',index,self.path))
                self.answer({'status':'ready'} if self.path=='/health/ready' else {'scope_id':bound['scope_id']})
            def do_POST(self):
                case.calls.append(('POST',index,self.path));value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path!='/v1/handoff/continue':self.send_error(400);return
                self.answer({'status':'resolved','scope_id':value['scope_id'],'selected_revision':value['revision']})
        server=ThreadingHTTPServer(('127.0.0.1',port),Server)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()

    def test_public_recovery_restores_two_namespaces_using_only_newest_root(self):
        _,preview=self.fixture.public(self.new,'recover-memory')
        self.assertEqual(len(preview['services']),2);self.assertFalse((self.fixture.state/'memory-services').exists())
        _,result=self.fixture.public(self.new,'recover-memory','--apply')
        self.assertEqual(result['status'],'MEMORY_SERVICES_STARTED')
        catalogue=json.loads((self.fixture.state/'installations.json').read_text())
        self.assertEqual([name for name,r in catalogue['roots'].items() if r['status']=='INSTALLED'],[str(self.new)])
        for index,repo in enumerate(self.repos):
            self.http(index);self.fixture.public(self.new,'prepare',repo,'--apply','--profile','L1')
            config=tomllib.loads((repo/'.codex/config.toml').read_text())
            self.assertEqual(config['mcp_servers']['powercontext']['url'],json.loads(self.originals[index][1])['url']+'/mcp/')
            _,recalled=self.fixture.public(self.new,'context','recall',repo,'--task','retained')
            self.assertEqual(recalled['reference']['scope_id'],'scope-'+str(index))
            self.assertEqual(recalled['reference']['revision']['revision'],index+1)
        for bound,before,ref,ref_before in self.originals:self.assertEqual(bound.read_bytes(),before);self.assertEqual(ref.read_bytes(),ref_before)
        self.assertFalse(any(c[2]=='/v1/scopes' for c in self.calls))

    def test_conflict_in_last_namespace_prevents_all_recovery_writes(self):
        with patch.dict(os.environ,self.fixture.env):namespace=recovery_source_namespace(self.old[1])
        self.edit_data(lambda d:d['volumes'][namespace+'-powercontext-data'].update({'com.harness.state_id':'unknown'}))
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.fixture.state/'memory-services').exists())
        self.assertFalse(any(a[0]=='compose' and 'up' in a for a in self.data()['calls']))

    def test_missing_volume_cannot_create_empty_replacement(self):
        with patch.dict(os.environ,self.fixture.env):namespace=recovery_source_namespace(self.old[0])
        self.edit_data(lambda d:d['volumes'].pop(namespace+'-powercontext-data'))
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.fixture.state/'memory-services').exists())
        self.assertNotIn(namespace+'-powercontext-data',self.data()['volumes'])

    def test_interrupted_start_retries_owned_deployment_without_losing_other_namespace(self):
        self.fixture.env['MEMORY_FAIL_ONCE']=str(self.fixture.base/'failed-once')
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False);self.assertNotEqual(result.returncode,0)
        _,retried=self.fixture.public(self.new,'recover-memory','--apply');self.assertEqual(len(retried['services']),2)
        self.assertEqual(len(self.data()['containers']),2)

    def test_changed_recovery_recipe_blocks_full_uninstall_before_mutation(self):
        self.fixture.public(self.new,'recover-memory','--apply')
        with patch.dict(os.environ,self.fixture.env):base=memory_services.directory(self.old[1])
        recipe=base/'compose.yaml';recipe.write_text(recipe.read_text()+'\n# later user change\n')
        before=(self.fixture.state/'accepted.json').read_bytes()
        result,_=self.fixture.public(self.new,'uninstall','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual((self.fixture.state/'accepted.json').read_bytes(),before)
        self.assertEqual(len(self.data()['containers']),2);self.assertIn('later user change',recipe.read_text())

    def test_full_uninstall_removes_recovered_programs_but_preserves_original_volumes_and_env(self):
        self.fixture.public(self.new,'recover-memory','--apply');volumes=set(self.data()['volumes'])
        _,preview=self.fixture.public(self.new,'uninstall')
        self.assertEqual(len(preview['preserved_data']['retained_memory_services']),2)
        for service in preview['retained_memory_services']:
            self.assertTrue(Path(service['provider_environment']).is_file());self.assertTrue(Path(service['program']).is_file())
            self.assertIn(service['memory_volume'],volumes)
        _,removed=self.fixture.public(self.new,'uninstall','--apply')
        self.assertTrue(all(s['state']=='REMOVED' for s in removed['preserved_data']['retained_memory_services']))
        self.assertEqual(self.data()['containers'],{});self.assertEqual(set(self.data()['volumes']),volumes)
        with patch.dict(os.environ,self.fixture.env):
            for origin in self.old:
                base=memory_services.directory(origin)
                self.assertFalse((base/'compose.yaml').exists());self.assertTrue((base/'.env').is_file())
                self.assertEqual(memory_services.load(origin)['status'],'REMOVED')

    def test_unknown_volume_consumer_blocks_every_namespace_before_writes(self):
        with patch.dict(os.environ,self.fixture.env):namespace=recovery_source_namespace(self.old[1])
        self.edit_data(lambda d:d['containers'].update({'unknown':{'Config':{'Image':'unrelated'},'Mounts':[{'Name':namespace+'-powercontext-data'}]}}))
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.fixture.state/'memory-services').exists())
        self.assertEqual(set(self.data()['containers']),{'unknown'})

    def test_interrupted_start_does_not_contact_reused_original_port(self):
        self.fixture.env['MEMORY_FAIL_ONCE']=str(self.fixture.base/'failed-once')
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False);self.assertNotEqual(result.returncode,0)
        with patch.dict(os.environ,self.fixture.env):doc=memory_services.deployments()[0]
        with socket.socket() as occupant:
            occupant.bind(('127.0.0.1',doc['port']));occupant.listen()
            result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False)
            self.assertNotEqual(result.returncode,0);self.assertEqual(self.data()['containers'],{})

    def test_different_runtime_image_requires_compatibility_review_without_mounting_database(self):
        with patch.dict(os.environ,self.fixture.env):
            value=sidecars.layout(self.new);value['image_ids']['powercontext']='sha256:'+('e'*64)
            write_json(sidecars.layout_path(self.new),value)
        self.edit_data(lambda d:d['images'].update({value['images']['powercontext']:value['image_ids']['powercontext']}))
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.fixture.state/'memory-services').exists())
        self.assertEqual(self.data()['containers'],{})

    def test_complete_install_entry_automatically_recovers_retained_memory_namespaces(self):
        with patch.dict(os.environ,self.fixture.env):
            for area in ['node','markitdown','screenshot-to-code']:
                path=self.new/'runtimes'/area;path.mkdir(exist_ok=True);(path/'fixture').write_text('prepared fixture')
            software.capture(self.new,['runtimes/node','runtimes/markitdown','runtimes/screenshot-to-code'])
        _,installed=self.fixture.public(self.new,'install','--prepare')
        self.assertEqual(installed['retained_memory']['status'],'MEMORY_SERVICES_STARTED')
        self.assertEqual(len(installed['retained_memory']['services']),2);self.assertEqual(len(self.data()['containers']),2)

    def test_closed_service_time_wait_does_not_block_original_port_recovery(self):
        with patch.dict(os.environ,self.fixture.env):port=memory_services.source(self.old[0])[2]['ports']['powercontext']
        listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',port));listener.listen(1)
        client=socket.socket();client.connect(('127.0.0.1',port));accepted,_=listener.accept()
        accepted.close();client.recv(1);client.close();listener.close()
        with socket.socket() as naive:
            with self.assertRaises(OSError):naive.bind(('127.0.0.1',port))
        _,preview=self.fixture.public(self.new,'recover-memory')
        self.assertEqual(len(preview['services']),2);self.assertFalse((self.fixture.state/'memory-services').exists())

    def test_new_retained_generation_cannot_overwrite_existing_memory_mapping(self):
        self.fixture.public(self.new,'recover-memory','--apply')
        from harness_core import recovery
        from harness_core.common import new_id
        with patch.dict(os.environ,self.fixture.env):
            retained=recovery.locate(self.old[0]);generation=retained/'generations/replacement';generation.mkdir(parents=True)
            identifier=new_id('uninstall');descriptor=json.loads((retained/'recovery.json').read_text())
            descriptor['uninstall_id']=identifier;write_json(generation/'recovery.json',descriptor)
            shutil.copyfile(retained/'services.json',generation/'services.json')
            original=json.loads((self.fixture.state/'uninstalls'/(json.loads((retained/'recovery.json').read_text())['uninstall_id']+'.json')).read_text())
            original['id']=identifier;original['roots'][str(self.old[0])]['retained']=str(generation)
            write_json(self.fixture.state/'uninstalls'/(identifier+'.json'),original)
            with Transaction('Fixture later retained generation registration') as tx:
                installations.remove(tx,[str(self.old[0])],{str(self.old[0]):str(generation)})
            base=memory_services.directory(self.old[0]);before=(base/'deployment.json').read_bytes()
        result,_=self.fixture.public(self.new,'recover-memory','--apply',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertEqual((base/'deployment.json').read_bytes(),before)
        self.assertEqual(len(self.data()['containers']),2)

    def test_stopped_recovered_services_can_be_inspected_restarted_and_uninstalled(self):
        self.fixture.public(self.new,'recover-memory','--apply')
        self.fixture.public(self.new,'services','stop')
        _,status=self.fixture.public(self.new,'services','status')
        self.assertTrue(all(s['state']=='exited' for s in status['retained_memory_services']))
        self.assertTrue(all(c['NetworkSettings']['Ports'] is None for c in self.data()['containers'].values()))
        self.fixture.public(self.new,'recover-memory','--apply')
        self.assertTrue(all(c['State']['Status']=='running' for c in self.data()['containers'].values()))
        self.fixture.public(self.new,'services','stop')
        self.fixture.public(self.new,'uninstall','--apply');self.assertEqual(self.data()['containers'],{})

    def test_unrecovered_original_memory_disables_raw_mcp_without_creating_replacement_scope(self):
        _,prepared=self.fixture.public(self.new,'prepare',self.repos[0],'--apply','--profile','L1')
        config=tomllib.loads((self.repos[0]/'.codex/config.toml').read_text())
        self.assertFalse(config['mcp_servers']['powercontext']['enabled'])
        self.assertEqual(prepared['memory']['status'],'UNSYNCED');self.assertEqual(self.calls,[])
        for bound,before,ref,ref_before in self.originals:self.assertEqual(bound.read_bytes(),before);self.assertEqual(ref.read_bytes(),ref_before)

    def test_restart_last_port_conflict_prevents_primary_and_all_auxiliary_starts(self):
        self.fixture.public(self.new,'recover-memory','--apply');self.fixture.public(self.new,'services','stop')
        with patch.dict(os.environ,self.fixture.env):doc=memory_services.deployments()[-1]
        before=sum(a[0]=='compose' and 'up' in a for a in self.data()['calls'])
        with socket.socket() as occupant:
            occupant.bind(('127.0.0.1',doc['port']));occupant.listen()
            result,_=self.fixture.public(self.new,'services','start','--service','powercontext',ok=False)
            self.assertNotEqual(result.returncode,0)
        self.assertEqual(sum(a[0]=='compose' and 'up' in a for a in self.data()['calls']),before)
        self.assertTrue(all(c['State']['Status']=='exited' for c in self.data()['containers'].values()))


def recovery_source_namespace(origin):return memory_services.source(origin)[2]['data_namespace']
