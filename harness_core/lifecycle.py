"""Reviewable full uninstall; local data retained independently of software removal."""
import os
import shutil
import hashlib
import re
from contextlib import ExitStack
from pathlib import Path
from . import sidecars, software, workflow, movement, processes, installations
from .common import HarnessError, lock, new_id, now, read_json, state_dir, write_json, sha, canonical, atomic
from .transaction import image, preflight_rollback, rollback, safe_parent, operation_scope


def resource_plan(root):
    root=installations.checked_root(str(root))
    if not root.is_dir():raise HarnessError('Registered installation root is missing; preserve shared registration: '+str(root))
    if not (root/'VERSION').is_file() or not (root/'bin/harness').is_file():raise HarnessError('Registered root is not an intact Harness source; recover it before uninstall')
    process_plan=processes.plan(root)
    if processes.unregistered_consumers(root,process_plan):raise HarnessError('Unregistered processes use this runtime; close the original component/session before uninstall: '+str(root))
    for journal in (root/'.harness-install/preparation-data').glob('*.json'):
        safe_parent(journal)
        if journal.is_symlink():raise HarnessError('Preparation journal is a symlink')
        if read_json(journal,{}).get('status')!='RESTORED':raise HarnessError('Previous component preparation is incomplete; recover it before uninstall')
    runtime=software.verify(root);deployment=sidecars.layout(root)
    recipes={}
    if deployment:
        sidecars.verify_image_tags(deployment)
        if sidecars.unowned_resource_consumers(root):raise HarnessError('Unowned containers use this installation image/volume; preserve all resources and close those consumers first')
        for group,files in deployment['recipes'].items():
            if group not in sidecars.GROUPS:raise HarnessError('Unknown service recipe group')
            for name,digest in files.items():
                if Path(name).name!=name:raise HarnessError('Unsafe recipe ownership path')
                path=root/'runtimes'/group/name;safe_parent(path)
                if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=digest:
                    raise HarnessError('Managed recipe changed; preserve: '+str(path))
                recipes[str(path.relative_to(root))]=image(path)
            sidecars.inspect_owned(root,group);sidecars.inspect_volumes(root,group)
    managed={**recipes,**runtime['files']}
    # Registration of a legacy root cannot retroactively own its dependencies.
    if software.inventory(root,['runtimes'])!=managed:
        raise HarnessError('Unowned runtime files or changed dependencies; preserve and reconcile: '+str(root))
    receipt=root/'.harness-install/receipt.json';safe_parent(receipt)
    if receipt.is_symlink():raise HarnessError('Source ownership receipt is a symlink')
    source=read_json(receipt,{})
    source_files={}
    if source:
        if source.get('target')!=str(root) or source.get('state')!=str(state_dir()):raise HarnessError('Source receipt differs from installation/state root')
        for name,expected in source['files'].items():
            path=root/name
            if Path(name).is_absolute() or '..' in Path(name).parts:raise HarnessError('Unsafe source ownership path')
            safe_parent(path)
            actual={'link':os.readlink(path)} if path.is_symlink() else {'sha256':sha(path.read_bytes())} if path.is_file() else None
            if actual!=expected:raise HarnessError('Managed source changed; preserve and reconcile: '+str(path))
            source_files[name]=image(path)
    caches=[]
    for name in source_files:
        path=root/name
        if path.suffix=='.py':
            for cached in (path.parent/'__pycache__').glob(path.stem+'.*.pyc'):
                safe_parent(cached)
                if re.fullmatch(re.escape(path.stem)+r'\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc',cached.name):
                    if cached.is_symlink() or not cached.is_file():raise HarnessError('Source cache is not a regular file; preserve')
                    caches.append(str(cached.relative_to(root)))
    areas=runtime['areas']
    # Canonical areas avoid moving the same retained DB twice with parent/child
    # runtime ownership records.
    areas=[a for a in areas if not any(Path(b) in Path(a).parents for b in areas)]
    return {'root':str(root),'software_files':{**source_files,**managed},'runtime_areas':areas,
            'source_cleanup':'MANAGED_ARCHIVE_FILES' if source else 'USER_SOURCE_CHECKOUT_PRESERVED','retained_source_caches':caches,
            'deployment':deployment,'component_processes':process_plan,
            'preserved_data':{'local_install_data':str(state_dir()/'retained'/sidecars.root_id(root)),
                'memory_volumes':deployment['data_namespace'] if deployment else 'NO_OWNED_SERVICE_VOLUMES',
                'journals_and_backups':str(state_dir())}}


