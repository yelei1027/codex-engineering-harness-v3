"""Public install retry over stateful Docker/dependency fault fixtures, not live providers."""
import hashlib
import json
import shutil
import subprocess
import sys
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import test_public_lifecycle as fixtures


DOCKER = r'''#!/usr/bin/env python3
import hashlib,json,os,sys
from pathlib import Path
p=Path(os.environ['PARTIAL_DOCKER_STATE']);d=json.loads(p.read_text());a=sys.argv[1:];d['calls'].append(a)
def done(value='',code=0):
 p.write_text(json.dumps(d));print(value if isinstance(value,str) else json.dumps(value))
 if code:print('error: no such object: fixture' if code==1 else 'controlled service startup failure',file=sys.stderr)
 raise SystemExit(code)
if a[:2]==['compose','version']:done('Docker Compose fixture')
if a[0]=='info':done('fixture daemon')
if a[0]=='build':
 tag=a[a.index('-t')+1];d['images'][tag]='sha256:'+hashlib.sha256(tag.encode()).hexdigest();done('fixture build')
if a[:2]==['image','inspect']:
 if a[2] not in d['images']:done(code=1)
 done([{'Id':d['images'][a[2]]}])
if a[:2]==['volume','inspect']:
 if a[2] not in d['volumes']:done(code=1)
 done([{'Labels':d['volumes'][a[2]]['labels']}])
if a[:2]==['ps','-aq']:done(' '.join(d['containers']))
if a[:2]==['inspect','--format']:
 done('\n'.join(' '.join(json.dumps(v) for v in ['/'+n,d['containers'][n]['Config']['Image'],d['containers'][n]['Mounts']]) for n in a[3:]))
if a[0]=='inspect':
 if a[1] not in d['containers']:done(code=1)
 done([d['containers'][a[1]]])
if a[0]=='compose':
 project=a[a.index('-p')+1];recipe=a[a.index('-f')+1];action=a[a.index('-f')+2:]
 group='powercontext' if project.endswith('-powercontext') else 'screenshot-to-code'
 components=['powercontext'] if group=='powercontext' else ['screenshot-backend','screenshot-frontend']
 ns=os.environ['HARNESS_SERVICE_NAMESPACE'];vn=os.environ['HARNESS_VOLUME_NAMESPACE']
 keys={'powercontext':'POWERCONTEXT_PORT','screenshot-backend':'SCREENSHOT_BACKEND_PORT','screenshot-frontend':'SCREENSHOT_FRONTEND_PORT'}
 tags={n:os.environ['HARNESS_'+n.upper().replace('-','_')+'_IMAGE'] for n in components}
 specs={n:{'ports':[{'host_ip':'127.0.0.1','published':int(os.environ[keys[n]]),'target':8000}], 'volumes':[{'type':'volume','source':suffix,'target':'/'+suffix} for suffix in (['powercontext-data'] if n=='powercontext' else ['screenshot-logs','screenshot-assets'] if n=='screenshot-backend' else [])]} for n in components}
 if action[0]=='config':done({'services':specs,'volumes':{m['source']:{'name':vn+'-'+m['source']} for spec in specs.values() for m in spec['volumes']}})
 if action[0]=='up':
  if group=='screenshot-to-code' and not d['failed_once']:d['failed_once']=True;done(code=9)
  for n in components:
   suffixes=['powercontext-data'] if n=='powercontext' else ['screenshot-logs','screenshot-assets'] if n=='screenshot-backend' else []
   mounts=[]
   for suffix in suffixes:
    name=vn+'-'+suffix;d['volumes'].setdefault(name,{'labels':{'com.harness.state_id':os.environ['HARNESS_STATE_ID'],'com.harness.data_namespace':vn},'data':'retained marker'});mounts.append({'Type':'volume','Name':name,'Destination':'/'+suffix,'RW':True})
   name=ns+'-'+n;d['containers'][name]={'Image':d['images'][tags[n]],'Config':{'Image':tags[n],'Labels':{'com.docker.compose.project':project,'com.docker.compose.project.config_files':recipe}},'Mounts':mounts,'HostConfig':{'PortBindings':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':os.environ[keys[n]]}]}},'NetworkSettings':{'Ports':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':os.environ[keys[n]]}]}},'State':{'Status':'running','Running':True}}
 elif action[0]=='stop':
  for n in components:
   if ns+'-'+n in d['containers']:d['containers'][ns+'-'+n]['State'].update({'Status':'exited','Running':False})
 elif action[0]=='down':
  for n in components:d['containers'].pop(ns+'-'+n,None)
 done('fixture compose')
done('')
'''


