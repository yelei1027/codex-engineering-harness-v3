#!/usr/bin/env bash
set -euo pipefail
# Standalone entry point: safe before executing any downloaded code.
python3 - "${1:-v3.1.1}" <<'PY'
import fcntl, hashlib, json, os, re, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath

def fail(message): raise RuntimeError(message)
def call(argv): subprocess.run(argv, check=True)
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def entry(path):
    return {'link': os.readlink(path)} if path.is_symlink() else {'sha256': digest(path)} if path.is_file() else None

tag = sys.argv[1]
phase = '检查安装条件'
try:
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?', tag): fail('版本格式无效。')
    if os.uname().sysname != 'Linux' or os.uname().machine != 'x86_64': fail('当前安装器仅验证 Linux x86_64。')
    version = tag[1:]
    asset = f'codex-engineering-harness-{version}-linux-x86_64.tar.gz'
    target = Path(os.environ.get('HARNESS_INSTALL_DIR', str(Path.home()/'.local/share/codex-engineering-harness/releases'/version))).expanduser().absolute()
    state = Path(os.environ.get('HARNESS_STATE_DIR', str(Path.home()/'.local/share/codex-engineering-harness'))).expanduser().absolute()
    os.environ['HARNESS_STATE_DIR'] = str(state)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    for path in (target, state):
        if '..' in path.parts or path in (Path(path.anchor), Path.home()): fail('请选择专用安装/状态目录。')
        if any(p.is_symlink() for p in (path, *path.parents)): fail('安装/状态目录包含符号链接；保留原文件并选择专用目录。')
    if state == target or target in state.parents: fail('状态目录必须在安装目录之外。')
    receipt_path = target/'.harness-install/receipt.json'
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        if receipt_path.is_symlink() or receipt_path.parent.is_symlink(): fail('安装记录包含未知链接；保留目录。')
        try: receipt = json.loads(receipt_path.read_text())
        except (OSError, ValueError): fail('目标目录已有未受管内容；文件已保留，请选择新的 HARNESS_INSTALL_DIR。')
        if not isinstance(receipt,dict): fail('安装记录无效；保留原目录。')
        if any(receipt.get(k) != v for k,v in {'target':str(target),'state':str(state),'tag':tag}.items()): fail('目标目录不属于本次安装；文件已保留。')
    for name in ('gh','npm','uv','docker','systemctl','systemd-run'):
        if not shutil.which(name): fail('缺少依赖: '+name)
    call(['docker','compose','version']); call(['gh','auth','status']); call(['systemctl','--user','is-active','default.target'])
    state.mkdir(parents=True, exist_ok=True); target.parent.mkdir(parents=True, exist_ok=True)
    with (state/'install-release.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state/'accepted.json').exists(): fail('已有可用 Harness；请使用 harness update，不能覆盖安装。')
        with tempfile.TemporaryDirectory() as download, tempfile.TemporaryDirectory(prefix='.harness-staging-',dir=target.parent) as staging:
            phase = '下载与校验'
            call(['gh','release','download',tag,'--repo','yelei1027/codex-engineering-harness-v3','--pattern',asset+'*','--dir',download])
            archive = Path(download)/asset; checksum = (Path(download)/(asset+'.sha256')).read_text().strip(); archive_hash = digest(archive)
            if not re.fullmatch(re.escape(archive_hash)+r' [ *]'+re.escape(asset), checksum): fail('Release 校验失败；未更改目标目录。')
            phase = '验证发行包'
            with tarfile.open(archive,'r:gz') as tar:
                seen, links = set(), set()
                for member in tar.getmembers():
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'codex-engineering-harness': fail('Release 包含不安全路径。')
                    if str(path) in seen or '.harness-install' in path.parts: fail('Release 包含重复路径或安装记录。')
                    seen.add(str(path))
                    if member.issym():
                        normalized = PurePosixPath(os.path.normpath(str(path.parent/member.linkname)))
                        if len(path.parts)<4 or path.parts[1]!='vendor' or PurePosixPath(member.linkname).is_absolute() or normalized.parts[:3]!=path.parts[:3]: fail('Release 包含不安全链接。')
                        links.add(path)
                    elif not (member.isfile() or member.isdir()): fail('Release 包含非普通文件。')
                if any(link in PurePosixPath(name).parents for link in links for name in seen): fail('Release 不允许通过链接写入文件。')
                # Prevalidated full member set; ignore archive UID/GID and special mode bits.
                for member in tar.getmembers():
                    member.mode &= 0o777
                    tar.extract(member,staging,set_attrs=False,numeric_owner=False)
                    if not member.issym(): (Path(staging)/member.name).chmod(member.mode)
            src = Path(staging)/'codex-engineering-harness'
            if not (src/'bin/harness').is_file() or not os.access(src/'bin/harness',os.X_OK): fail('Release 内容不完整。')
            files = {str(p.relative_to(src)):entry(p) for p in src.rglob('*') if p.is_file() or p.is_symlink()}
            expected = {'target':str(target),'state':str(state),'tag':tag,'archive_sha256':archive_hash,'files':files}
            if target.exists() and any(target.iterdir()):
                if receipt_path.is_symlink() or receipt_path.parent.is_symlink(): fail('安装记录已变化；保留目录。')
                receipt = json.loads(receipt_path.read_text())
                if not isinstance(receipt,dict): fail('安装记录无效；保留原目录。')
                if any(receipt.get(k)!=v for k,v in expected.items()): fail('安装来源或所有权已变化；保留目录并停止。')
                for name, recorded in files.items():
                    p=target/name
                    if any(parent.is_symlink() for parent in p.parents): fail('安装源路径已变化；保留目录并停止。')
                    if entry(p)!=recorded: fail('安装源文件已变化：'+name+'；保留改动并停止。')
            else:
                (src/'.harness-install').mkdir(mode=0o700)
                (src/'.harness-install/receipt.json').write_text(json.dumps(expected)+'\n')
                (src/'.harness-install/receipt.json').chmod(0o600)
                # Rename refuses a nonempty competing target; never delete an existing target.
                os.rename(src,target)
            phase='静态检查'; call([sys.executable,str(target/'bin/harness'),'check'])
            help_text=subprocess.check_output([sys.executable,str(target/'bin/harness'),'install','--help'],text=True)
            if '--prepare' in help_text:
                phase='恢复保留数据、完整组件准备与登记'; call([sys.executable,str(target/'bin/harness'),'install','--prepare'])
            else:
                # Older published archives retain their own supported bootstrap
                # route. No claim that they implement the new recovery workflow.
                phase='旧版完整组件准备'; call([sys.executable,str(target/'scripts/bootstrap.py'),'--services'])
                phase='旧版安装登记'; call([sys.executable,str(target/'bin/harness'),'install'])
    print(f'\nHarness {version} 程序与完整组件已准备。\n安装目录: {target}')
    print('项目接入、Hook 原生信任和真实调用仍需分别核验。')
    print('下一步：harness start /你的项目路径。已接入项目的聊天中明确说“用 Harness”。')
    print('首次项目准备前查看影响并确认；Hook 信任按 Codex 原生提示处理。')
    print('如 shell 找不到 harness，将 $HOME/.local/bin 加入 PATH。')
except (RuntimeError,OSError,ValueError,subprocess.CalledProcessError,tarfile.TarError) as error:
    print(f'\n安装未完成：{phase}。{error}\n已有文件和准备进度保留；修复失败原因后，使用相同版本和目录重试。',file=sys.stderr)
    sys.exit(1)
PY