def uninstall_plan(root):
    from . import cli
    root=Path(root);accepted=read_json(state_dir()/'accepted.json',{})
    known=installations.roots()
    if not accepted and not known:return {'status':'ALREADY_UNINSTALLED','preserved_state':str(state_dir())}
    if accepted and accepted.get('root')!=str(root):raise HarnessError('Use the accepted launcher for shared uninstall')
    if not accepted and str(root) not in known:raise HarnessError('Use a registered prepared root for shared cleanup')
    registration=read_json(state_dir()/'launcher.json',{})
    if registration and image(Path(registration['path']))!=registration['image']:raise HarnessError('Launcher changed; preserve and reconcile')
    entry=registration.get('entry_skill')
    if entry and image(Path(entry['path']))!=entry['image']:raise HarnessError('Entry skill changed; preserve and reconcile')
    projects=[];active_tasks=[]
    for name,record in cli.registry().items():
        repo=Path(name);cli.verify_owned(record)
        for events in (repo/'.harness/task').glob('*/events.jsonl'):
            state=workflow.load(events.parent)
            from .context import INACTIVE_TASK_NODES
            if state['node'] not in INACTIVE_TASK_NODES:active_tasks.append({'repo':name,'task':state['task_id'],'state':state['node']})
        projects.append({'repo':name,'preserved':str(repo/'.harness'),'action':'Detach owned overlay; retain task and memory'})
    plans=[resource_plan(Path(name)) for name in installations.roots()]
    from . import memory_services
    retained_memory=memory_services.manage('status')
    primary=next(p for p in plans if p['root']==str(root))
    preserved={**primary['preserved_data'],'project_tasks':[p['preserved'] for p in projects],
               'retained_memory_services':retained_memory,
               'installations':[{'root':p['root'],**p['preserved_data']} for p in plans]}
    return {**primary,'status':'UNINSTALL_DEFERRED' if active_tasks else 'UNINSTALL_PLANNED',
            'projects':projects,'active_tasks':active_tasks,'installations':plans,'preserved_data':preserved,
            'retained_memory_services':retained_memory,
            'confirmation':'Review this complete multi-version scope once; --apply is not authenticated human approval'}


def retained_directory(resource,identifier):
    base=Path(resource['preserved_data']['local_install_data']);safe_parent(base/'guard')
    return base if not base.exists() else base/'generations'/identifier


def data_paths(root,resource,retained):
    result={}
    def add(source,target):
        if source.exists() or source.is_symlink():result.setdefault(str(source),(source,target))
    add(root/'private',retained/'private')
    for group in sidecars.GROUPS:add(root/'runtimes'/group/'.env',retained/'provider-environments'/group/'.env')
    for name in ['machine-binding.json','services-binding.json','.harness-install']:add(root/name,retained/'installation-config'/name)
    for name in resource.get('retained_source_caches',[]):add(root/name,retained/'source-cache'/name)
    for area in resource['runtime_areas']:
        for directory,dirs,files in os.walk(root/area,followlinks=False):
            for name in list(dirs):
                original=Path(directory)/name
                if original.is_symlink():
                    if software.retained_path(original.relative_to(root)):add(original,retained/'runtime-data'/original.relative_to(root))
                    dirs.remove(name)
            for name in files:
                original=Path(directory)/name
                if software.retained_path(original.relative_to(root)):add(original,retained/'runtime-data'/original.relative_to(root))
    return list(result.values())


def clean_retained_metadata(resource,retained,identifier):
    descriptor=retained/'recovery.json'
    if descriptor.exists() and read_json(descriptor,{}).get('uninstall_id')==identifier:descriptor.unlink()
    service=retained/'services.json'
    if service.exists() and read_json(service)==resource['deployment']:service.unlink()
    for directory in sorted([p for p in retained.rglob('*') if p.is_dir() and not p.is_symlink()],key=lambda p:len(p.parts),reverse=True):
        try:directory.rmdir()
        except OSError:pass
    try:retained.rmdir()
    except OSError:pass


