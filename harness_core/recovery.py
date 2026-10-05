"""Durable local-data recovery; no old software, caches or native trust restored."""
import os
from pathlib import Path
from contextlib import ExitStack
from .common import HarnessError,json_bytes,lock,new_id,read_json,safe_id,sha,state_dir,write_json
from .transaction import image,safe_parent
from .sidecars import root_id
from . import movement


def regular_json(path,default=None):
    path=Path(path);safe_parent(path)
    if path.is_symlink():raise HarnessError('Recovery metadata is a symlink; preserve')
    if path.exists() and not path.is_file():raise HarnessError('Recovery metadata is not a regular file')
    return read_json(path,default)


def locate(root):
    from . import installations
    base=state_dir()/'retained'/root_id(root)
    selected=installations.load()['roots'].get(str(root),{}).get('retained_data_path')
    retained=Path(selected) if selected else base
    if '..' in retained.parts or (retained!=base and base not in retained.parents):raise HarnessError('Retained data path escapes installation namespace')
    safe_parent(retained/'recovery.json');return retained


def planned_moves(root,retained):
    result=[]
    if (retained/'private').exists() or (retained/'private').is_symlink():result.append((retained/'private',root/'private'))
    for group in ['powercontext','screenshot-to-code']:
        source=retained/'provider-environments'/group/'.env'
        if source.exists() or source.is_symlink():result.append((source,root/'runtimes'/group/'.env'))
    data=retained/'runtime-data'
    if data.exists():
        safe_parent(data/'guard')
        for directory,dirs,files in os.walk(data,followlinks=False):
            for name in list(dirs):
                path=Path(directory)/name
                if path.is_symlink():files.append(name);dirs.remove(name)
            for name in files:
                source=Path(directory)/name;result.append((source,root/source.relative_to(data)))
    if len({str(target) for source,target in result})!=len(result):raise HarnessError('Retained data has duplicate destinations; preserve all copies')
    return result


def validate_record(doc,root,retained,uninstall):
    if doc.get('root')!=str(root) or doc.get('retained')!=str(retained) or doc.get('uninstall_id')!=uninstall:
        raise HarnessError('Pending recovery belongs to another root or data generation')
    destinations=set()
    for entry in doc['moves']:
        source,target=Path(entry['from']),Path(entry['to'])
        if (not source.is_absolute() or not target.is_absolute() or '..' in source.parts or '..' in target.parts
                or retained not in source.parents or root not in target.parents or str(target) in destinations):
            raise HarnessError('Recovery journal contains invalid or duplicate paths')
        safe_parent(source);safe_parent(target);destinations.add(str(target))
    service=doc.get('service')
    if service and service['path']!=str(root/'.harness-install/services.json'):raise HarnessError('Recovery service path differs')


def preflight(doc):
    pending=[];done=0
    for entry in doc['moves']:
        source,target=Path(entry['from']),Path(entry['to'])
        safe_parent(source);safe_parent(target)
        original,current=movement.describe(source),movement.describe(target)
        if original==entry['expected'] and current['kind']=='absent':pending.append(entry)
        elif original['kind']=='absent' and current==entry['expected']:done+=1
        else:raise HarnessError('Recovery conflict; current and retained data preserved: '+str(target))
    service=doc.get('service')
    if service:
        path=Path(service['path']);safe_parent(path)
        if image(path) not in [service['before'],service['after']]:raise HarnessError('Service recovery conflicts with newer configuration; preserve both')
    return pending,done