class PartialServiceRetryCase(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicLifecycleCase();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.release();self.store=self.fixture.base/'docker.json'
        self.store.write_text(json.dumps({'images':{},'volumes':{},'containers':{},'calls':[],'failed_once':False}))
        self.fixture.env['PARTIAL_DOCKER_STATE']=str(self.store)
        self.fixture.env['XDG_CONFIG_HOME']=str(self.fixture.base/'config')
        (self.root/'harness.lock').write_text('{}\n')
        self.fixture.executable(self.fixture.tools/'node', r'''#!/usr/bin/env python3
import json,sys
if sys.argv[1:]==['--version']:print('v24.0.0')
else:
 request=json.load(sys.stdin)
 assert request['operation']=='discovery'
 print(json.dumps({'initialize':{'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}},'tools/list':{'tools':[]}}))
''')
        self.fixture.executable(self.fixture.tools/'systemctl', r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
if 'is-active' in sys.argv:print('active')
if 'show' in sys.argv:
 unit=sys.argv[sys.argv.index('show')+1]
 print('ActiveState=active\nSubState=running\nMainPID=0\nMemoryCurrent=0\nFragmentPath='+str(Path(os.environ['XDG_CONFIG_HOME'])/'systemd/user'/unit))
''')

        self.fixture.executable(self.fixture.tools/'docker',DOCKER)
        self.fixture.executable(self.fixture.tools/'npm','#!/bin/sh\nif [ "$1" != --version ]; then mkdir -p node_modules; echo owned > node_modules/fixture; fi\n')
        self.fixture.executable(self.fixture.tools/'uv',r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
a=sys.argv[1:]
if a[0]=='venv':p=Path(a[-1])/'bin/python';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture python');p.chmod(0o755)
if a[0]=='sync':p=Path(os.environ['UV_PROJECT_ENVIRONMENT'])/'bin/powercontext';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture client');p.chmod(0o755)
''')
        for name in ['bootstrap.py','services-build.py','services.py','services_common.py']:
            shutil.copyfile(fixtures.ROOT/'scripts'/name,self.root/'scripts'/name)
        shutil.copytree(fixtures.ROOT/'templates/services',self.root/'templates/services')
        shutil.copytree(fixtures.ROOT/'manifests/node',self.root/'manifests/node')
        trees=json.loads((self.root/'manifests/source-trees.json').read_text())
        for name,pin in [('powercontext','6b2f6e8a4c1aac59fca75662aca26d78748bfa29'),('screenshot-to-code','d026163f586dfa8c5c10d28c36edd59a9d3b0e88')]:
            source=self.root/'vendor'/name;source.mkdir();(source/'fixture.txt').write_text('accepted fault-fixture source')
            trees[name]={'commit':pin,'files':{'fixture.txt':{'kind':'file','sha256':hashlib.sha256((source/'fixture.txt').read_bytes()).hexdigest()}}}
        frontend=self.root/'vendor/screenshot-to-code/frontend'
        frontend.mkdir();(frontend/'fixture.txt').write_text('accepted frontend source')
        trees['screenshot-to-code']['files']['frontend/fixture.txt']={'kind':'file','sha256':hashlib.sha256((frontend/'fixture.txt').read_bytes()).hexdigest()}
        backend=self.root/'vendor/screenshot-to-code/backend'
        backend.mkdir();(backend/'fixture.txt').write_text('accepted backend source')
        trees['screenshot-to-code']['files']['backend/fixture.txt']={'kind':'file','sha256':hashlib.sha256((backend/'fixture.txt').read_bytes()).hexdigest()}
        (self.root/'manifests/source-trees.json').write_text(json.dumps(trees))

    def data(self):return json.loads(self.store.read_text())

    def failed_start(self):
        result,reply=self.fixture.public(self.root,'install','--prepare',ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('服务启动',reply['error'])
        d=self.data();self.assertEqual(len(d['containers']),1)
        self.assertFalse((self.fixture.state/'accepted.json').exists())
        # Real loopback readiness fixture; Docker/systemd/SDK remain declared fakes.
        port=json.loads((self.root/'.harness-install/services.json').read_text())['ports']['powercontext']
        class Ready(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                payload=b'{"status":"ready"}'
                self.send_response(200);self.send_header('Content-Length',str(len(payload)))
                self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',port),Ready)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return d

    def test_public_retry_resumes_partial_start_without_rebuilding_or_losing_data(self):
        before=self.failed_start();logs={p.name:p.read_bytes() for p in (self.root/'private/install-logs').glob('*')}
        _,result=self.fixture.public(self.root,'install','--prepare')
        self.assertEqual(result['status'],'INSTALLED')
        after=self.data();self.assertEqual(len(after['containers']),3)
        self.assertEqual(sum(a[0]=='build' for a in after['calls']),3)
        self.assertEqual(after['images'],before['images'])
        for name,value in before['volumes'].items():self.assertEqual(after['volumes'][name],value)
        self.assertFalse(any(a[0]=='compose' and 'down' in a for a in after['calls']))
        self.assertTrue(all((self.root/'private/install-logs'/n).read_bytes()==v for n,v in logs.items()))
        self.assertIn('powercontext',json.loads((self.root/'machine-binding.json').read_text())['mcp_servers'])

    def test_public_retry_also_resumes_stopped_partial_container(self):
        before=self.failed_start();self.fixture.public(self.root,'services','stop')
        self.assertEqual(next(iter(self.data()['containers'].values()))['State']['Status'],'exited')
        self.fixture.public(self.root,'install','--prepare')
        self.assertTrue(all(v['State']['Status']=='exited' for v in self.data()['containers'].values()))
        self.assertTrue((self.root/'.harness-install/resources.json').is_file())
        self.assertEqual(self.data()['images'],before['images'])

    def test_changed_build_or_foreign_consumer_blocks_before_any_start_or_rebuild(self):
        for change in ['recipe','image','source','runtime','foreign','mount','port','missing_group_port','data_namespace']:
            with self.subTest(change=change):
                case=PartialServiceRetryCase();case.setUp();self.addCleanup(case.doCleanups);case.failed_start();d=case.data()
                if change=='recipe':
                    with (case.root/'runtimes/powercontext/compose.yaml').open('a') as f:f.write('\n# later user change\n')
                elif change=='source':(case.root/'vendor/powercontext/fixture.txt').write_text('changed source')
                elif change=='runtime':(case.root/'runtimes/powercontext/client-venv/bin/powercontext').write_text('changed runtime')
                elif change=='image':d['images'][next(iter(d['images']))]='sha256:later-image'
                elif change in ('missing_group_port','data_namespace'):
                    from harness_core import sidecars
                    layout_path=sidecars.layout_path(case.root);layout=json.loads(layout_path.read_text())
                    if change=='missing_group_port':layout['ports']['screenshot-backend']+=1
                    else:layout['data_namespace']='codex-harness-'+('0'*16 if layout['data_namespace']!='codex-harness-'+('0'*16) else '1'*16)
                    layout_path.write_text(json.dumps(layout))
                elif change=='mount':next(iter(d['containers'].values()))['Mounts'][0]['Destination']='/wrong'
                elif change=='port':next(iter(d['containers'].values()))['HostConfig']['PortBindings']['8000/tcp'][0]['HostIp']='0.0.0.0'
                else:
                    tag=next(iter(d['images']));d['containers']['foreign']={'Config':{'Image':tag,'Labels':{}},'Image':d['images'][tag],'Mounts':[],'State':{'Status':'exited'}}
                case.store.write_text(json.dumps(d));before=case.data();offset=len(before['calls'])
                result,_=case.fixture.public(case.root,'install','--prepare',ok=False)
                case.assertNotEqual(result.returncode,0)
                after=case.data();case.assertEqual(after['containers'],before['containers']);case.assertEqual(after['volumes'],before['volumes'])
                case.assertFalse(any(a[0]=='build' or (a[0]=='compose' and any(x in a for x in ['up','down'])) for a in after['calls'][offset:]))
                case.assertFalse((case.fixture.state/'accepted.json').exists())

    def test_backend_restart_preserves_public_gateway_addresses(self):
        self.failed_start();self.fixture.public(self.root,'install','--prepare')
        binding=self.root/'services-binding.json';before=binding.read_bytes()
        result=subprocess.run([sys.executable,str(self.root/'scripts/services.py'),'start','--service','powercontext'],env=self.fixture.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(binding.read_bytes(),before)
        saved=json.loads(before);gateway=json.loads((self.root/'.harness-install/resources.json').read_text())
        self.assertEqual(saved['mcp_servers']['powercontext']['url'],f"http://127.0.0.1:{gateway['ports']['powercontext']}/mcp/")
