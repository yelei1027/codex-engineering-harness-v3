"""Transactional installation-root catalogue; registration is not resource ownership."""
from pathlib import Path
from .common import HarnessError,canonical,now,read_json,state_dir
from .transaction import safe_parent,image


def path():return state_dir()/'installations.json'


def checked_root(value):
    root=Path(value)
    if not root.is_absolute() or '..' in root.parts or root in {Path(root.anchor),Path.home()}:
        raise HarnessError('Invalid installation root; preserve and reconcile')
    safe_parent(root/'guard')
    if root==state_dir() or root in state_dir().parents:
        receipt=root/'.harness-install/receipt.json';safe_parent(receipt)
        if receipt.is_symlink():raise HarnessError('Candidate source receipt is a symlink')
        source=read_json(receipt,{})
        isolated=(source.get('origin')=='COPIED_UPDATE_CANDIDATE' and source.get('target')==str(root)
                  and state_dir() in {root/'candidate-private',root/'candidate-private/state'} and Path(source.get('state','')).is_absolute()
                  and Path(source['state'])!=state_dir())
        if not isolated:raise HarnessError('Installation root contains shared state; preserve and reconcile')
    return root


def load():
    target=path();safe_parent(target)
    if target.is_symlink():raise HarnessError('Installation catalogue is a symlink')
    receipt=target.with_name('installations-ownership.json');safe_parent(receipt)
    if receipt.is_symlink():raise HarnessError('Installation ownership receipt is a symlink')
    if target.exists()!=receipt.exists():raise HarnessError('Installation catalogue/ownership pair is incomplete; recover before maintenance')
    if target.exists():
        if not target.is_file() or not receipt.is_file():raise HarnessError('Installation catalogue/ownership is not a regular file')
        if receipt.is_symlink() or read_json(receipt,{}).get('image')!=image(target):raise HarnessError('Installation catalogue is unknown or changed; preserve and reconcile')
    doc=read_json(target,{'schema_version':1,'roots':{}})
    if doc.get('schema_version')!=1 or not isinstance(doc.get('roots'),dict):raise HarnessError('Unknown installation catalogue schema')
    for name,record in doc['roots'].items():
        checked_root(name)
        if record.get('root')!=name or record.get('status') not in {'INSTALLED','REMOVED'}:
            raise HarnessError('Installation catalogue identity differs; preserve')
    return doc


def register(tx,wanted,previous=None):
    doc=load();root=str(checked_root(wanted['root']))
    if previous and previous['root']!=root:
        old=str(checked_root(previous['root']))
        if old not in doc['roots']:doc['roots'][old]={**previous,'status':'INSTALLED','registered':now(),'origin':'PRE_CATALOG_ACCEPTED_REGISTRATION','role':'ACCEPTED'}
    for name,record in doc['roots'].items():
        if record['status']=='INSTALLED' and record.get('role')=='ACCEPTED':record['role']='RETAINED_VERSION'
    prior=doc['roots'].get(root,{})
    doc['roots'][root]={**prior,**wanted,'status':'INSTALLED','role':'ACCEPTED','registered':prior.get('registered',now()),
                        'origin':prior.get('origin','APPROVED_INSTALL_OR_PROMOTION')}
    tx.change(path(),canonical(doc)+b'\n',mode=0o600)
    tx.change(path().with_name('installations-ownership.json'),canonical({'image':image(path()),'transaction_id':tx.id})+b'\n',mode=0o600)


def roots():
    from . import cli
    result={name for name,record in load()['roots'].items() if record['status']=='INSTALLED'}
    accepted=read_json(state_dir()/'accepted.json',{})
    if accepted:result.add(str(checked_root(accepted['root'])))
    for name,record in cli.registry().items():
        cli.verify_owned(record)
        target=Path(name)/'.harness/state/project.json';safe_parent(target)
        if target.is_symlink():raise HarnessError('Project installation binding is a symlink')
        binding=read_json(target,{})
        if not binding.get('harness_root'):raise HarnessError('Enabled project has no installation binding')
        result.add(str(checked_root(binding['harness_root'])))
    selected=sorted(result)
    for index,name in enumerate(selected):
        checked_root(name)
        if any(Path(name) in Path(other).parents or Path(other) in Path(name).parents for other in selected[index+1:]):
            raise HarnessError('Installation roots overlap; preserve and reconcile before shared uninstall')
    return selected


def remove(tx,names,retained_paths=None):
    doc=load()
    for name in names:
        record=doc['roots'].get(name,{'root':name,'origin':'PRE_CATALOG_REGISTRATION'})
        if retained_paths and name in retained_paths:record['retained_data_path']=retained_paths[name]
        record.update(status='REMOVED',role='DATA_RETAINED',removed=now());doc['roots'][name]=record
    tx.change(path(),canonical(doc)+b'\n',mode=0o600)
    tx.change(path().with_name('installations-ownership.json'),canonical({'image':image(path()),'transaction_id':tx.id})+b'\n',mode=0o600)


def register_candidate(tx,wanted):
    doc=load();root=str(checked_root(wanted['root']))
    if root in doc['roots']:raise HarnessError('Candidate root already registered; preserve')
    doc['roots'][root]={**wanted,'status':'INSTALLED','role':'CANDIDATE','registered':now(),'origin':'COPIED_UPDATE_CANDIDATE'}
    tx.change(path(),canonical(doc)+b'\n',mode=0o600)
    tx.change(path().with_name('installations-ownership.json'),canonical({'image':image(path()),'transaction_id':tx.id})+b'\n',mode=0o600)


def register_preparing(tx,wanted):
    doc=load();root=str(checked_root(wanted['root']));prior=doc['roots'].get(root,{})
    if prior.get('status')=='INSTALLED' and prior.get('release_hash')!=wanted['release_hash']:
        raise HarnessError('Registered source changed; use an isolated candidate before dependency preparation')
    role=prior.get('role') if prior.get('status')=='INSTALLED' else 'PREPARING'
    doc['roots'][root]={**prior,**wanted,'status':'INSTALLED','role':role or 'PREPARING',
                       'registered':prior.get('registered',now()),'origin':prior.get('origin','APPROVED_COMPONENT_PREPARATION')}
    tx.change(path(),canonical(doc)+b'\n',mode=0o600)
    tx.change(path().with_name('installations-ownership.json'),canonical({'image':image(path()),'transaction_id':tx.id})+b'\n',mode=0o600)