def read_operation_moves(record,path):
    # Legacy single-root journals remain readable; new roots have separate logs.
    resources=record['plan'].get('installations',[record['plan']]);result=[]
    for resource in resources:
        root=Path(resource['root']);key=sidecars.root_id(root)
        selected=record.get('roots',{}).get(str(root),{})
        retained=Path(selected.get('retained',str(state_dir()/'retained'/key)))
        base=state_dir()/'retained'/key
        if retained!=base and base not in retained.parents:raise HarnessError('Uninstall retained path escapes root namespace')
        staged=state_dir()/'uninstalling'/record['id']/'software'/key if record.get('roots') else state_dir()/'uninstalling'/record['id']/'software'
        log=path.with_suffix('.moves.jsonl') if root==Path(record['root']) else path.with_suffix('.'+key+'.moves.jsonl')
        result.extend(movement.read_moves(log,root,retained,staged))
    return result


def discard_owned_staging(record):
    changed=[]
    for source,selected in record['roots'].items():
        root=Path(source);staged=Path(selected['staged']);safe_parent(staged/'guard')
        resource=next(r for r in record['plan']['installations'] if r['root']==source)
        for name,expected in resource['software_files'].items():
            target=staged/name;safe_parent(target)
            if image(target)==expected:target.unlink()
            elif target.exists() or target.is_symlink():changed.append(str(target))
        for directory in sorted([p for p in staged.rglob('*') if p.is_dir() and not p.is_symlink()],key=lambda p:len(p.parts),reverse=True):
            try:directory.rmdir()
            except OSError:pass
        try:staged.rmdir()
        except OSError:pass
        if staged.exists():changed.append(str(staged))
    return changed