def _restore(root,apply=False):
    root=Path(root);retained=locate(root);descriptor_path=retained/'recovery.json'
    descriptor=regular_json(descriptor_path,{})
    if not descriptor:return {'status':'NO_RETAINED_DATA','root':str(root)}
    if descriptor.get('root')!=str(root) or descriptor.get('state')!=str(state_dir()):raise HarnessError('Retained data belongs to another installation/state root')
    uninstall_id=safe_id(descriptor['uninstall_id'])
    uninstall=regular_json(state_dir()/'uninstalls'/f'{uninstall_id}.json',{})
    if uninstall.get('status')!='COMMITTED':raise HarnessError('Previous uninstall is incomplete; preserve data and reconcile')
    if descriptor.get('restored'):return {'status':'ALREADY_RESTORED','root':str(root),'recovery_id':descriptor['restored'],'project_tasks':descriptor.get('project_tasks',[])}
    pending_id=descriptor.get('pending_recovery')
    if pending_id:
        identifier=safe_id(pending_id);journal=state_dir()/'recoveries'/f'{identifier}.json';doc=regular_json(journal,{})
        if not doc:raise HarnessError('Pending recovery journal is missing; preserve all data')
        validate_record(doc,root,retained,uninstall_id)
    else:
        identifier=new_id('recovery');journal=state_dir()/'recoveries'/f'{identifier}.json';entries=[]
        for source,target in planned_moves(root,retained):
            safe_parent(source);safe_parent(target)
            if target.exists() or target.is_symlink():raise HarnessError('Recovery target contains newer data; preserve: '+str(target))
            entries.append({'from':str(source),'to':str(target),'expected':movement.describe(source)})
        service=None;services=regular_json(retained/'services.json')
        if services:
            fresh={**services,'recipes':{}};path=root/'.harness-install/services.json';safe_parent(path)
            if path.is_symlink():raise HarnessError('Service recovery target is a symlink; preserve')
            if path.exists() and regular_json(path)!=fresh:raise HarnessError('Current service layout conflicts with retained data; preserve both')
            data=json_bytes(fresh)
            service={'path':str(path),'value':fresh,'before':image(path),'after':{'kind':'file','mode':0o600,'uid':os.getuid(),'gid':os.getgid(),'sha256':sha(data),'size':len(data)}}
        doc={'id':identifier,'root':str(root),'retained':str(retained),'uninstall_id':uninstall_id,'status':'STARTED','moves':entries,'service':service}
    pending,done=preflight(doc)
    result={'status':'RECOVERY_PLANNED','root':str(root),'moves':[{'from':e['from'],'to':e['to']} for e in doc['moves']],
            'already_moved':done,'remaining_moves':len(pending),'continuation':bool(pending_id),
            'memory_namespace':descriptor.get('data_namespace'),'project_tasks':descriptor.get('project_tasks',[]),
            'software':'NOT_RESTORED; prepare locked dependencies','hook_trust':'NATIVE_VERIFICATION_REQUIRED'}
    if not apply:return result
    if not pending_id:
        write_json(journal,doc)
        descriptor['pending_recovery']=identifier;write_json(descriptor_path,descriptor)
    try:
        for entry in pending:
            source,target=Path(entry['from']),Path(entry['to']);safe_parent(source);safe_parent(target)
            if movement.describe(source)!=entry['expected'] or movement.describe(target)['kind']!='absent':
                raise HarnessError('Data changed during recovery; preserve and recheck')
            target.parent.mkdir(parents=True,exist_ok=True)
            movement.move(journal.with_suffix('.moves.jsonl'),source,target,'DATA_RECOVERY')
        service=doc.get('service')
        if service:
            path=Path(service['path']);safe_parent(path)
            current=image(path)
            if current!=service['after']:
                if current!=service['before']:raise HarnessError('Service configuration changed during recovery; preserve')
                write_json(path,service['value'])
        doc['status']='DATA_RESTORED';write_json(journal,doc)
        descriptor.pop('pending_recovery',None);descriptor['restored']=identifier;write_json(descriptor_path,descriptor)
    except BaseException as error:
        doc['last_error']=str(error);write_json(journal,doc)
        # A durable partial recovery is resumed, never silently rolled back over
        # an already-restored directory which another actor may now have changed.
        raise
    return {**result,'status':'DATA_RESTORED','transaction_id':identifier,'remaining_moves':0}


def restore(root,apply=False,preparation_locked=False):
    if not apply:return _restore(root)
    from . import processes
    root=Path(root)
    with ExitStack() as stack:
        if not preparation_locked:stack.enter_context(lock(root/'.harness-install/preparation.lock'))
        stack.enter_context(lock(root/'.harness-install/services.lock'))
        processes.require_idle(root)
        if processes.unregistered_consumers(root):raise HarnessError('Runtime consumers are still active; close them before data recovery')
        return _restore(root,True)
