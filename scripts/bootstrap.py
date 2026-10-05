#!/usr/bin/env python3
"""Restore pinned local dependency environments; never registers global MCP/hooks."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness_core import software, recovery, processes, installations, cli, preflight, preparation
from harness_core.common import state_dir,read_json,HarnessError,lock
from harness_core.transaction import Transaction



def run(argv, cwd=None, env=None):
    subprocess.run(argv, cwd=cwd or ROOT, env=env or os.environ, check=True)

def runtime_stage(area,argv,cwd=None,env=None):
    with software.preserve_data(ROOT,area) as package:
        package(argv,cwd,env)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--services', action='store_true', help='also build/start locked loopback Docker sidecars')
    a = p.parse_args()
    preflight.require(a.services)
    with lock(ROOT/'.harness-install/preparation.lock'):
        prepare(a)

def prepare(a):
    preflight.require(a.services)
    if installations.load()['roots'].get(str(ROOT),{}).get('status')!='INSTALLED':
        with lock(state_dir()/'operation.lock'):
            with Transaction('Record direct component preparation') as tx:
                installations.register_preparing(tx,{'root':str(ROOT),'version':(ROOT/'VERSION').read_text().strip(),'release_hash':cli.release_hash(ROOT)})
    processes.require_host()
    processes.require_idle(ROOT)
    software.recover_preparation(ROOT)
    plan=recovery.restore(ROOT)
    if plan['status']=='RECOVERY_PLANNED':recovery.restore(ROOT,True,preparation_locked=True)
    projects=read_json(state_dir()/'projects.json',{})
    if any(read_json(Path(name)/'.harness/state/project.json',{}).get('harness_root')==str(ROOT) for name in projects):
        raise HarnessError('Enabled consumers use this runtime; prepare an isolated candidate instead')
    software.require_prepared_or_empty(ROOT,'runtimes/node')
    software.require_prepared_or_empty(ROOT,'runtimes/markitdown')
    runtime = ROOT / 'runtimes/node'
    runtime.mkdir(parents=True, exist_ok=True)
    import shutil
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(ROOT / 'manifests/node' / name, runtime / name)
    preparation.record(ROOT,'NODE')
    runtime_stage('runtimes/node',['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', str(ROOT/'private/npm-cache')], cwd=runtime)
    uv_env = {**os.environ, 'UV_CACHE_DIR': str(ROOT/'private/uv-cache')}
    py = ROOT/'runtimes/markitdown/bin/python'
    preparation.record(ROOT,'PYTHON_ENV')
    runtime_stage('runtimes/markitdown',['uv', 'venv', '--allow-existing', '--python', '3.12', str(py.parents[1])], env=uv_env)
    preparation.record(ROOT,'PYTHON_PACKAGES')
    runtime_stage('runtimes/markitdown',['uv', 'pip', 'install', '--python', str(py), '-r', str(ROOT/'manifests/markitdown-requirements.lock')], env=uv_env)
    preparation.record(ROOT,'MARKITDOWN_SOURCE')
    runtime_stage('runtimes/markitdown',['uv', 'pip', 'install', '--python', str(py), '--no-deps',
         str(ROOT/'vendor/markitdown/packages/markitdown'), str(ROOT/'vendor/markitdown/packages/markitdown-mcp')], env=uv_env)
    preparation.record(ROOT,'RUNTIME_BINDING')
    binary = os.environ.get('HARNESS_CODEX_BINARY', '/usr/lib/chatgpt/resources/codex')
    if not Path(binary).is_file():
        raise SystemExit('Set HARNESS_CODEX_BINARY to your installed Codex binary.')
    node = ROOT/'runtimes/node/node_modules'
    launch_mode = os.environ.get('HARNESS_CODEX_LAUNCH_MODE')
    if launch_mode is None:
        launch_mode = 'desktop' if Path(binary).resolve() == Path('/usr/lib/chatgpt/resources/codex') else 'cli'
    if launch_mode not in {'desktop','cli'}:
        raise SystemExit('HARNESS_CODEX_LAUNCH_MODE must be desktop or cli.')
    version_text = subprocess.check_output([binary, '--version'], text=True).strip()
    codex_version = version_text.rsplit(' ', 1)[-1]
    binding = {'codex_binary': binary, 'codex_version': codex_version, 'codex_launch_mode': launch_mode, 'mcp_servers': {
        'context-mode': {'command': shutil.which('node'), 'args': [str(node/'context-mode/server.bundle.mjs')],
                         'env': {'CONTEXT_MODE_DIR': str(ROOT/'private/context-mode')}},
        'codegraph': {'command': str(node/'.bin/codegraph'), 'args': ['serve', '--mcp'], 'env': {'CODEGRAPH_TELEMETRY': '0'}},
        'markitdown': {'command': str(py.parent/'markitdown-mcp'), 'args': []}},
        'component_commands': {'codex': [binary, '--version'], 'codegraph': [str(node/'.bin/codegraph'), '--version'],
                               'ocr': [str(node/'.bin/ocr'), '--version'],
                               'context-mode': [shutil.which('node'), str(node/'context-mode/cli.bundle.mjs'), '--help'],
                               'markitdown': [str(py.parent/'markitdown-mcp'), '--help']}}
    (ROOT/'private/context-mode').mkdir(parents=True, exist_ok=True)
    (ROOT/'machine-binding.json').write_text(json.dumps(binding, indent=2)+'\n')
    if a.services:
        preparation.record(ROOT,'SIDECARS')
        run([sys.executable, str(ROOT/'scripts/services-build.py')])
        preparation.record(ROOT,'SERVICES_START')
        run([sys.executable, str(ROOT/'scripts/services.py'), 'start'])
        service_binding = ROOT/'services-binding.json'
        if not service_binding.exists():
            raise SystemExit('Started services did not generate this machine\'s services-binding.json.')
        software.capture(ROOT,['runtimes/powercontext','runtimes/screenshot-to-code'])
        extra = json.loads(service_binding.read_text())
        binding['mcp_servers'].update(extra.get('mcp_servers', {}))
        binding['component_commands'].update(extra.get('component_commands', {}))
        (ROOT/'machine-binding.json').write_text(json.dumps(binding, indent=2)+'\n')
    (ROOT/'machine-binding.json').chmod(0o600)
    preparation.record(ROOT,'COMPLETE')
    print('Pinned core dependencies restored. Run bin/harness install, then enable a Git project.')


if __name__ == '__main__':
    main()
