"""Owned user-systemd registration for the lightweight resource gateway."""
import json
import os
import subprocess
import sys
from pathlib import Path

from . import sidecars
from .common import HarnessError, read_json, sha, canonical
from .transaction import Transaction, safe_parent, image


def unit_name(root):
    return 'harness-resources-'+sidecars.root_id(root)+'.service'


def quoted(value, *, command=False):
    value = str(value)
    if any(ord(c) < 32 for c in value):
        raise HarnessError('Control characters in systemd path')
    # systemd performs specifier and ExecStart variable expansion even without a shell.
    value = value.replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    return json.dumps(value, ensure_ascii=False)


def unit_text(root):
    root = Path(root).resolve()
    state = sidecars.owner_state(root)
    return ('[Unit]\nDescription=Harness owned resource gateway '+sidecars.root_id(root)+'\n'
            '\n[Service]\nType=simple\n'
            'ExecStart='+quoted(sys.executable, command=True)+' -B '+quoted(root/'scripts/resource-manager.py', command=True)+'\n'
            'WorkingDirectory='+str(root).replace('%', '%%')+'\n'
            'Environment='+quoted('HARNESS_STATE_DIR='+str(state))+'\n'
            'Environment=PYTHONDONTWRITEBYTECODE=1\n'
            'Restart=on-failure\nRestartSec=3\nKillMode=control-group\n'
            'TimeoutStopSec=15\nUMask=0077\nNoNewPrivileges=yes\n'
            '\n[Install]\nWantedBy=default.target\n')


def unit_path(root):
    directory = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
    if not directory.is_absolute():
        raise HarnessError('User systemd configuration root must be absolute')
    path = directory/'systemd/user'/unit_name(root)
    safe_parent(path)
    if path.is_symlink():
        raise HarnessError('Systemd unit is a symlink; preserve')
    return path


def receipt_path(root):
    path = Path(root)/'.harness-install/resource-unit.json'
    safe_parent(path)
    if path.is_symlink():
        raise HarnessError('Systemd receipt is a symlink; preserve')
    return path


def verify(root):
    path = unit_path(root)
    receipt = read_json(receipt_path(root), {})
    if not receipt:
        if path.exists():
            raise HarnessError('Unknown systemd unit; preserve')
        return None
    if (receipt.get('root') != str(Path(root).resolve()) or receipt.get('path') != str(path)
            or receipt.get('image') != image(path)):
        raise HarnessError('Systemd unit ownership changed; preserve')
    return receipt


def systemctl(*args):
    try:
        result = subprocess.run(['systemctl','--user',*args], capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise HarnessError('User systemd unavailable') from None
    if result.returncode:
        raise HarnessError('User systemd operation failed; gateway registration may remain')
    return result.stdout


def install(root):
    root = Path(root).resolve()
    verify(root)
    path = unit_path(root)
    content = unit_text(root).encode()
    with Transaction('Register owned resource gateway unit') as tx:
        tx.change(path, content, mode=0o600)
        record = {'root':str(root),'path':str(path),'unit':unit_name(root),'image':image(path)}
        tx.change(receipt_path(root), canonical(record)+b'\n', mode=0o600)
    systemctl('daemon-reload')
    systemctl('enable','--now',unit_name(root))
    return status(root)


def status(root):
    receipt = verify(root)
    if not receipt:
        return {'status':'NOT_REGISTERED','unit':unit_name(root)}
    raw = systemctl('show',unit_name(root),'--property=ActiveState','--property=SubState',
                    '--property=MainPID','--property=MemoryCurrent','--property=FragmentPath')
    values = dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    if values.get('FragmentPath') != receipt['path']:
        raise HarnessError('Running systemd unit comes from another file')
    memory = values.get('MemoryCurrent', '')
    return {'status':'RUNNING' if values.get('ActiveState')=='active' else 'REGISTERED_NOT_RUNNING',
            'unit':unit_name(root),'state':values.get('ActiveState'),'substate':values.get('SubState'),
            'pid':int(values.get('MainPID','0')),
            'memory_bytes':int(memory) if memory.isdigit() else None}


def remove(root):
    receipt = verify(root)
    if not receipt:
        return {'status':'NOT_REGISTERED'}
    systemctl('disable','--now',unit_name(root))
    with Transaction('Remove owned resource gateway unit') as tx:
        tx.change(unit_path(root), delete=True)
        tx.change(receipt_path(root), delete=True)
    systemctl('daemon-reload')
    return {'status':'REMOVED','unit':unit_name(root)}
