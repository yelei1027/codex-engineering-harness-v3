"""Recover retained memory volumes with current software, preserving exact bindings."""
import json
import os
import socket
from pathlib import Path
from contextlib import ExitStack

from . import installations, recovery, sidecars
from .common import HarnessError, canonical, lock, safe_id, sha, state_dir
from .transaction import Transaction, image, safe_parent


def directory(origin):
    return state_dir()/'memory-services'/sidecars.root_id(origin)


def load(origin):
    base=directory(origin);path=base/'deployment.json';owner=base/'ownership.json'
    doc=recovery.regular_json(path,{})
    receipt=recovery.regular_json(owner,{})
    if bool(doc)!=bool(receipt) or (doc and receipt.get('image')!=image(path)):
        raise HarnessError('Retained memory deployment ownership changed; preserve')
    if doc and (doc.get('origin_root')!=str(origin) or doc.get('state')!=str(state_dir())
                or doc.get('status') not in {'PREPARED','REMOVED'}):
        raise HarnessError('Retained memory deployment identity differs; preserve')
    return doc


def save(tx,doc):
    base=directory(doc['origin_root']);path=base/'deployment.json'
    tx.change(path,canonical(doc)+b'\n',mode=0o600)
    tx.change(base/'ownership.json',canonical({'image':image(path),'transaction_id':tx.id})+b'\n',mode=0o600)


def deployments():
    result=[];base=state_dir()/'memory-services';safe_parent(base/'guard')
    if not base.exists():return result
    for folder in sorted(base.iterdir()):
        if folder.is_symlink() or not folder.is_dir():raise HarnessError('Unknown retained memory deployment path; preserve')
        doc=recovery.regular_json(folder/'deployment.json',{})
        if not doc or folder!=directory(doc.get('origin_root','')):
            raise HarnessError('Unknown retained memory deployment directory; preserve')
        result.append(load(doc['origin_root']))
    return result


def source(origin):
    origin=installations.checked_root(str(origin));catalogue=installations.load()['roots']
    record=catalogue.get(str(origin),{})
    if record.get('status')!='REMOVED':raise HarnessError('Memory recovery requires a removed registered installation')
    retained=recovery.locate(origin);descriptor=recovery.regular_json(retained/'recovery.json',{})
    if descriptor.get('root')!=str(origin) or descriptor.get('state')!=str(state_dir()):
        raise HarnessError('Retained memory belongs to another root/state; preserve')
    uninstall_id=safe_id(descriptor['uninstall_id'])
    uninstall=recovery.regular_json(state_dir()/'uninstalls'/(uninstall_id+'.json'),{})
    if uninstall.get('status')!='COMMITTED':raise HarnessError('Retained memory uninstall is incomplete; reconcile first')
    if uninstall.get('roots',{}).get(str(origin),{}).get('retained')!=str(retained):
        raise HarnessError('Retained memory generation differs from committed uninstall')
    old=recovery.regular_json(retained/'services.json',{})
    if not old or 'powercontext' not in old.get('recipes',{}):return None
    expected=next((r.get('deployment') for r in uninstall['plan']['installations'] if r['root']==str(origin)),None)
    if old!=expected or old.get('data_namespace')!=descriptor.get('data_namespace'):
        raise HarnessError('Retained memory service differs from the committed resource plan')
    if old.get('state_id')!=sha(str(state_dir()).encode())[:16]:raise HarnessError('Retained volume state ownership differs')
    return origin,retained,old


def env(doc):
    return {**os.environ,'HARNESS_SERVICE_NAMESPACE':doc['namespace'],
            'HARNESS_VOLUME_NAMESPACE':doc['data_namespace'],'HARNESS_STATE_ID':doc['state_id'],
            'POWERCONTEXT_PORT':str(doc['port']),'HARNESS_POWERCONTEXT_IMAGE':doc['image_id']}


