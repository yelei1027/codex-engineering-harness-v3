#!/usr/bin/env python3
"""Restore pinned local dependency environments; never registers global MCP/hooks."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(argv, cwd=None, env=None):
    subprocess.run(argv, cwd=cwd or ROOT, env=env or os.environ, check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--services', action='store_true', help='also build/start locked loopback Docker sidecars')
    a = p.parse_args()
    runtime = ROOT / 'runtimes/node'
    runtime.mkdir(parents=True, exist_ok=True)
    import shutil
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(ROOT / 'manifests/node' / name, runtime / name)
    run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', str(ROOT/'private/npm-cache')], cwd=runtime)
    uv_env = {**os.environ, 'UV_CACHE_DIR': str(ROOT/'private/uv-cache')}
    py = ROOT/'runtimes/markitdown/bin/python'
    run(['uv', 'venv', '--python', '3.12', str(py.parents[1])], env=uv_env)
    run(['uv', 'pip', 'install', '--python', str(py), '-r', str(ROOT/'manifests/markitdown-requirements.lock')], env=uv_env)
    run(['uv', 'pip', 'install', '--python', str(py), '--no-deps',
         str(ROOT/'vendor/markitdown/packages/markitdown'), str(ROOT/'vendor/markitdown/packages/markitdown-mcp')], env=uv_env)
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
        run([sys.executable, str(ROOT/'scripts/services-build.py')])
        run([sys.executable, str(ROOT/'scripts/services.py'), 'start'])
        service_binding = ROOT/'services-binding.json'
        if not service_binding.exists():
            raise SystemExit('Started services did not generate this machine\'s services-binding.json.')
        extra = json.loads(service_binding.read_text())
        binding['mcp_servers'].update(extra.get('mcp_servers', {}))
        binding['component_commands'].update(extra.get('component_commands', {}))
        (ROOT/'machine-binding.json').write_text(json.dumps(binding, indent=2)+'\n')
    (ROOT/'machine-binding.json').chmod(0o600)
    print('Pinned core dependencies restored. Run bin/harness install, then enable a Git project.')


if __name__ == '__main__':
    main()
