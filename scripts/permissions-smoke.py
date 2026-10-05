#!/usr/bin/env python3
"""Probe native command boundaries in disposable files, never assert GUI/model coverage."""
import argparse
import json
import queue
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from codex_inventory import AppServer


def probe(binary, parent):
    parent = parent.expanduser().absolute()
    if any(p.is_symlink() for p in (parent, *parent.parents)):
        raise ValueError('Probe parent must not contain symlinks')
    parent.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output([binary, '--version'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='permissions-probe-', dir=parent) as temporary:
        base = Path(temporary)
        project = base/'project';project.mkdir()
        home = base/'codex';home.mkdir()
        source = project/'business.txt';source.write_text('original')
        outside = base/'outside.txt';outside.write_text('outside')
        protected = project/'.codex/config.toml';protected.parent.mkdir();protected.write_text('')
        receipts = []
        client = AppServer(binary, project, home)
        try:
            def execute(name, profile, code, expected_exit, expected_source):
                result = client.call('command/exec', {'cwd':str(project), 'permissionProfile':profile,
                    'command':[sys.executable,'-c','print("HARNESS_PROBE_EXECUTED",flush=True);'+code], 'timeoutMs':10000}, timeout=20)
                ok = (result['exitCode']==0) == (expected_exit==0)
                ok = ok and 'HARNESS_PROBE_EXECUTED' in result['stdout'] and source.read_text()==expected_source
                if expected_exit!=0:
                    ok = ok and bool(re.search(r'\[Errno (?:1|13|30)\]',result['stderr']))
                receipt={'name':name, 'profile':profile, 'expected':'ALLOW' if expected_exit==0 else 'DENY',
                    'status':'PASS' if ok else 'FAIL', 'exit_code':result['exitCode'],
                    'stdout':result['stdout'][-1500:], 'stderr':result['stderr'][-1500:],
                    'source_contents':source.read_text()}
                receipts.append(receipt)
                return receipt
            execute('read_before_scope',':read-only',
                    'from pathlib import Path;assert Path("business.txt").read_text()=="original";print("READ_OK")',0,'original')
            write='from pathlib import Path;Path("business.txt").write_text("changed")'
            execute('write_before_scope',':read-only',write,1,'original')
            # A subprocess cannot bypass its parent's OS file restriction.
            execute('subprocess_before_scope',':read-only',
                    'import subprocess,sys;sys.exit(subprocess.run([sys.executable,"-c",'+repr(write)+']).returncode)',1,'original')
            execute('write_in_workspace',':workspace',write,0,'changed')
            deny_outside=execute('write_outside_workspace',':workspace',
                    'from pathlib import Path;Path('+repr(str(outside))+').write_text("unexpected")',1,'changed')
            if outside.read_text()!='outside':deny_outside['status']='FAIL'
            config_denial=execute('protected_config',':workspace',
                    'from pathlib import Path;Path(".codex/config.toml").write_text("unexpected")',1,'changed')
            if protected.read_text()!='':config_denial['status']='FAIL'
            execute('write_after_readonly_restore',':read-only',
                    'from pathlib import Path;Path("business.txt").write_text("unexpected")',1,'changed')
        finally:
            client.close()
    return {'status':'PASS' if all(r['status']=='PASS' for r in receipts) else 'FAIL',
        'binary':binary, 'version':version, 'receipts':receipts,
        'scope':'ACTUAL_NATIVE_APP_SERVER_COMMAND_BOUNDARIES; client-selected profiles in disposable files',
        'unverified':['Desktop chat/model tool selection','Native patch/editor tools','App/MCP authorization',
                      'Human approval provenance','Active-turn permission revocation','GUI journey'],
        'current_chat_permissions':'NOT_CHANGED'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',default='/usr/lib/chatgpt/resources/codex')
    parser.add_argument('--work-parent',default=str(Path(__file__).resolve().parents[1]/'.test-projects'))
    parser.add_argument('--output')
    args=parser.parse_args()
    try:result=probe(args.binary,Path(args.work_parent))
    except (OSError,ValueError,RuntimeError,TimeoutError,queue.Empty,subprocess.SubprocessError) as error:
        result={'status':'FAIL','error':str(error),'scope':'Probe did not establish native boundary coverage'}
    if args.output:
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':sys.exit(main())
