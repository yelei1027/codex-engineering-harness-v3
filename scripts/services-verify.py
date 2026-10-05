#!/usr/bin/env python3
"""Verify this owned deployment; keep fixtures and evidence outside runtime software."""
import sys
sys.dont_write_bytecode=True
import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness_core import sidecars
from harness_core.common import HarnessError,lock,read_json,write_json
from harness_core.service_reports import prepare_outputs


def execute(argv,env):
    result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=90)
    if result.returncode:raise HarnessError('验证命令失败；受管服务和数据已保留。检查该组件后重试：'+Path(argv[0]).name)
    return result.stdout


def json_command(argv,env):
    try:return json.loads(execute(argv,env))
    except ValueError:raise HarnessError('验证结果不是有效JSON；未改服务接入配置')


def verify(document_layout=False):
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    reports, _ = prepare_outputs(ROOT)
    with lock(ROOT/'.harness-install/services.lock'):
        layout=sidecars.layout(ROOT)
        if not layout:raise HarnessError('当前安装尚未准备服务；先完成组件准备')
        sidecars._manage(ROOT,'status')
        if sidecars.unowned_resource_consumers(ROOT):raise HarnessError('有未知消费者引用本安装资源；保留并先核对归属')
        for group in sidecars.GROUPS:
            owned=sidecars.inspect_owned(ROOT,group);sidecars.inspect_volumes(ROOT,group)
            if len(owned)!=len(sidecars.GROUPS[group]) or any(c['state']!='running' for c in owned):
                raise HarnessError('受管服务未全部运行；用 harness services start 后重试')
        sidecars.verify_image_tags(layout)
        binding=read_json(ROOT/'services-binding.json',{})
        expected={'powercontext':f'http://127.0.0.1:{layout["ports"]["powercontext"]}',
                  'screenshot_backend':f'http://127.0.0.1:{layout["ports"]["screenshot-backend"]}',
                  'screenshot_frontend':f'http://127.0.0.1:{layout["ports"]["screenshot-frontend"]}'}
        if binding.get('service_urls')!=expected:raise HarnessError('服务接入与当前受管部署不一致；保留原配置并核对')
        client=ROOT/'runtimes/powercontext/client-venv/bin'
        pc=layout['namespace']+'-powercontext';backend=layout['namespace']+'-screenshot-backend'
        host=json_command([str(client/'powercontext'),'doctor','--server-url',expected['powercontext'],'--json'],env)
        container=json_command(['docker','exec',pc,'powercontext','doctor','--server-url','http://127.0.0.1:8000','--json'],env)
        write_json(reports/'powercontext-host-doctor.json',host);write_json(reports/'powercontext-container-doctor.json',container)
        for name in ['services-smoke.py','services-mcp-smoke.py']:
            execute([str(client/'python'),str(ROOT/'scripts'/name)],env)
        remote='/tmp/harness-owned-screenshot-smoke.py'
        execute(['docker','cp',str(ROOT/'scripts/services-screenshot-smoke.py'),backend+':'+remote],env)
        runner='import sys,runpy;sys.path.insert(0,"/app");runpy.run_path("'+remote+'",run_name="__main__")'
        execute(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',backend,'poetry','run','python','-c',runner],env)
        for name in ['screenshot-preview.png','screenshot-smoke.json']:
            execute(['docker','cp',backend+':/tmp/'+name,str(reports/name)],env)
        if document_layout:
            execute(['docker','cp',str(ROOT/'docs/codex_engineering_harness_v3_formal_manual.html'),backend+':/tmp/harness-formal-manual.html'],env)
            execute(['docker','cp',str(ROOT/'scripts/services-document-layout.py'),backend+':/tmp/services-document-layout.py'],env)
            execute(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',backend,'poetry','run','python','/tmp/services-document-layout.py'],env)
            previews=reports/'document-previews';previews.mkdir(exist_ok=True)
            execute(['docker','cp',backend+':/tmp/document-previews/.',str(previews)],env)
        smoke=read_json(reports/'powercontext-smoke.json',{});mcp=read_json(reports/'powercontext-mcp.json',{});screenshot=read_json(reports/'screenshot-smoke.json',{})
        endpoints={}
        for name,url in expected.items():
            with urlopen(url+('/health/ready' if name=='powercontext' else '/'),timeout=15) as response:
                endpoints[name]={'status_code':response.status}
        result={'status':'PASS' if host.get('ok') and container.get('ok') and all(d.get('status')=='PASS' for d in [smoke,mcp,screenshot]) else 'FAIL',
                'scope':'OWNED_LOCAL_BASIC_AND_RENDERER; no GUI/human approval/Full Provider acceptance',
                'namespace':layout['namespace'],'service_urls':expected,'endpoints':endpoints,
                'powercontext':{'scope_id':smoke.get('scope_id'),'basic_independent_recall':smoke.get('handoff',{}).get('independent_recall',{}).get('status'),'mcp_tool_count':len(mcp.get('tools',[])),'full_automatic_memory':'NOT_TESTED'},
                'screenshot_to_code':screenshot,'hook_trust':'NOT_INSPECTED','binding_changes':'NONE'}
        if document_layout:
            document=read_json(reports/'document-previews/document-validation.json',{})
            result['formal_document_layout']={'status':document.get('status','UNVERIFIED')}
            if document.get('status')!='PASS':result['status']='FAIL'
        write_json(reports/'services.json',result)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--document-layout',action='store_true');args=parser.parse_args()
    try:
        result=verify(args.document_layout);print(json.dumps({'status':result['status'],'report':'private/service-reports/services.json','generation':result['screenshot_to_code'].get('generation',{}).get('status')},ensure_ascii=False));sys.exit(0 if result['status']=='PASS' else 1)
    except (HarnessError,OSError,ValueError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'status':'ERROR','error':str(error)},ensure_ascii=False));sys.exit(2)
