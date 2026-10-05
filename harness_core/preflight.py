"""Read-only complete-preparation prerequisites, before registration or downloads."""
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .common import HarnessError, read_json, sha


def access_denied(result=None,error=None):
    if isinstance(error,PermissionError):return True
    return result is not None and result.returncode!=0 and any(
        term in (result.stderr or '').lower() for term in ('permission denied','operation not permitted'))


def inspect(services=True):
    checks=[]
    def add(name,ok,next_step,status=None,**facts):
        checks.append({'name':name,'status':status or ('PASS' if ok else 'MISSING'),'next_step':None if ok else next_step,**facts})
    def add_denied(name,label,**facts):
        add(name,False,'当前权限下未核验 '+label+'；在已授权且可访问该组件的环境中重跑 harness preflight，不要据此安装或重启，也不要开放业务项目 Full access。',
            status='UNVERIFIED',reason='ACCESS_DENIED',**facts)
    add('platform',platform.system()=='Linux' and platform.machine()=='x86_64',
        '当前完整准备仅支持已验证的 Linux x86_64；请在该环境运行。',system=platform.system(),architecture=platform.machine())
    names=['node','npm','uv','systemctl','systemd-run']+(['docker'] if services else [])
    for name in names:add(name,bool(shutil.which(name)),'安装 '+name+' 后重新运行 harness preflight。')
    root=Path(__file__).resolve().parents[1]
    node_version=None;node_valid=False;node_denied=False;engines={}
    try:
        p=subprocess.run(['node','--version'],capture_output=True,text=True,timeout=10)
        node_denied=access_denied(p)
        match=re.fullmatch(r'v([0-9]+)\.([0-9]+)\.([0-9]+)',p.stdout.strip())
        node_valid=p.returncode==0 and bool(match)
        numeric=tuple(map(int,match.groups())) if match else None
        if match:node_version=p.stdout.strip()
        locked=read_json(root/'manifests/source-trees.json',{})
        for name in ['context-mode','codegraph']:
            path=root/'vendor'/name/'package.json'
            expected=locked.get(name,{}).get('files',{}).get('package.json',{}).get('sha256')
            if not expected or path.is_symlink() or sha(path.read_bytes())!=expected:node_valid=False;continue
            rule=read_json(path,{}).get('engines',{}).get('node','');engines[name]=rule
            # The pinned sources use conjunctions of full-version comparators.
            # Unknown future syntax is unverified rather than silently accepted.
            terms=rule.split()
            if not terms:node_valid=False
            for term in terms:
                parsed=re.fullmatch(r'(>=|<)([0-9]+)\.([0-9]+)\.([0-9]+)',term)
                if not parsed or numeric is None:node_valid=False;continue
                bound=tuple(map(int,parsed.groups()[1:]));node_valid=node_valid and (numeric>=bound if parsed[1]=='>=' else numeric<bound)
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:
        node_valid=False;node_denied=access_denied(error=error)
    if node_denied:add_denied('node_compatibility','Node 版本及锁定组件兼容条件',version=node_version,locked_engines=engines)
    else:add('node_compatibility',node_valid,'按锁定组件要求准备可用的 Node 22.5 至 24 系列，再运行预检；未开始依赖安装。',version=node_version,locked_engines=engines)
    binary=os.environ.get('HARNESS_CODEX_BINARY','/usr/lib/chatgpt/resources/codex')
    try:compatibility=read_json(Path(__file__).resolve().parents[1]/'manifests/compatibility.yaml',{})
    except (ValueError,OSError):compatibility={}
    minimum=compatibility.get('minimum_codex_version')
    floor=tuple(map(int,minimum.split('.'))) if isinstance(minimum,str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',minimum) else None
    exists=Path(binary).is_file();valid=exists and os.access(binary,os.X_OK)
    codex_denied=exists and not valid
    version=None
    if valid:
        try:
            p=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=10)
            codex_denied=access_denied(p)
            q=None if codex_denied else subprocess.run([binary,'--help'],capture_output=True,text=True,timeout=10)
            codex_denied=codex_denied or access_denied(q)
            match=re.fullmatch(r'codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:[^\s]*)?)',p.stdout.strip())
            valid=not codex_denied and p.returncode==0 and q.returncode==0 and bool(match) and 'app-server' in q.stdout and 'exec' in q.stdout
            if match:
                version=match.group(1)
                numeric=tuple(map(int,re.match(r'([0-9]+)\.([0-9]+)\.([0-9]+)',version).groups()))
                valid=valid and floor is not None and numeric>=floor
        except (OSError,subprocess.TimeoutExpired) as error:
            valid=False;codex_denied=access_denied(error=error)
    facts={'binary':binary,'version':version,'minimum_version':minimum,
           'validation':'VERSION_AND_REQUIRED_ENTRY_POINTS; actual permissions and model calls require acceptance'}
    if codex_denied:add_denied('codex_runtime','Codex 运行时',**facts)
    else:add('codex_runtime',valid,'先安装 Codex Desktop；非默认安装位置请显式设置 HARNESS_CODEX_BINARY，再运行预检。',**facts)
    commands=[('npm_execution',['npm','--version'],'修复 npm 的可执行性后重试；未开始依赖安装。'),
              ('uv_execution',['uv','--version'],'修复 uv 的可执行性后重试；未开始 Python 环境准备。'),
              ('user_session',['systemctl','--user','is-active','default.target'],'恢复 systemd 用户会话后重试；当前未执行安装或提权。')]
    if services:
        commands.extend([('docker_compose',['docker','compose','version'],'安装 Docker Compose 插件后重试。'),
                         ('docker_daemon',['docker','info','--format','{{.ServerVersion}}'],'启动 Docker 并确认当前用户能访问 daemon，再重试；不要为此开放项目 Full access。')])
    for name,argv,next_step in commands:
        if not shutil.which(argv[0]):add(name,False,next_step);continue
        try:
            p=subprocess.run(argv,capture_output=True,text=True,timeout=15);ok=p.returncode==0
            denied=access_denied(p)
        except (OSError,subprocess.TimeoutExpired) as error:
            ok=False;denied=access_denied(error=error)
        if denied:
            label={'user_session':'systemd 用户会话','docker_daemon':'Docker 服务'}.get(name,argv[0])
            add_denied(name,label)
            continue
        add(name,ok,next_step)
    blocked=[c for c in checks if c['status']!='PASS']
    return {'status':'PREFLIGHT_PASSED' if not blocked else 'PREFLIGHT_BLOCKED','checks':checks,
            'missing':[c['name'] for c in blocked if c['status']=='MISSING'],
            'unverified':[c['name'] for c in blocked if c['status']=='UNVERIFIED'],
            'next_step':blocked[0]['next_step'] if blocked else '环境预检通过；按已确认安装范围执行完整准备。',
            'changes':'NONE','provider_keys':'NOT_REQUIRED_FOR_CORE_PREPARATION','login':'NOT_INSPECTED; native login/provider acceptance is separate',
            'scope':'LOCAL_PREREQUISITES_ONLY; dependency downloads and real component calls remain to be verified'}


def require(services=True):
    result=inspect(services)
    if result['status']!='PREFLIGHT_PASSED':
        raise HarnessError('完整组件准备前预检未通过：'+', '.join(result['missing']+result['unverified'])+'。'+result['next_step'])
    return result
