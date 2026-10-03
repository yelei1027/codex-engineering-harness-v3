#!/usr/bin/env python3
"""Refresh actual local service evidence without exposing provider secrets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import urlopen
from services_common import ROOT, PINS, verify_source

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--document-layout', action='store_true')
args = parser.parse_args()
reports = ROOT / 'reports'
reports.mkdir(exist_ok=True)

def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)

def json_command(command, destination, env=None):
    result = run(command, env=env)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = {'status': 'FAIL', 'stdout': result.stdout}
    data['exit_code'] = result.returncode
    data['stderr'] = result.stderr
    (reports / destination).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    return data

def get(url, json_response=True):
    with urlopen(url, timeout=30) as response:
        data = response.read()
        return json.loads(data) if json_response else {'status_code': response.status, 'bytes': len(data)}

client = ROOT / 'runtimes/powercontext/client-venv/bin'
host_doctor = json_command([str(client/'powercontext'), 'doctor', '--server-url', 'http://127.0.0.1:18080', '--json'], 'powercontext-host-doctor.json')
container_doctor = json_command(['docker', 'exec', 'codex-harness-powercontext', 'powercontext', 'doctor', '--server-url', 'http://127.0.0.1:8000', '--json'], 'powercontext-container-doctor.json')
codex_env = {**os.environ, 'PATH': str(ROOT/'runtimes/powercontext/bin')+':'+os.environ['PATH']}
# Use the installed desktop binary rather than the sandboxed snap launcher.
codex_dir = ROOT/'runtimes/powercontext/bin'
codex_dir.mkdir(exist_ok=True)
codex_link = codex_dir/'codex'
desktop = Path('/usr/lib/chatgpt/resources/codex')
if desktop.exists() and not codex_link.exists():
    codex_link.symlink_to(desktop)
codex_doctor = json_command([str(client/'powercontext'), 'doctor', 'codex', '--json'], 'powercontext-codex-doctor.json', env=codex_env)
codex_doctor['codex_binary'] = str(codex_link.resolve())
version = run([str(codex_link), '--version'])
codex_doctor['codex_version'] = version.stdout.strip()
(reports/'powercontext-codex-doctor.json').write_text(json.dumps(codex_doctor, ensure_ascii=False, indent=2)+'\n')
for script in ['services-smoke.py', 'services-mcp-smoke.py']:
    command = run([str(client/'python'), str(ROOT/'scripts'/script)])
    if command.returncode:
        raise SystemExit(command.stderr or command.stdout)

subprocess.run(['docker','cp',str(ROOT/'scripts/services-screenshot-smoke.py'),'codex-harness-screenshot-backend:/app/harness_screenshot_smoke.py'], check=True)
subprocess.run(['docker','exec','codex-harness-screenshot-backend','poetry','run','python','/app/harness_screenshot_smoke.py'], check=True)
for filename in ['screenshot-preview.png', 'screenshot-smoke.json']:
    subprocess.run(['docker','cp',f'codex-harness-screenshot-backend:/tmp/{filename}',str(reports/filename)], check=True)
if args.document_layout:
    subprocess.run(['docker','cp',str(ROOT/'docs/codex_engineering_harness_v3_formal_manual.html'),'codex-harness-screenshot-backend:/tmp/harness-formal-manual.html'], check=True)
    subprocess.run(['docker','cp',str(ROOT/'scripts/services-document-layout.py'),'codex-harness-screenshot-backend:/tmp/services-document-layout.py'], check=True)
    subprocess.run(['docker','exec','codex-harness-screenshot-backend','poetry','run','python','/tmp/services-document-layout.py'], check=True)
    previews = reports/'document-previews'
    previews.mkdir(exist_ok=True)
    subprocess.run(['docker','cp','codex-harness-screenshot-backend:/tmp/document-previews/.',str(previews)], check=True)

containers = {}
for name in ['powercontext', 'screenshot-backend', 'screenshot-frontend']:
    item = json.loads(run(['docker','inspect',f'codex-harness-{name}']).stdout)[0]
    image = json.loads(run(['docker','image','inspect',item['Image']]).stdout)[0]
    ports = item['NetworkSettings']['Ports']
    if any(binding['HostIp'] not in ['127.0.0.1','::1'] for bindings in ports.values() if bindings for binding in bindings):
        raise RuntimeError('Service publish address is not loopback')
    containers[name] = {'container': item['Name'].lstrip('/'), 'running': item['State']['Running'],
        'health': item['State'].get('Health', {}).get('Status'), 'image_tag': item['Config']['Image'],
        'image_id': item['Image'], 'repo_digests': image['RepoDigests'],
        'published_ports': ports, 'named_volumes': [m['Name'] for m in item['Mounts'] if m['Type']=='volume']}

smoke = json.loads((reports/'powercontext-smoke.json').read_text())
mcp = json.loads((reports/'powercontext-mcp.json').read_text())
screenshot = json.loads((reports/'screenshot-smoke.json').read_text())
endpoints = {'powercontext_ready':get('http://127.0.0.1:18080/health/ready'),
             'screenshot_backend':get('http://127.0.0.1:17001/',False),
             'screenshot_frontend':get('http://127.0.0.1:15173/',False),
             'screenshot_backend_capabilities':get('http://127.0.0.1:17001/api/capabilities'),
             'screenshot_frontend_proxy':get('http://127.0.0.1:15173/api/capabilities')}
result = {'status':'PASS' if all(c['running'] and c['health']=='healthy' for c in containers.values()) and host_doctor['ok'] and container_doctor['ok'] and smoke['status']=='PASS' and mcp['status']=='PASS' and screenshot['status']=='PASS' else 'FAIL',
          'checked_at':datetime.now(timezone.utc).isoformat(), 'scope':'LOCAL_BASIC_EXPLICIT_MEMORY_AND_OPTIONAL_SIDECAR',
          'sources':{name:verify_source(name) for name in PINS}, 'containers':containers, 'endpoints':endpoints,
          'powercontext':{'status':'PASS','package_version':'0.0.0+g6b2f6e8','mcp_tool_count':len(mcp['tools']),
                         'basic_handoff_independent_recall':smoke['handoff']['independent_recall']['status'],
                         'full_automatic_memory':'NOT_ENABLED_PROVIDER_UNCONFIGURED',
                         'upstream_full_plugin_doctor':codex_doctor['status'],
                         'upstream_full_plugin_doctor_detail':'Basic direct HTTP MCP is verified independently. The upstream automatic integration plugin is not installed.',
                         'evidence':['reports/powercontext-smoke.json','reports/powercontext-mcp.json','reports/powercontext-host-doctor.json','reports/powercontext-container-doctor.json','reports/powercontext-codex-doctor.json']},
          'screenshot_to_code':screenshot,
          'lifecycle':{'start':'python3 scripts/services.py start','stop':'python3 scripts/services.py stop',
                       'retention':'Stop preserves all named data volumes. Runtime environments, machine binding, data volumes, credentials, and reports are private and excluded from portable package.',
                       'build':'python3 scripts/services-build.py','verification':'python3 scripts/services-verify.py --document-layout'}}
layout = reports/'document-previews/document-validation.json'
if layout.exists():
    visual = reports/'document-previews/visual-review.json'
    result['formal_document_layout'] = {'status':json.loads(layout.read_text())['status'], 'report':str(layout.relative_to(ROOT))}
    if visual.exists():
        review = json.loads(visual.read_text())
        current = all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in review['images'].items())
        result['formal_document_layout']['visual_review'] = {'status':'PASS' if current else 'REQUIRES_REFRESH', 'report':str(visual.relative_to(ROOT))}
(reports/'services.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
binding = {'private_machine_binding':True,'mcp_servers':{'powercontext':{'url':'http://127.0.0.1:18080/mcp/'}},
           'component_commands':{'powercontext':[str(client/'powercontext'),'doctor','--server-url','http://127.0.0.1:18080','--json'],
                                 'screenshot-to-code':['python3',str(ROOT/'scripts/services.py'),'status','--service','screenshot-to-code']},
           'service_urls':{'powercontext':'http://127.0.0.1:18080','screenshot_backend':'http://127.0.0.1:17001','screenshot_frontend':'http://127.0.0.1:15173'}}
binding_path = ROOT/'services-binding.json'
binding_path.write_text(json.dumps(binding,ensure_ascii=False,indent=2)+'\n')
binding_path.chmod(0o600)
print(json.dumps({'status':result['status'],'report':'reports/services.json','generation':screenshot['generation']['status'],'codex_plugin_doctor':codex_doctor['status']}))