def _uninstall(root,apply=False):
    from . import cli,rescue
    plan=uninstall_plan(root)
    if not apply or plan['status']=='ALREADY_UNINSTALLED':return plan
    if plan['active_tasks']:raise HarnessError('Finish/cancel active or paused consumers before full uninstall; original bindings preserved')
    identifier=new_id('uninstall');path=state_dir()/'uninstalls'/f'{identifier}.json'
    record={'id':identifier,'root':str(root),'status':'STARTED','created':now(),'plan':plan,'transactions':[],
            'move_log':str(path.with_suffix('.moves.jsonl')),'roots':{}}
    for resource in plan['installations']:
        source=Path(resource['root']);retained=retained_directory(resource,identifier);key=sidecars.root_id(source)
        if retained.exists() or retained.is_symlink():raise HarnessError('Retained-data generation already exists; preserve')
        record['roots'][str(source)]={'retained':str(retained),'staged':str(state_dir()/'uninstalling'/identifier/'software'/key),'services':'UNCHANGED'}
    record['recovery_runtime']=rescue.prepare(root,identifier);write_json(path,record)
    moves=[];stopped=False
    def move(source,target,kind,owner):
        log=path.with_suffix('.moves.jsonl') if str(owner)==str(root) else path.with_suffix('.'+sidecars.root_id(owner)+'.moves.jsonl')
        entry=movement.move(log,source,target,kind);moves.append(entry)
    try:
        # Every root's ownership has already passed before the first stop/write.
        from . import memory_services
        with operation_scope(identifier):record['retained_memory_services']=memory_services.manage('remove')
        if record['retained_memory_services']:stopped=True
        for resource in plan['installations']:
            source=Path(resource['root']);selected=record['roots'][str(source)]
            selected['component_processes']=processes.stop(source)
            if resource['deployment']:
                sidecars._manage(source,'remove');stopped=True;selected['services']='STOPPED_AND_REMOVED'
                selected['removed_image_tags']=sidecars.remove_image_tags(resource['deployment'])
            else:selected['services']='NOT_PREPARED'
            write_json(path,record)
        for project in plan['projects']:
            with operation_scope(identifier):result=cli.disable(project['repo'])
            record['transactions'].append(result['transaction_id']);write_json(path,record)
        for resource in plan['installations']:
            source=Path(resource['root'])
            if processes.unregistered_consumers(source):raise HarnessError('A runtime consumer appeared during uninstall; preserve and retry after closing it')
            selected=record['roots'][str(source)];retained=Path(selected['retained']);staged=Path(selected['staged'])
            retained.mkdir(parents=True,mode=0o700)
            for original,destination in data_paths(source,resource,retained):
                safe_parent(original);safe_parent(destination);destination.parent.mkdir(parents=True,exist_ok=True)
                move(original,destination,'DATA',source)
            if resource['deployment']:write_json(retained/'services.json',resource['deployment'])
            for name,expected in resource['software_files'].items():
                original=source/name;destination=staged/name
                if image(original)!=expected:raise HarnessError('Software changed during uninstall; preserve: '+str(original))
                safe_parent(original);safe_parent(destination);destination.parent.mkdir(parents=True,exist_ok=True)
                move(original,destination,'SOFTWARE',source)
            write_json(retained/'recovery.json',{'schema_version':1,'root':str(source),'uninstall_id':identifier,'state':str(state_dir()),
                'data_namespace':resource['deployment']['data_namespace'] if resource['deployment'] else None,
                'project_tasks':plan['preserved_data']['project_tasks'],'source_cleanup':resource['source_cleanup']})
        with operation_scope(identifier):result=cli.uninstall(removed_roots=list(record['roots']),retained_paths={r:v['retained'] for r,v in record['roots'].items()})
        record['transactions'].append(result['transaction_id'])
        record.update(status='COMMITTED',finished=now(),database_restore='NOT_PERFORMED',services='STOPPED_AND_REMOVED' if stopped else 'NOT_PREPARED');write_json(path,record)
    except BaseException as error:
        record.update(status='FAILED',error=str(error));write_json(path,record)
        # All roots and config transactions preflight before restoring any file.
        actions=movement.preflight_restore(moves);virtual={};txids=child_transactions(identifier)
        for txid in reversed(txids):virtual=preflight_rollback(txid,virtual)
        movement.apply_restore(actions)
        for txid in reversed(txids):rollback(txid)
        for resource in plan['installations']:clean_retained_metadata(resource,Path(record['roots'][resource['root']]['retained']),identifier)
        record.update(status='FILES_RESTORED',services='STOPPED; rebuild/restart services separately' if stopped else 'UNCHANGED',
                      recovery_runtime_cleanup=rescue.cleanup(record['recovery_runtime']));write_json(path,record)
        raise
    record['preserved_staging_changes']=discard_owned_staging(record)
    record['recovery_runtime_cleanup']=rescue.cleanup(record['recovery_runtime']);write_json(path,record)
    directories={parent for item in moves for parent in Path(item['from']).parents if any(Path(r['root']) in parent.parents for r in plan['installations'])}
    for directory in sorted(directories,key=lambda p:len(p.parts),reverse=True):
        try:directory.rmdir()
        except OSError:pass
    retained_paths={r:v['retained'] for r,v in record['roots'].items()}
    preserved={**plan['preserved_data'],'local_install_data':retained_paths[str(root)],
               'retained_memory_services':record.get('retained_memory_services',[]),
               'installations':[{'root':r,'local_install_data':v} for r,v in retained_paths.items()]}
    return {'status':'UNINSTALLED','transaction_id':identifier,'source_cleanup':plan['source_cleanup'],'services':record['services'],
            'installations':list(record['roots']),'preserved_data':preserved,'preserved_staging_changes':record['preserved_staging_changes'],
            'recovery':'Reinstall locked software; per-root retained task/memory data remain available'}


def uninstall(root,apply=False):
    if not apply:return _uninstall(root)
    from . import cli
    with ExitStack() as stack:
        for name in installations.roots():
            for filename in ['preparation.lock','services.lock']:
                path=Path(name)/'.harness-install'/filename;safe_parent(path)
                if path.is_symlink():raise HarnessError('Lifecycle lock is a symlink')
                stack.enter_context(lock(path))
        for name in sorted(cli.registry()):
            path=Path(name)/'.harness/state/workflow.lock';safe_parent(path)
            if path.is_symlink():raise HarnessError('Workflow lock is a symlink')
            stack.enter_context(lock(path))
        return _uninstall(root,True)


