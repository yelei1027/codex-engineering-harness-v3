"""Actual Compose/service/data seams in isolated release/state; no provider generation."""
import json,os,shutil,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'tests'),str(root)]
from test_public_lifecycle import PublicLifecycleCase
from harness_core import sidecars,software
case=PublicLifecycleCase();case.setUp();case.env['PATH']=os.environ['PATH']
original=set(subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines())
release=None;deployment=None;extra=None;record={'scope':'Actual Compose ownership, HTTP readiness, local data retention and recovery; no full dependency bootstrap or model/GUI/provider proof','checks':[]}
try:
 release=case.release()
 shutil.copytree(root/'templates/services',release/'templates/services')
 with patch.dict(os.environ,case.env):
  for group in sidecars.GROUPS:sidecars.prepare_runtime(release,group)
  deployment=sidecars.layout(release)
  originals={'powercontext':'codex-harness-powercontext:6b2f6e8','screenshot-backend':'codex-harness-screenshot-backend:d026163','screenshot-frontend':'codex-harness-screenshot-frontend:d026163'}
  for group in sidecars.GROUPS:sidecars.preflight_build(release,group)
  for name,old in originals.items():
   subprocess.run(['docker','tag',old,deployment['images'][name]],check=True)
   # Seal only once both screenshots are tagged.
   if name in ('powercontext','screenshot-frontend'):sidecars.record_images(release,'powercontext' if name=='powercontext' else 'screenshot-to-code')
  software.capture(release,['runtimes/powercontext','runtimes/screenshot-to-code'])
 case.public(release,'install')
 _,started=case.public(release,'services','start')
 binding=json.loads((release/'services-binding.json').read_text());url=binding['service_urls']['powercontext']
 def get(path):
  with urlopen(url+path,timeout=3) as response:return json.load(response)
 def post(path,value):
  with urlopen(Request(url+path,data=json.dumps(value).encode(),headers={'Content-Type':'application/json'}),timeout=5) as response:return json.load(response)
 deadline=time.monotonic()+30
 while True:
  try:
   if get('/health/ready').get('status')=='ready':break
  except Exception:
   if time.monotonic()>deadline:raise
   time.sleep(.5)
 scope=post('/v1/scopes',{'title':'Owned lifecycle fixture','summary':'isolated retention proof','idempotency_key':'harness-owned-fixture'})
 scoped=scope['scope_id'];record['checks'].append({'name':'real_powercontext_scope_created','scope_id':scoped})
 case.public(release,'prepare',case.repo,'--apply','--profile','L1')
 note=case.repo/'.harness/task/retained-note';note.parent.mkdir(parents=True);note.write_text('retained task bytes')
 extra=deployment['namespace']+'-fixture-reader'
 volume=deployment['data_namespace']+'-'+sidecars.VOLUMES['powercontext'][0]
 subprocess.run(['docker','run','--rm','-d','--name',extra,'--network','none','--mount','type=volume,source='+volume+',target=/hold,readonly','--entrypoint','sleep',originals['powercontext'],'60'],check=True,capture_output=True)
 refused,_=case.public(release,'uninstall','--apply',ok=False);assert refused.returncode!=0
 assert note.read_text()=='retained task bytes';assert get('/health/ready')['status']=='ready'
 record['checks'].append({'name':'unowned_readonly_volume_consumer_blocks_uninstall'})
 subprocess.run(['docker','stop',extra],check=True,capture_output=True);extra=None
 _,removed=case.public(release,'uninstall','--apply')
 assert removed['services']=='STOPPED_AND_REMOVED'
 assert note.read_text()=='retained task bytes'
 assert not (case.state/'accepted.json').exists()
 # Reinstall source checkout, restore data identity/provider config before preparing new recipes.
 case.public(release,'install');case.public(release,'recover-data','--apply')
 with patch.dict(os.environ,case.env):
  for group in sidecars.GROUPS:sidecars.prepare_runtime(release,group)
  for group in sidecars.GROUPS:sidecars.preflight_build(release,group)
  for name,old in originals.items():subprocess.run(['docker','tag',old,deployment['images'][name]],check=True)
  for group in sidecars.GROUPS:sidecars.record_images(release,group)
  software.capture(release,['runtimes/powercontext','runtimes/screenshot-to-code'])
 case.public(release,'services','start')
 deadline=time.monotonic()+30
 while True:
  try:
   if get('/health/ready').get('status')=='ready':break
  except Exception:
   if time.monotonic()>deadline:raise
   time.sleep(.5)
 # The immutable idempotency key resolves the same actual database entity.
 restored=post('/v1/scopes',{'title':'Owned lifecycle fixture','summary':'isolated retention proof','idempotency_key':'harness-owned-fixture'})
 assert restored['scope_id']==scoped
 assert note.read_text()=='retained task bytes'
 record['checks'].append({'name':'scope_survived_uninstall_reinstall','scope_id':restored['scope_id']})
 current=set(subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines());assert original<=current
 record['checks'].append({'name':'preexisting_containers_remain_running','count':len(original)})
 record.update(status='PASS',namespace=deployment['namespace'],ports=deployment['ports'])
except Exception as error:
 record.update(status='FAIL',error=str(error));print('REAL_SERVICE_FAILURE',str(error),flush=True)
finally:
 if extra:subprocess.run(['docker','stop',extra],capture_output=True)
 if deployment and release:
  # Cleanup only test-created exact namespace. Every existing real service is outside it.
  for group in sidecars.GROUPS:
   subprocess.run(['docker','compose','-p',deployment['namespace']+'-'+group,'-f',str(release/'runtimes'/group/'compose.yaml'),'down'],env={**case.env,'HARNESS_SERVICE_NAMESPACE':deployment['namespace'],'HARNESS_VOLUME_NAMESPACE':deployment['data_namespace'],'HARNESS_STATE_ID':deployment['state_id'],**{key:str(deployment['ports'][name]) for name,key in sidecars.PORT_KEYS.items()},**{'HARNESS_'+name.upper().replace('-','_')+'_IMAGE':image for name,image in deployment['images'].items()}},capture_output=True)
  names=subprocess.check_output(['docker','volume','ls','--format','{{.Name}}'],text=True).splitlines()
  for name in names:
   if name.startswith(deployment['data_namespace']+'-'):subprocess.run(['docker','volume','rm',name],capture_output=True)
  for image in deployment['images'].values():subprocess.run(['docker','image','rm',image],capture_output=True)
  record['cleanup']='Only disposable test namespace/volumes/image aliases'
 after=set(subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines())
 if not original<=after:record.update(status='FAIL',error='Preexisting container disappeared during fixture cleanup')
 case.doCleanups()
 if args.output:
  args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(record,ensure_ascii=False,indent=2),flush=True)
 if record['status']!='PASS':sys.exit(1)
