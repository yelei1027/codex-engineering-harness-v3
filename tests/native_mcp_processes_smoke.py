"""Actual owned user-scope stdio lifetime and uninstall; disposable resources only."""
import argparse
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
from test_public_lifecycle import PublicLifecycleCase
from harness_core import software,processes
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
case=PublicLifecycleCase();case.setUp();case.env['PATH']=os.environ['PATH'];release=case.release();connection=None;unrelated=None;record={'scope':'Real user systemd scope, stdio bytes, detached worker cleanup and public uninstall; no real model/GUI proof','checks':[]}
try:
    runtime=release/'runtimes/stdio';runtime.mkdir(parents=True);server=runtime/'server.py';pidfile=case.base/'detached-worker.pid'
    server.write_text('''import subprocess,sys,os
from pathlib import Path
worker=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path('''+repr(str(pidfile))+''').write_text(str(worker.pid))
print('READY',flush=True)
for line in sys.stdin:print('ECHO:'+line.strip(),flush=True)
''')
    (runtime/'memory.sqlite').write_bytes(b'kept component database')
    (release/'machine-binding.json').write_text(json.dumps({'codex_binary':'/bin/echo','mcp_servers':{'echo-fixture':{'command':sys.executable,'args':[str(server)]}}}))
    with patch.dict(os.environ,case.env):software.capture(release,['runtimes/stdio'])
    case.public(release,'install');case.public(release,'prepare',case.repo,'--apply','--profile','L1')
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
    connection=subprocess.Popen([sys.executable,str(release/'bin/harness'),'mcp-run',str(case.repo),'--server','echo-fixture'],cwd=case.repo,env=case.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def line():
        readable,_,_=select.select([connection.stdout],[],[],15)
        if not readable:
            if connection.poll() is not None:raise RuntimeError(connection.stderr.read())
            raise RuntimeError('Owned stdio server did not respond')
        value=connection.stdout.readline()
        if not value:raise RuntimeError(connection.stderr.read())
        return value.strip()
    assert line()=='READY'
    connection.stdin.write('protocol-marker\n');connection.stdin.flush();assert line()=='ECHO:protocol-marker'
    _,state=case.public(release,'processes','status');assert len(state['active_scopes'])==1
    unit=state['active_scopes'][0]['unit'];record['checks'].append({'name':'owned_stdio_live','unit':unit,'transport':'STDIO marker roundtrip'})
    _,preview=case.public(release,'uninstall');assert len(preview['component_processes']['active_scopes'])==1
    _,removed=case.public(release,'uninstall','--apply');connection.wait(timeout=20)
    with patch.dict(os.environ,case.env):assert not processes.properties(unit)
    # The detached worker is part of the cgroup even though its session differs.
    pid=int(pidfile.read_text());stat=Path('/proc',str(pid),'stat')
    if stat.exists():assert stat.read_text().split(')')[-1].strip().split()[0]=='Z'
    assert unrelated.poll() is None
    retained=Path(removed['preserved_data']['local_install_data'])
    assert (retained/'runtime-data/runtimes/stdio/memory.sqlite').read_bytes()==b'kept component database'
    assert not server.exists();assert not (case.repo/'.harness/state/project.json').exists()
    record['checks'].extend([{'name':'owned_stdio_and_detached_worker_stopped'},{'name':'unrelated_process_survived'},{'name':'software_removed_database_retained'}]);record['status']='PASS'
except Exception as error:record.update(status='FAIL',error=str(error))
finally:
    # Only scopes created in this fixture's exact receipt set are stopped.
    with patch.dict(os.environ,case.env):
        try:processes.stop(release)
        except Exception as error:record['cleanup_warning']=str(error)
    if connection:
        if connection.poll() is None:connection.terminate()
        try:connection.communicate(timeout=15)
        except subprocess.TimeoutExpired:connection.kill();connection.communicate()
    if unrelated:unrelated.terminate();unrelated.wait(timeout=10)
    case.doCleanups()
    if args.output:args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
    if record['status']!='PASS':sys.exit(1)
