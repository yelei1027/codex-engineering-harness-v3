#!/usr/bin/env python3
"""Rebuild immutable-source sidecars without changing host dependencies."""
import argparse
import os
import subprocess
import json
from services_common import ROOT, PINS, prepare_runtime, verify_source
import sys
sys.path.insert(0,str(ROOT))
from harness_core import sidecars,software,processes,preparation
from harness_core.common import lock

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--service', choices=['all','powercontext','screenshot-to-code'], default='all')
parser.add_argument('--validate-only', action='store_true', help='Verify accepted source hashes without building')
args = parser.parse_args()
names = list(PINS) if args.service == 'all' else [args.service]
with lock(ROOT/'.harness-install/services.lock'):
    processes.require_idle(ROOT)
    software.recover_preparation(ROOT)
    for name in names:
        if not args.validate_only:
            preparation.record(ROOT,'SERVICES_VERIFY')
        source = ROOT/'vendor'/name
        verification = verify_source(name)
        print(json.dumps({name: verification}), flush=True)
        if args.validate_only:
            continue
        if sidecars.reusable_build(ROOT,name):
            print(json.dumps({name:'VERIFIED_BUILD_REUSED'}),flush=True)
            continue
        preparation.record(ROOT,'POWERCONTEXT_BUILD' if name=='powercontext' else 'SCREENSHOT_BACKEND')
        prepare_runtime(name)
        sidecars.preflight_build(ROOT,name)
        images=sidecars.layout(ROOT)['images']
        if name == 'powercontext':
            software.require_prepared_or_empty(ROOT,'runtimes/powercontext/client-venv')
            subprocess.run(['docker','build','-f',str(ROOT/'runtimes'/name/'Dockerfile'),'--build-arg','POWERCONTEXT_VERSION=0.0.0+g6b2f6e8','-t',images['powercontext'],str(source)],check=True)
            sidecars.record_image(ROOT,'powercontext')
            preparation.record(ROOT,'POWERCONTEXT_CLIENT')
            with software.preserve_data(ROOT,'runtimes/powercontext/client-venv') as package:
                package(['uv','sync','--frozen','--no-dev','--extra','cli','--extra','server'],cwd=source,env={**os.environ,'UV_CACHE_DIR':str(ROOT/'private/uv-cache'),'UV_PROJECT_ENVIRONMENT':str(ROOT/'runtimes/powercontext/client-venv'),'SETUPTOOLS_SCM_PRETEND_VERSION':'0.0.0+g6b2f6e8'})
        else:
            for component in ['backend','frontend']:
                preparation.record(ROOT,'SCREENSHOT_'+component.upper())
                subprocess.run(['docker','build','-f',str(ROOT/'runtimes'/name/f'Dockerfile.{component}'),'-t',images['screenshot-'+component],str(source)],check=True)
                sidecars.record_image(ROOT,'screenshot-'+component)
        sidecars.record_images(ROOT,name)
        sidecars.record_build(ROOT,name)
