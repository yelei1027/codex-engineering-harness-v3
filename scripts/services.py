#!/usr/bin/env python3
"""Owned service entry point; the public Harness CLI exposes the same operations."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness_core.sidecars import manage
from harness_core.common import HarnessError,lock,state_dir
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('action',choices=['start','stop','status','remove'])
p.add_argument('--service',choices=['all','powercontext','screenshot-to-code'],default='all')
a=p.parse_args()
try:
    result=manage(ROOT,a.action,a.service)
    print(json.dumps(result,ensure_ascii=False,indent=2))
except (HarnessError,OSError,ValueError) as e:
    print(json.dumps({'status':'ERROR','error':str(e)},ensure_ascii=False));sys.exit(2)
