"""Actual two-volume memory recovery; minimal control fixtures, no GUI/full bootstrap."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
from test_public_lifecycle import PublicLifecycleCase
from harness_core import cli,installations,memory,memory_services,sidecars,software
from harness_core.common import distribution_path,sha,write_json
from harness_core.transaction import Transaction

case=PublicLifecycleCase();case.setUp();case.env['PATH']=os.environ['PATH'];case.env['PYTHONDONTWRITEBYTECODE']='1'
original=set(subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines())
original_volumes=set(subprocess.check_output(['docker','volume','ls','--format','{{.Name}}'],text=True).splitlines())
layouts=[];aliases=[];bindings=[];references=[];docs=[]
record={'scope':'Actual owned Compose, two real PowerContext DB volumes, Scope/exact Revision recovery via current public CLI; minimal source/registration fixtures, no GUI/model/full-bootstrap/candidate-promotion proof','checks':[]}


def seal_source(root):
    files={str(p.relative_to(root)):{'sha256':sha(p.read_bytes())} for p in root.rglob('*') if p.is_file() and distribution_path(p.relative_to(root))}
    write_json(root/'.harness-install/receipt.json',{'schema_version':1,'target':str(root),'state':str(case.state),'origin':'DISPOSABLE_NATIVE_MEMORY_FIXTURE','files':files})


def prepare(root):
    shutil.copytree(ROOT/'templates/services',root/'templates/services')
    with patch.dict(os.environ,case.env):
        sidecars.prepare_runtime(root,'powercontext');layout=sidecars.layout(root);layouts.append((root,layout))
        sidecars.preflight_build(root,'powercontext')
        alias=layout['images']['powercontext'];subprocess.run(['docker','tag','codex-harness-powercontext:6b2f6e8',alias],check=True);aliases.append(alias)
        sidecars.record_images(root,'powercontext');software.capture(root,['runtimes/powercontext']);seal_source(root)
        layout=sidecars.layout(root);layouts[-1]=(root,layout)
    return layout


def ready(url):
    deadline=time.monotonic()+30
    while True:
        try:
            with urlopen(url+'/health/ready',timeout=2) as response:
                if json.load(response).get('status')=='ready':return
        except Exception:
            if time.monotonic()>deadline:raise
        time.sleep(.3)


try:
    old=[case.release('old1'),case.release('old2')];repos=[case.repo,case.base/'project2'];repos[1].mkdir()
    subprocess.run(['git','init','-q',str(repos[1])],check=True)
    for index,root in enumerate(old):
        layout=prepare(root)
        if index==0:case.public(root,'install')
        else:
            with patch.dict(os.environ,case.env),Transaction('Register second isolated service source') as tx:
                installations.register_preparing(tx,{'root':str(root),'version':'3.1.1','release_hash':cli.release_hash(root)})
        case.public(root,'services','start','--service','powercontext')
        ready('http://127.0.0.1:'+str(layout['ports']['powercontext']))
        case.public(root,'prepare',repos[index],'--apply','--profile','L1')
        facts={'objective':'Retain real memory for fixture '+str(index),'next_action':'Recover and verify the original exact revision',
               'completed':['Real service ready'],'remaining':['Uninstall and recover'],'blockers':[],'decisions':['Keep original Scope and revision'],
               'spec_refs':[],'tests':[],'review':{},'workers':[]}
        with patch.dict(os.environ,case.env),patch.object(cli,'ROOT',root):
            committed=memory.handoff(repos[index],{'facts':facts,'task_id':'retained','resume_node':'A00','git':{'head':'fixture-reference'}})
        bound=repos[index]/'.harness/state/memory.json';ref=repos[index]/'.harness/task/retained/powercontext-handoff.json'
        bindings.append((bound,bound.read_bytes()));references.append((ref,ref.read_bytes()))
        record['checks'].append({'name':'real_scope_and_revision_created','scope_id':committed['scope_id'],'revision':committed['revision']})
    case.public(old[0],'uninstall','--apply')
    assert all(not (r/'bin/harness').exists() for r in old)
    record['checks'].append({'name':'both_old_program_roots_removed'})
    newest=case.release('newest','3.2.0');prepare(newest);case.public(newest,'install')
    case.public(newest,'services','start','--service','powercontext')
    with patch.dict(os.environ,case.env):url=sidecars.layout(newest)['ports']['powercontext']
    ready('http://127.0.0.1:'+str(url))
    write_json(newest/'machine-binding.json',{'codex_binary':'/bin/echo','mcp_servers':{'powercontext':{'url':'http://127.0.0.1:'+str(url)+'/mcp/'}}})
    _,preview=case.public(newest,'recover-memory');assert len(preview['services'])==2
    case.public(newest,'recover-memory','--apply')
    with patch.dict(os.environ,case.env):docs=memory_services.deployments()
    for index,repo in enumerate(repos):
        ready(json.loads(bindings[index][1])['url'])
        case.public(newest,'prepare',repo,'--apply','--profile','L1')
        config=tomllib.loads((repo/'.codex/config.toml').read_text())
        assert config['mcp_servers']['powercontext']['url']==json.loads(bindings[index][1])['url']+'/mcp/'
        _,recalled=case.public(newest,'context','recall',repo,'--task','retained')
        original_ref=json.loads(references[index][1]);assert recalled['reference']==original_ref
        assert bindings[index][0].read_bytes()==bindings[index][1] and references[index][0].read_bytes()==references[index][1]
        record['checks'].append({'name':'exact_original_scope_revision_recalled','scope_id':recalled['reference']['scope_id'],'revision':recalled['reference']['revision'],'binding_unchanged':True})
    catalogue=json.loads((case.state/'installations.json').read_text())
    assert [r for r,v in catalogue['roots'].items() if v['status']=='INSTALLED']==[str(newest)]
    _,status=case.public(newest,'services','status');assert len(status['retained_memory_services'])==2
    case.public(newest,'services','stop','--service','powercontext')
    _,status=case.public(newest,'services','status');assert all(s['state']=='exited' for s in status['retained_memory_services'])
    case.public(newest,'recover-memory','--apply')
    for index,repo in enumerate(repos):
        ready(json.loads(bindings[index][1])['url'])
        _,recalled=case.public(newest,'context','recall',repo,'--task','retained');assert recalled['reference']==json.loads(references[index][1])
    record['checks'].append({'name':'real_stopped_services_status_restart_and_exact_revision_recall'})
    case.public(newest,'uninstall','--apply')
    with patch.dict(os.environ,case.env):
        for doc in docs:
            assert not (memory_services.directory(doc['origin_root'])/'compose.yaml').exists()
            assert (memory_services.directory(doc['origin_root'])/'.env').is_file()
            raw=sidecars.docker(['volume','inspect',doc['data_namespace']+'-powercontext-data']);assert json.loads(raw)
    record['checks'].append({'name':'second_full_uninstall_stops_aux_services_removes_programs_keeps_env_and_volumes'})
    record.update(status='PASS',source_hashes={name:sha((ROOT/'harness_core'/name).read_bytes()) for name in ['memory_services.py','memory.py','cli.py','lifecycle.py','sidecars.py']})
except Exception as error:
    record.update(status='FAIL',error=str(error));print('NATIVE_RETAINED_MEMORY_FAILURE',str(error),flush=True)
finally:
    # Exact test names only; verify Compose project before removing a container.
    try:
        with patch.dict(os.environ,case.env):docs=memory_services.deployments()
    except Exception:pass
    expected={layout['namespace']+'-powercontext':layout['namespace']+'-powercontext' for root,layout in layouts}
    expected.update({doc['namespace']+'-powercontext':doc['namespace']+'-powercontext' for doc in docs})
    for name,project in expected.items():
        result=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',name],capture_output=True,text=True)
        if result.returncode==0:
            if result.stdout.strip()!=project:record.update(status='FAIL',error='Unknown container occupies a disposable test name; preserved');continue
            subprocess.run(['docker','stop',name],capture_output=True);subprocess.run(['docker','rm',name],capture_output=True)
        network=project+'_default'
        result=subprocess.run(['docker','network','inspect','--format','{{index .Labels "com.docker.compose.project"}}',network],capture_output=True,text=True)
        if result.returncode==0 and result.stdout.strip()==project:subprocess.run(['docker','network','rm',network],capture_output=True)
    for root,layout in layouts:
        volume=layout['data_namespace']+'-powercontext-data'
        if volume not in original_volumes:subprocess.run(['docker','volume','rm',volume],capture_output=True)
    for alias in aliases:subprocess.run(['docker','image','rm',alias],capture_output=True)
    after=set(subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines())
    if not original<=after:record.update(status='FAIL',error='Preexisting container disappeared')
    record['preexisting_containers_preserved']=len(original);record['cleanup']='Exact disposable namespaces, volumes and image aliases only'
    case.doCleanups()
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(record,ensure_ascii=False,indent=2),flush=True)
    if record['status']!='PASS':sys.exit(1)
