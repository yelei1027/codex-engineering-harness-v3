"""Own stdio MCP lifetimes through Linux user systemd scopes, never PID guessing."""
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from contextlib import ExitStack
from .common import HarnessError, canonical, lock, new_id, now, read_json, sha, state_dir, write_json
from .transaction import safe_parent
from .software import process_identity

PROPERTIES=['Id','LoadState','ActiveState','ControlGroup','InvocationID','Description','Transient']


def properties(unit):
    if not re.fullmatch(r'harness-mcp-[a-f0-9]{16}-[a-f0-9]{12}\.scope',unit):raise HarnessError('Unknown managed MCP scope name')
    result=subprocess.run(['systemctl','--user','show',unit,*['--property='+p for p in PROPERTIES]],capture_output=True,text=True,timeout=20)
    values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('LoadState')=='not-found':return None
    if result.returncode:raise HarnessError('无法读取组件进程状态；保留程序：'+result.stderr[:500])
    return values


def identity(values):
    return {key:values.get(key) for key in ['Id','ControlGroup','InvocationID','Description','Transient']}


def active(values):
    return bool(values and (values.get('ActiveState') not in {'inactive','failed'} or values.get('ControlGroup')))


def verify_document(doc):
    unit=doc['unit'];values=properties(unit)
    if not active(values):return None
    expected=doc.get('scope_identity')
    if not expected or identity(values)!=expected or values.get('Transient')!='yes':
        raise HarnessError('组件进程归属不一致或启动尚未登记，保留程序和进程：'+unit)
    return values


def documents(root):
    result=[]
    for path in sorted((state_dir()/'mcp-processes').glob('*.json')):
        safe_parent(path)
        if path.is_symlink():raise HarnessError('MCP process receipt is a symlink')
        doc=read_json(path)
        if doc.get('root')==str(root):result.append((path,doc))
    return result


def plan(root):
    scopes=[]
    for path,doc in documents(root):
        values=verify_document(doc)
        if values:scopes.append({'unit':doc['unit'],'repo':doc['repo'],'server':doc['server'],
                                'scope_identity':doc['scope_identity'],'receipt':str(path)})
        elif doc.get('status')=='STARTING' and process_identity(doc['launcher_pid'])==doc.get('launcher_identity'):
            raise HarnessError('组件仍在启动；等待接入完成后重试')
    return {'status':'MANAGED_PROCESSES','root':str(root),'active_scopes':scopes,
            'ownership':'Exact recorded systemd invocation and control group; unrelated processes are never selected'}


def stop_document(path,doc):
    if verify_document(doc):
        result=subprocess.run(['systemctl','--user','stop',doc['unit']],capture_output=True,text=True,timeout=30)
        if result.returncode or active(properties(doc['unit'])):
            raise HarnessError('受管组件未正常停止；程序和数据保留：'+doc['unit']+' '+result.stderr[:400])
    current=read_json(path,doc);current.update(status='STOPPED',stopped=now());write_json(path,current)


def stop(root):
    # The caller holds the root service lock across checking and detaching config.
    plan(root)
    for path,doc in documents(root):stop_document(path,doc)
    return {'status':'MANAGED_PROCESSES_STOPPED','root':str(root)}


def require_idle(root):
    if plan(root)['active_scopes']:raise HarnessError('仍有组件进程使用此安装；先关闭相应会话或使用隔离候选目录')


def worker(path):
    path=Path(path);safe_parent(path)
    if path.is_symlink():raise HarnessError('MCP process receipt is a symlink')
    doc=read_json(path);root=Path(doc['root'])
    server=read_json(root/'machine-binding.json',{}).get('mcp_servers',{}).get(doc['server'])
    if not server or sha(canonical(server))!=doc['binding_hash']:raise HarnessError('MCP binding changed before startup')
    values=properties(doc['unit'])
    if (not active(values) or values.get('Description')!=doc['description'] or values.get('Transient')!='yes'
            or not values.get('InvocationID') or not values.get('ControlGroup')):
        raise HarnessError('MCP scope identity unavailable; component was not started')
    with lock(path.with_suffix('.lock')):
        doc=read_json(path)
        if doc['status']!='STARTING' or process_identity(doc['launcher_pid'])!=doc['launcher_identity']:
            raise HarnessError('MCP launcher is no longer active; component was not started')
        doc.update(status='REGISTERED',scope_identity=identity(values),worker_pid=os.getpid(),worker_identity=process_identity(os.getpid()))
        write_json(path,doc)
    # Component exec is gated by a live connection, not a disk "approved" flag.
    # This is startup coordination only, never human task/tool authorization.
    gate='\0harness-mcp-'+sha(str(path).encode())[:32]
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(35);connection.connect(str(gate))
        if connection.recv(2)!=b'GO':raise HarnessError('MCP startup cancelled; component was not started')
    current=read_json(path)
    if current['status']!='RUNNING' or process_identity(doc['launcher_pid'])!=doc['launcher_identity']:
        raise HarnessError('MCP launcher ended before exec; component was not started')
    env={**os.environ,**server.get('env',{})};os.chdir(doc['repo'])
    os.execvpe(server['command'],[server['command'],*server.get('args',[])],env)


def require_host():
    ready=subprocess.run(['systemctl','--user','is-active','default.target'],capture_output=True,timeout=10)
    if ready.returncode:raise HarnessError('用户级systemd不可用；无法安全管理组件进程，请检查桌面用户会话')


