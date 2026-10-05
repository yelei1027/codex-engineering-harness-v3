#!/usr/bin/env python3
"""Internal bounded worker for a preselected registered retained memory service."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness_core import memory_services
from harness_core.common import HarnessError
p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop','status']);p.add_argument('--origin',required=True)
a=p.parse_args()
try:
    print(json.dumps(memory_services.manage_one(a.origin,a.action)))
except (HarnessError,OSError,ValueError):
    print(json.dumps({'status':'ERROR','error':'Retained memory operation refused; data preserved'}));sys.exit(2)