def child_transactions(identifier):
    docs=[read_json(p) for p in (state_dir()/'transactions').glob('*/journal.json')]
    return [d['id'] for d in sorted(docs,key=lambda d:d.get('started','')) if d.get('parent_operation')==identifier]


def _recover_uninstall(identifier,apply=False):
    from .common import safe_id
    identifier=safe_id(identifier);path=state_dir()/'uninstalls'/f'{identifier}.json';safe_parent(path)
    if path.is_symlink():raise HarnessError('Uninstall journal is a symlink')
    doc=read_json(path)
    if not doc:raise HarnessError('Unknown uninstall operation')
    if doc['status'] in {'COMMITTED','FILES_RESTORED'} and apply and doc.get('recovery_runtime'):
        from . import rescue
        doc['recovery_runtime_cleanup']=rescue.cleanup(doc['recovery_runtime']);write_json(path,doc)
    if doc['status']=='COMMITTED':
        preserved={**doc['plan']['preserved_data'],'retained_memory_services':doc.get('retained_memory_services',[])}
        if doc.get('roots'):
            preserved={**preserved,'local_install_data':doc['roots'][doc['root']]['retained'],
                       'installations':[{'root':r,'local_install_data':v['retained']} for r,v in doc['roots'].items()]}
        return {'status':'UNINSTALL_COMMITTED','operation_id':identifier,'preserved_data':preserved}
    if doc['status']=='FILES_RESTORED':return {'status':'ALREADY_RESTORED','operation_id':identifier}
    entries=read_operation_moves(doc,path)
    actions=movement.preflight_restore(entries)
    txids=child_transactions(identifier);virtual={}
    for txid in reversed(txids):virtual=preflight_rollback(txid,virtual)
    result={'status':'UNINSTALL_RECOVERY_PLANNED','operation_id':identifier,'restore_moves':len(actions),'transactions':txids,
            'services':'Stopped services/images are not restored by file recovery; rebuild/restart them explicitly'}
    if not apply:return result
    movement.apply_restore(actions)
    for txid in reversed(txids):rollback(txid)
    for resource in doc['plan'].get('installations',[doc['plan']]):
        selected=doc.get('roots',{}).get(resource['root'],{})
        retained=Path(selected.get('retained',str(state_dir()/'retained'/sidecars.root_id(Path(resource['root'])))))
        clean_retained_metadata(resource,retained,identifier)
    from . import rescue
    cleanup=rescue.cleanup(doc['recovery_runtime']) if doc.get('recovery_runtime') else 'LEGACY_NO_RUNTIME'
    doc.update(status='FILES_RESTORED',recovered=now(),services=result['services'],recovery_runtime_cleanup=cleanup);write_json(path,doc)
    return {**result,'status':'FILES_RESTORED'}


def recover_uninstall(identifier,apply=False):
    if not apply:return _recover_uninstall(identifier)
    from . import cli
    from .common import safe_id
    doc=read_json(state_dir()/'uninstalls'/f'{safe_id(identifier)}.json',{})
    if not doc:raise HarnessError('Unknown uninstall operation')
    with ExitStack() as stack:
        for resource in sorted(doc['plan'].get('installations',[doc['plan']]),key=lambda p:p['root']):
            for filename in ['preparation.lock','services.lock']:
                path=Path(resource['root'])/'.harness-install'/filename;safe_parent(path)
                if path.is_symlink():raise HarnessError('Recovery lock is a symlink')
                stack.enter_context(lock(path))
        names=set(cli.registry())|{p['repo'] for p in doc['plan']['projects']}
        for name in sorted(names):
            path=Path(name)/'.harness/state/workflow.lock';safe_parent(path)
            if path.is_symlink():raise HarnessError('Recovery workflow lock is a symlink')
            stack.enter_context(lock(path))
        return _recover_uninstall(identifier,True)
