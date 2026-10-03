#!/usr/bin/env python3
"""Manage isolated Harness sidecars; stop preserves named data volumes."""
from pathlib import Path
import argparse
import subprocess
from services_common import prepare_runtime, write_service_binding

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['start', 'stop', 'status', 'logs', 'doctor'])
parser.add_argument('--service', choices=['all', 'powercontext', 'screenshot-to-code'], default='all')
args = parser.parse_args()
names = ['powercontext', 'screenshot-to-code'] if args.service == 'all' else [args.service]
for name in names:
    if args.action == 'start':
        prepare_runtime(name)
    base = ['docker', 'compose', '-p', 'codex-harness', '-f', str(ROOT/'runtimes'/name/'compose.yaml')]
    if args.action == 'doctor' and name == 'powercontext':
        subprocess.run(['docker', 'exec', 'codex-harness-powercontext', 'powercontext', 'doctor', '--server-url', 'http://127.0.0.1:8000', '--json'], check=True)
        continue
    if args.action == 'doctor':
        command = ['ps']
    else:
        command = {'start': ['up', '-d', '--no-build'], 'stop': ['stop'], 'status': ['ps'], 'logs': ['logs', '--tail', '60']}[args.action]
    subprocess.run(base + command, check=True)
if args.action == 'start':
    write_service_binding()