def inspect(doc):
    base=directory(doc['origin_root']);compose=base/'compose.yaml';safe_parent(compose)
    if image(compose)!=doc['compose_image']:raise HarnessError('Recovered memory recipe changed; preserve')
    provider=base/'.env';safe_parent(provider)
    if provider.is_symlink() or not provider.is_file():raise HarnessError('Recovered provider environment is missing/unsafe; preserve')
    allowed={'compose.yaml','.env','deployment.json','ownership.json'}
    if any(p.name not in allowed for p in base.iterdir()):raise HarnessError('Unknown recovered memory files; preserve')
    return resources(doc)


def resources(doc,require_absent=False):
    compose=directory(doc['origin_root'])/'compose.yaml'
    volume=doc['data_namespace']+'-powercontext-data'
    raw=sidecars.docker(['volume','inspect',volume],allow_missing=True)
    if raw is None:raise HarnessError('Retained memory volume is missing; do not create an empty replacement')
    labels=json.loads(raw)[0].get('Labels') or {}
    if labels.get('com.harness.state_id')!=doc['state_id'] or labels.get('com.harness.data_namespace')!=doc['data_namespace']:
        raise HarnessError('Retained memory volume ownership changed; preserve')
    name=doc['namespace']+'-powercontext';raw=sidecars.docker(['inspect',name],allow_missing=True)
    container=None
    if raw is not None:
        if require_absent:raise HarnessError('Unowned container occupies recovered memory deployment; preserve')
        container=json.loads(raw)[0];labels=container.get('Config',{}).get('Labels',{})
        if (container.get('Image')!=doc['image_id'] or labels.get('com.docker.compose.project')!=doc['namespace']+'-powercontext'
                or labels.get('com.docker.compose.project.config_files')!=str(compose)):
            raise HarnessError('Recovered memory container ownership changed; preserve')
        mounts=container.get('Mounts',[])
        if not any(m.get('Name')==volume and m.get('Destination')=='/data' for m in mounts):
            raise HarnessError('Recovered memory container uses another database; preserve')
        expected_ports=[{'HostIp':'127.0.0.1','HostPort':str(doc['port'])}]
        configured=container.get('HostConfig',{}).get('PortBindings',{}).get('8000/tcp') or []
        if configured!=expected_ports:
            raise HarnessError('Recovered memory endpoint differs; preserve')
        if container.get('State',{}).get('Status')=='running':
            ports=(container.get('NetworkSettings',{}).get('Ports') or {}).get('8000/tcp') or []
            if ports!=expected_ports:raise HarnessError('Running recovered memory endpoint differs; preserve')
    ids=sidecars.docker(['ps','-aq']).split()
    if ids:
        lines=sidecars.docker(['inspect','--format','{{json .Name}} {{json .Mounts}}',*ids]).splitlines()
        decoder=json.JSONDecoder()
        for line in lines:
            name_value,end=decoder.raw_decode(line);mounts=json.loads(line[end:].strip())
            if name_value.removeprefix('/')!=name and any(m.get('Name')==volume for m in mounts):
                raise HarnessError('Unknown consumer uses retained memory volume; close it before maintenance')
    return container


def compose(doc,action):
    return sidecars.docker(['compose','-p',doc['namespace']+'-powercontext','-f',str(directory(doc['origin_root'])/'compose.yaml'),*action],env(doc))


def binding(origin,url,namespace=None):
    doc=load(origin)
    if not doc or doc['status']!='PREPARED':raise HarnessError('Original memory service is not recovered; local data preserved')
    if doc['url']!=url or (namespace and namespace!=doc['data_namespace']):raise HarnessError('Recovered memory binding differs; preserve')
    original=source(origin)
    verify_origin(doc,original)
    container=inspect(doc)
    if not container or container.get('State',{}).get('Status')!='running':
        raise HarnessError('Recovered memory container is not running; preserve binding and start its owned service')
    return doc


