#!/usr/bin/env python3
"""Rebuild immutable-source sidecars without changing host dependencies."""
import argparse
import os
import subprocess
import json
from services_common import ROOT, PINS, prepare_runtime, verify_source

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--service', choices=['all','powercontext','screenshot-to-code'], default='all')
parser.add_argument('--validate-only', action='store_true', help='Verify accepted source hashes without building')
args = parser.parse_args()
names = list(PINS) if args.service == 'all' else [args.service]
for name in names:
    source = ROOT/'vendor'/name
    verification = verify_source(name)
    print(json.dumps({name: verification}), flush=True)
    if args.validate_only:
        continue
    prepare_runtime(name)
    if name == 'powercontext':
        subprocess.run(['docker','build','-f',str(ROOT/'runtimes'/name/'Dockerfile'),'--build-arg','POWERCONTEXT_VERSION=0.0.0+g6b2f6e8','-t','codex-harness-powercontext:6b2f6e8',str(source)],check=True)
        subprocess.run(['uv','sync','--frozen','--no-dev','--extra','cli','--extra','server'],cwd=source,env={**os.environ,'UV_CACHE_DIR':str(ROOT/'private/uv-cache'),'UV_PROJECT_ENVIRONMENT':str(ROOT/'runtimes/powercontext/client-venv'),'SETUPTOOLS_SCM_PRETEND_VERSION':'0.0.0+g6b2f6e8'},check=True)
    else:
        for component in ['backend','frontend']:
            subprocess.run(['docker','build','-f',str(ROOT/'runtimes'/name/f'Dockerfile.{component}'),'-t',f'codex-harness-screenshot-{component}:d026163',str(source)],check=True)
