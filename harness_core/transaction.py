import os
import stat
from pathlib import Path
from .common import HarnessError, atomic, canonical, new_id, now, read_json, sha, state_dir, write_json

def image(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return {'kind': 'absent'}
    st = path.lstat()
    base = {'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid}
    if path.is_symlink():
        return {**base, 'kind': 'symlink', 'target': os.readlink(path)}
    if not path.is_file():
        raise HarnessError(f'Refusing a non-file transaction target: {path}')
    return {**base, 'kind': 'file', 'sha256': sha(path.read_bytes()), 'size': st.st_size}

def safe_parent(path):
    for parent in Path(path).parents:
        if parent.is_symlink():
            raise HarnessError(f'Refusing a symlink parent: {parent}')

class Transaction:
    """Journal intent before writes; preserve bytes, mode, UID/GID and conflicts."""
    def __init__(self, reason):
        self.id = new_id('HARNESS-V3')
        self.path = state_dir()/'transactions'/self.id/'journal.json'
        self.backup = state_dir()/'backups'/self.id
        self.doc = {'id': self.id, 'reason': reason, 'started': now(), 'status': 'OPEN', 'touch_set': []}
        self.save()

    def save(self):
        write_json(self.path, self.doc)

    def change(self, path, data=None, mode=0o644, link=None, delete=False):
        path = Path(path).absolute()
        safe_parent(path)
        before = image(path)
        if delete:
            after = {'kind': 'absent'}
        elif link is not None:
            after = {'kind':'symlink', 'target':str(link), 'mode':0o777, 'uid':os.getuid(), 'gid':os.getgid()}
        else:
            after = {'kind':'file', 'sha256':sha(data), 'size':len(data), 'mode':mode,
                     'uid':before.get('uid', os.getuid()), 'gid':before.get('gid', os.getgid())}
        if before == after:
            return
        backup = None
        if before['kind'] == 'file':
            backup = str(self.backup/f'{len(self.doc["touch_set"]):05d}.bin')
            atomic(Path(backup), path.read_bytes(), 0o600)
        entry = {'path':str(path), 'before':before, 'after':after, 'backup':backup, 'applied':False}
        self.doc['touch_set'].append(entry)
        self.save()
        path.parent.mkdir(parents=True, exist_ok=True)
        if link is not None:
            if path.exists() or path.is_symlink():
                path.unlink()
            path.symlink_to(link)
        elif delete:
            if path.exists() or path.is_symlink():
                path.unlink()
        else:
            atomic(path, data, mode)
            if before['kind'] == 'file' and (before['uid'], before['gid']) != (os.getuid(), os.getgid()):
                os.chown(path, before['uid'], before['gid'])
        entry['applied'] = True
        self.save()

    def finish(self):
        self.doc.update(status='COMMITTED', finished=now())
        self.save()
        return self.id

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        if typ is None:
            self.finish()
        else:
            self.doc.update(status='FAILED', error=str(value), finished=now())
            self.save()
            rollback(self.id)

def rollback(txid):
    from .common import safe_id
    path = state_dir()/'transactions'/safe_id(txid)/'journal.json'
    doc = read_json(path)
    if not doc:
        raise HarnessError(f'Unknown transaction: {txid}')
    if doc['status'] == 'ROLLED_BACK':
        return {'transaction_id':txid, 'status':'ALREADY_ROLLED_BACK'}
    # First validate the complete set; do not partially roll back before discovering conflicts.
    entries = []
    virtual = {}
    for e in reversed(doc['touch_set']):
        current = virtual.get(e['path'], image(Path(e['path'])))
        if not e['applied'] and current == e['before']:
            continue
        if current != e['after']:
            raise HarnessError(f'Rollback conflict; preserve later/user changes: {e["path"]}')
        safe_parent(e['path'])
        if e['before']['kind'] == 'file' and sha(Path(e['backup']).read_bytes()) != e['before']['sha256']:
            raise HarnessError(f'Backup hash mismatch: {e["path"]}')
        entries.append(e)
        virtual[e['path']] = e['before']
    for e in entries:
        target, before = Path(e['path']), e['before']
        if target.exists() or target.is_symlink():
            target.unlink()
        if before['kind'] == 'file':
            atomic(target, Path(e['backup']).read_bytes(), before['mode'])
            if (before['uid'],before['gid']) != (os.getuid(),os.getgid()):
                os.chown(target,before['uid'],before['gid'])
        elif before['kind'] == 'symlink':
            target.symlink_to(before['target'])
    doc.update(status='ROLLED_BACK', rolled_back=now())
    write_json(path, doc)
    return {'transaction_id':txid, 'status':'ROLLED_BACK', 'files':len(entries), 'database_restore':'NOT_PERFORMED'}