def verify_origin(doc,original):
    if not original:raise HarnessError('Recovered memory has no committed original deployment')
    root,retained,old=original
    descriptor=recovery.regular_json(retained/'recovery.json',{})
    if (doc['data_namespace']!=old['data_namespace'] or doc['state_id']!=old['state_id']
            or doc['retained_path']!=str(retained) or doc['uninstall_id']!=descriptor.get('uninstall_id')
            or doc['image_id']!=old['image_ids']['powercontext']
            or doc['port']!=old['ports']['powercontext'] or doc['url']!='http://127.0.0.1:'+str(doc['port'])
            or doc['namespace']!='codex-harness-memory-'+sidecars.root_id(root)):
        raise HarnessError('Recovered memory provenance differs from the original resource plan; preserve')


def free_port(port):
    with socket.socket() as sock:
        # Docker's TCP listener permits reuse after close. TIME_WAIT is not a
        # live owner; listen still rejects an existing listener without REUSEPORT.
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:sock.bind(('127.0.0.1',port));sock.listen(1)
        except OSError as error:raise HarnessError('Original memory port '+str(port)+' is occupied; preserve original URL and reconcile') from error


def plan(root,origin=None):
    root=Path(root);current=sidecars.layout(root)
    records=installations.load()['roots']
    origins=[str(origin)] if origin else [name for name,r in records.items() if r['status']=='REMOVED']
    result=[]
    for name in origins:
        original=source(name)
        if original is None:continue
        oldroot,retained,old=original;previous=load(oldroot)
        if current and current['data_namespace']==old['data_namespace']:
            raise HarnessError('Retained volume is already attached to the current installation; use its service')
        if not current or 'powercontext' not in current['recipes']:raise HarnessError('Prepare the current locked PowerContext service before memory recovery')
        if old['images']['powercontext'].rsplit(':',1)[-1]!=current['images']['powercontext'].rsplit(':',1)[-1]:
            raise HarnessError('PowerContext version changed; database upgrade requires a separately reviewed migration')
        if not old.get('image_ids',{}).get('powercontext') or old['image_ids']['powercontext']!=current.get('image_ids',{}).get('powercontext'):
            raise HarnessError('PowerContext runtime image changed; schema compatibility is unverified, preserve memory and review migration')
        sidecars.verify_image_tags(current)
        recipe=root/'runtimes/powercontext/compose.yaml';safe_parent(recipe)
        expected=current['recipes']['powercontext'].get('compose.yaml')
        if recipe.is_symlink() or not recipe.is_file() or sha(recipe.read_bytes())!=expected:raise HarnessError('Current PowerContext recipe is not verified')
        base=directory(oldroot);provider=retained/'provider-environments/powercontext/.env'
        if previous and previous['status']=='PREPARED':
            verify_origin(previous,original)
            container=inspect(previous)
            if not container or container.get('State',{}).get('Status')!='running':free_port(previous['port'])
            result.append(previous);continue
        if previous:
            verify_origin(previous,original)
            if (base/'compose.yaml').exists() or (base/'compose.yaml').is_symlink():raise HarnessError('Removed memory recipe was replaced; preserve')
            if any(p.name not in {'deployment.json','ownership.json','.env'} for p in base.iterdir()):
                raise HarnessError('Unknown removed memory files; preserve')
            provider=base/'.env'
        elif base.exists():raise HarnessError('Unowned memory recovery destination; preserve')
        safe_parent(provider)
        if provider.is_symlink() or not provider.is_file():raise HarnessError('Retained provider environment is missing; preserve')
        port=old['ports']['powercontext']
        free_port(port)
        volume=old['data_namespace']+'-powercontext-data';raw=sidecars.docker(['volume','inspect',volume],allow_missing=True)
        if raw is None:raise HarnessError('Retained memory volume is missing; preserve binding')
        labels=json.loads(raw)[0].get('Labels') or {}
        if labels.get('com.harness.state_id')!=old['state_id'] or labels.get('com.harness.data_namespace')!=old['data_namespace']:
            raise HarnessError('Retained memory volume is not owned; preserve')
        doc={'origin_root':str(oldroot),'state':str(state_dir()),'namespace':'codex-harness-memory-'+sidecars.root_id(oldroot),
             'retained_path':str(retained),'uninstall_id':recovery.regular_json(retained/'recovery.json')['uninstall_id'],
             'data_namespace':old['data_namespace'],'state_id':old['state_id'],'port':port,'url':'http://127.0.0.1:'+str(port),
             'image_id':current['image_ids']['powercontext'],'owner_root':str(root),'status':'PREPARED',
             'compose_image':{**image(recipe),'mode':0o600},'provider_source':str(provider)}
        resources(doc,require_absent=True)
        result.append(doc)
    if len({d['data_namespace'] for d in result})!=len(result):raise HarnessError('Duplicate retained memory volumes; reconcile before recovery')
    return result