def launch(root,repo,name):
    from . import cli
    root=Path(root);repo=Path(repo);require_host()
    process=None;path=None;listener=None
    def shutdown(signum,frame):raise HarnessError('MCP连接已关闭；结束该受管组件')
    previous={s:signal.signal(s,shutdown) for s in [signal.SIGTERM,signal.SIGINT]}
    try:
        with ExitStack() as stack:
            # Multiple native MCP servers initialize concurrently. Read locks
            # prevent lifecycle mutation while allowing independent startups;
            # live scope receipts guard maintenance once startup completes.
            for filename in ['preparation.lock','services.lock']:stack.enter_context(lock(root/'.harness-install'/filename,shared=True))
            binding=cli.project_binding(repo)
            if binding['harness_root']!=str(root):raise HarnessError('MCP project belongs to a different installation')
            cli.verify_owned(cli.registry().get(str(repo),{}))
            server=cli.machine_binding().get('mcp_servers',{}).get(name)
            if not isinstance(server,dict) or not isinstance(server.get('command'),str):raise HarnessError('MCP server has no owned stdio command')
            identifier=new_id('mcp');unit='harness-mcp-'+sha(str(root).encode())[:16]+'-'+identifier.rsplit('-',1)[-1]+'.scope'
            if properties(unit):raise HarnessError('MCP scope name already exists; preserve')
            path=state_dir()/'mcp-processes'/f'{identifier}.json';safe_parent(path)
            doc={'id':identifier,'root':str(root),'repo':str(repo),'server':name,'unit':unit,'description':'Harness MCP '+identifier,
                 'status':'STARTING','created':now(),'launcher_pid':os.getpid(),'launcher_identity':process_identity(os.getpid()),
                 'binding_hash':sha(canonical(server))}
            write_json(path,doc)
            gate='\0harness-mcp-'+sha(str(path).encode())[:32]
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(gate)
            listener.listen(1);listener.settimeout(.1)
            code='import sys;sys.path.insert(0,'+repr(str(root))+');from harness_core.processes import worker;worker(sys.argv[1])'
            process=subprocess.Popen(['systemd-run','--user','--scope','--quiet','--unit='+unit,'--description='+doc['description'],
                        '--property=TimeoutStopSec=10s','--property=SendSIGKILL=no',sys.executable,'-c',code,str(path)],cwd=repo)
            deadline=time.monotonic()+30
            while True:
                if process.poll() is not None:raise HarnessError('MCP组件启动失败；详见组件stderr')
                if time.monotonic()>deadline:raise HarnessError('组件启动登记超时；保留未核验进程清单，先诊断后重试')
                try:connection,_=listener.accept()
                except socket.timeout:continue
                with connection,lock(path.with_suffix('.lock')):
                    current=read_json(path)
                    peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if (current['status']!='REGISTERED' or peer[0]!=current.get('worker_pid')
                            or process_identity(peer[0])!=current.get('worker_identity')):
                        raise HarnessError('MCP startup peer differs; component was not started')
                    if not verify_document(current):raise HarnessError('MCP scope ended before startup acknowledgement')
                    current.update(status='RUNNING',started=now());write_json(path,current)
                    connection.sendall(b'GO')
                break
        return process.wait()
    finally:
        if listener:listener.close()
        if path and path.exists():
            with lock(path.with_suffix('.lock')):
                current=read_json(path)
                if current.get('status') in {'STARTING','REGISTERED'}:
                    current.update(status='STARTUP_FAILED',finished=now());write_json(path,current)
            if current.get('scope_identity'):
                stop_document(path,current)
            elif active(properties(current['unit'])):
                current.update(status='STARTUP_UNVERIFIED_PRESERVED',finished=now());write_json(path,current)
                print('启动未核验，保留该进程实例并阻止后续维护：'+current['unit'],file=sys.stderr,flush=True)
            else:
                current.update(status='STOPPED',stopped=now());write_json(path,current)
        for signum,handler in previous.items():signal.signal(signum,handler)


def unregistered_consumers(root,scope_plan=None):
    runtime=Path(root)/'runtimes';groups={item['scope_identity']['ControlGroup'] for item in (scope_plan or plan(root))['active_scopes']}
    def inside(value):
        path=Path(value.removesuffix(' (deleted)'))
        return path.is_absolute() and (path==runtime or runtime in path.parents)
    found=[]
    for proc in Path('/proc').glob('[0-9]*'):
        if proc.name==str(os.getpid()):continue
        try:
            if proc.stat().st_uid!=os.getuid():continue
            argv=[v.decode(errors='replace') for v in (proc/'cmdline').read_bytes().split(b'\0') if v]
            if not argv:continue
            executable=os.readlink(proc/'exe');cwd=os.readlink(proc/'cwd')
            using=(inside(executable) or any(inside(arg.split('=',1)[-1]) for arg in argv)
                   or (Path(executable).name.startswith(('python','node','npm','uv')) and inside(cwd)))
            if not using:continue
            membership=[line.split(':',2)[2] for line in (proc/'cgroup').read_text().splitlines()]
            if any(group==member or member.startswith(group+'/') for group in groups for member in membership):continue
            found.append({'pid':int(proc.name),'status':'UNREGISTERED_RUNTIME_CONSUMER'})
        except (OSError,ValueError,IndexError):continue
    return found
