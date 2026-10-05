"""Explicit test resource registration and conservative container/network collection."""
import os
from contextlib import ExitStack
from pathlib import Path
from . import installations, processes, sidecars, software, resource_manager, resource_systemd
from .common import HarnessError, canonical, lock, read_json, safe_id, sha, state_dir, now
from .transaction import Transaction, image, safe_parent


def paths(test_id):
    base=state_dir()/'resource-tests';path=base/(safe_id(test_id)+'.json')
    safe_parent(path)
    return path,path.with_suffix('.ownership.json')


def load(test_id):
    path,receipt=paths(test_id)
    if path.is_symlink() or receipt.is_symlink():raise HarnessError('Test registration symlink; preserve')
    doc=read_json(path,{})
    if not doc or read_json(receipt,{}).get('image')!=image(path):raise HarnessError('Test registration ownership changed')
    if doc.get('test_id')!=test_id or doc.get('state_root')!=str(state_dir()):raise HarnessError('Test registration identity mismatch')
    return doc


def save(doc):
    path,receipt=paths(doc['test_id'])
    with Transaction('Record owned test resources') as tx:
        tx.change(path,canonical(doc)+b'\n',mode=0o600)
        tx.change(receipt,canonical({'image':image(path)})+b'\n',mode=0o600)


def register(root,test_id):
    """Test harness explicitly opts in after owned resources are prepared."""
    root=installations.checked_root(str(Path(root).resolve()))
    path,receipt=paths(test_id)
    if path.exists() or receipt.exists():raise HarnessError('Test ID already registered')
    layout=sidecars.layout(root)
    if not layout:raise HarnessError('Only prepared owned test deployments can register')
    for group in layout['recipes']:
        sidecars.manage(root,'status',group)
        sidecars.inspect_volumes(root,group)
    doc={'schema_version':1,'test_id':test_id,'root':str(root),'state_root':str(state_dir()),
         'owner_pid':os.getpid(),'owner_identity':software.process_identity(os.getpid()),
         'layout_hash':sha(canonical(layout)),'status':'ACTIVE','created':now()}
    save(doc)
    return doc


def finish(test_id):
    doc=load(test_id)
    if doc['owner_pid']!=os.getpid() or doc['owner_identity']!=software.process_identity(os.getpid()):
        raise HarnessError('Only the registered test process may finish this run')
    doc.update(status='FINISHED',finished=now());save(doc)


def inspect(doc):
    root=installations.checked_root(doc['root'])
    if doc['status']=='COLLECTED':return 'ALREADY_COLLECTED'
    if doc['status']=='ACTIVE' and software.process_identity(doc['owner_pid'])==doc['owner_identity']:
        return 'OWNER_RUNNING'
    if doc['status'] not in {'ACTIVE','FINISHED'}:raise HarnessError('Unknown test state')
    if sha(canonical(sidecars.layout(root)))!=doc['layout_hash']:raise HarnessError('Test deployment changed')
    if processes.plan(root)['active_scopes'] or processes.unregistered_consumers(root):return 'CONSUMERS_RUNNING'
    software.require_preparation_idle(root)
    if sidecars.unowned_resource_consumers(root):return 'FOREIGN_CONSUMER'
    # Project bindings may belong to other tasks; detachment must be explicit.
    for name in read_json(state_dir()/'projects.json',{}):
        if read_json(Path(name)/'.harness/state/project.json',{}).get('harness_root')==str(root):
            return 'PROJECT_STILL_BOUND'
    resource_systemd.verify(root)
    for group in sidecars.layout(root)['recipes']:
        sidecars.manage(root,'status',group);sidecars.inspect_volumes(root,group)
    return 'COLLECTIBLE'


def collect(apply=False):
    base=state_dir()/'resource-tests'
    if not base.exists():return {'status':'TEST_GC_PREVIEW','resources':[],'changes':'NONE'}
    safe_parent(base/'guard')
    entries=[]
    for path in sorted(base.glob('*.json')):
        if path.name.endswith('.ownership.json'):continue
        doc=load(path.stem)
        try:state=inspect(doc)
        except HarnessError as error:state='CONFLICT';doc={**doc,'reason':str(error)}
        entries.append({'test_id':doc['test_id'],'root':doc['root'],'state':state,**({'reason':doc['reason']} if 'reason' in doc else {})})
    if not apply:return {'status':'TEST_GC_PREVIEW','resources':entries,'changes':'NONE'}
    results=[]
    with lock(state_dir()/'operation.lock'):
        for entry in entries:
            if entry['state']!='COLLECTIBLE':continue
            root=Path(entry['root'])
            with ExitStack() as stack:
                for name in ['preparation.lock','services.lock']:stack.enter_context(lock(root/'.harness-install'/name))
                doc=load(entry['test_id'])
                if inspect(doc)!='COLLECTIBLE':raise HarnessError('Test resource state changed before collection')
                registered=resource_systemd.verify(root)
                if registered:
                    if resource_systemd.status(root)['status']=='RUNNING':resource_manager.quiesce(root)
                    resource_systemd.remove(root)
                sidecars._manage(root,'remove')
                doc.update(status='COLLECTED',collected=now());save(doc)
                results.append(entry['test_id'])
    return {'status':'TEST_GC_COMPLETE','collected':results,'resources':entries,
            'preserved':'Volumes, source, logs and evidence; unregistered resources untouched'}