def _restore(root,apply=False,origin=None):
    planned=plan(root,origin)
    result={'status':'MEMORY_RECOVERY_PLANNED','services':[{'origin_root':d['origin_root'],'data_namespace':d['data_namespace'],'url':d['url']} for d in planned],
            'database_migration':'NONE; original volumes, Scope and Revision preserved','project_bindings':'UNCHANGED'}
    if not apply:return result
    for doc in planned:
        previous=load(doc['origin_root']);base=directory(doc['origin_root'])
        if not previous or previous['status']=='REMOVED':
            with Transaction('Prepare retained memory deployment') as tx:
                tx.change(base/'compose.yaml',(Path(root)/'runtimes/powercontext/compose.yaml').read_bytes(),mode=0o600)
                if not previous:tx.change(base/'.env',Path(doc['provider_source']).read_bytes(),mode=0o600)
                save(tx,doc)
        inspect(doc);compose(doc,['up','-d','--no-build']);inspect(doc)
    return {**result,'status':'MEMORY_SERVICES_STARTED','readiness':'VERIFY_ON_CALL; each binding checks its original Scope'}


def restore(root,apply=False,origin=None):
    if not apply:return _restore(root,origin=origin)
    with ExitStack() as locks:
        for doc in plan(root,origin):locks.enter_context(lock(state_dir()/'data-locks'/doc['data_namespace']/'maintenance.lock'))
        return _restore(root,True,origin)


def start_preflight():
    docs=[d for d in deployments() if d['status']=='PREPARED']
    for doc in docs:
        verify_origin(doc,source(doc['origin_root']));container=inspect(doc)
        if not container or container.get('State',{}).get('Status')!='running':free_port(doc['port'])
    return docs


def _manage(action):
    if action=='start':docs=start_preflight()
    else:
        docs=[d for d in deployments() if d['status']=='PREPARED']
        for doc in docs:verify_origin(doc,source(doc['origin_root']));inspect(doc)
    result=[]
    for doc in docs:
        if action=='start':compose(doc,['up','-d','--no-build'])
        elif action=='stop':compose(doc,['stop'])
        elif action=='remove':
            compose(doc,['down'])
            with Transaction('Remove retained memory service program; retain data') as tx:
                tx.change(directory(doc['origin_root'])/'compose.yaml',delete=True)
                save(tx,{**doc,'status':'REMOVED'})
        elif action!='status':raise HarnessError('Unknown retained memory service action')
        container=None if action=='remove' else inspect(doc)
        result.append({'origin_root':doc['origin_root'],'data_namespace':doc['data_namespace'],'action':action,
                       'state':'REMOVED' if action=='remove' else container.get('State',{}).get('Status') if container else 'MISSING',
                       'container':doc['namespace']+'-powercontext','url':doc['url'],
                       'program':str(directory(doc['origin_root'])/'compose.yaml'),
                       'provider_environment':str(directory(doc['origin_root'])/'.env'),
                       'memory_volume':doc['data_namespace']+'-powercontext-data'})
    return result


def manage(action):
    if action=='status':return _manage(action)
    with ExitStack() as locks:
        for doc in deployments():
            if doc['status']=='PREPARED':locks.enter_context(lock(state_dir()/'data-locks'/doc['data_namespace']/'maintenance.lock'))
        return _manage(action)
