"""Append-only filesystem move intents and whole-set conflict preflight."""
import json
import os
import stat
from pathlib import Path
from .common import HarnessError
from .transaction import image,safe_parent


def describe(path):
    path=Path(path)
    if not path.is_dir() or path.is_symlink():return image(path)
    st=path.stat();entries={}
    for directory,dirs,files in os.walk(path,followlinks=False):
        for name in list(dirs):
            target=Path(directory)/name
            if target.is_symlink():files.append(name);dirs.remove(name)
            else:
                info=target.stat();entries[str(target.relative_to(path))]={'kind':'directory','mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid}
        for name in files:
            target=Path(directory)/name;entries[str(target.relative_to(path))]=image(target)
    return {'kind':'directory','mode':stat.S_IMODE(st.st_mode),'uid':st.st_uid,'gid':st.st_gid,'entries':entries}


def move(log,original,destination,kind):
    safe_parent(original);safe_parent(destination)
    expected=describe(original)
    if expected['kind']=='absent':raise HarnessError('Move source disappeared; preserve and reconcile')
    if describe(destination)['kind']!='absent':raise HarnessError('Move destination contains unknown content; preserve')
    entry={'from':str(original),'to':str(destination),'kind':kind,'expected':expected}
    log=Path(log);safe_parent(log)
    if log.is_symlink():raise HarnessError('Move log is a symlink')
    with log.open('a') as stream:
        stream.write(json.dumps(entry)+'\n');stream.flush();os.fsync(stream.fileno())
    os.rename(original,destination)
    return entry


def read_moves(log,root,retained,staged):
    result=[];log=Path(log);safe_parent(log)
    if log.is_symlink():raise HarnessError('Move log is a symlink')
    for line in log.read_text().splitlines() if log.exists() else []:
        entry=json.loads(line);source=Path(entry['from']);target=Path(entry['to'])
        if (not source.is_absolute() or not target.is_absolute() or '..' in source.parts or '..' in target.parts
                or root not in source.parents or not any(bound in target.parents for bound in [retained,staged])):raise HarnessError('Move log contains out-of-scope paths')
        if 'expected' not in entry:raise HarnessError('Legacy move log has no exact data identity; preserve and reconcile manually')
        result.append(entry)
    return result


def preflight_restore(entries):
    actions=[]
    for entry in reversed(entries):
        original,target=Path(entry['from']),Path(entry['to']);safe_parent(original);safe_parent(target)
        current,backup=describe(original),describe(target)
        if current==entry['expected'] and backup['kind']=='absent':continue # intent was never applied
        if current['kind']!='absent' or backup!=entry['expected']:raise HarnessError('Move restore conflict; both current and retained data preserved: '+str(original))
        actions.append((original,target))
    return actions


def apply_restore(actions):
    # The caller validated the entire set before any mutation.
    for original,target in actions:
        safe_parent(original);safe_parent(target)
        if original.exists() or original.is_symlink():raise HarnessError('Concurrent restore target appeared; preserve retained data')
        original.parent.mkdir(parents=True,exist_ok=True);os.rename(target,original)
