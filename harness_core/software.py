"""Exact software ownership snapshots; provider environments and data stay separate."""
import os
import subprocess
import sys
from pathlib import Path
from .common import HarnessError, read_json, sha, write_json
from .transaction import image, safe_parent


def retained_path(path):
    p=Path(path)
    return ('private' in p.parts or p.name=='.env' or p.name.startswith('.env.')
            or p.suffix.lower() in {'.db','.sqlite','.sqlite3','.log'}
            or p.name.endswith(('-wal','-shm')))


def inventory(root,areas):
    result={};root=Path(root)
    for area in areas:
        if Path(area).is_absolute() or '..' in Path(area).parts:raise HarnessError('Unsafe software ownership area')
        base=root/area
        if not base.exists() and not base.is_symlink():continue
        safe_parent(base/'guard')
        for directory,dirs,files in os.walk(base,followlinks=False):
            for name in list(dirs):
                p=Path(directory)/name
                if p.is_symlink():files.append(name);dirs.remove(name)
            for name in files:
                path=Path(directory)/name;relative=path.relative_to(root)
                if not retained_path(relative):result[str(relative)]=image(path)
    return result


def snapshot_path(root):return Path(root)/'.harness-install/runtime-ownership.json'

def capture(root,areas):
    root=Path(root);path=snapshot_path(root);safe_parent(path)
    if path.is_symlink():raise HarnessError('Software ownership receipt is a symlink')
    old=read_json(path,{})
    for area in old.get('areas',[]):
        if not any(Path(area)==Path(chosen) or Path(chosen) in Path(area).parents or Path(area) in Path(chosen).parents for chosen in areas):
            expected={name:value for name,value in old['files'].items() if Path(area)==Path(name) or Path(area) in Path(name).parents}
            if inventory(root,[area])!=expected:raise HarnessError('Unrelated runtime changed during preparation; preserve and reconcile')
    prior=set(old.get('areas',[]));chosen=sorted(prior|set(areas))
    value={'root':str(root),'areas':chosen,'files':inventory(root,chosen),
           'retained':'Provider environments, private state, logs and databases are not software'}
    write_json(path,value);return value


def verify(root):
    value=read_json(snapshot_path(root),{})
    if not value:return {'root':str(root),'areas':[],'files':{},'ownership':'NO_MANAGED_RUNTIME'}
    if value.get('root')!=str(root):raise HarnessError('Runtime ownership belongs to another root')
    actual=inventory(root,value['areas'])
    if actual!=value['files']:raise HarnessError('Dedicated runtime files changed or contain unknown additions; preserve and reconcile')
    return value


def verify_all(root):
    """Check all runtime software, including areas outside the saved snapshot."""
    from . import sidecars
    value=verify(root);deployment=sidecars.layout(root);recipes={}
    if deployment:
        for group,files in deployment['recipes'].items():
            if group not in sidecars.GROUPS:raise HarnessError('Unknown service recipe group')
            for name,digest in files.items():
                if Path(name).name!=name:raise HarnessError('Unsafe recipe ownership path')
                path=Path(root)/'runtimes'/group/name;safe_parent(path)
                if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=digest:
                    raise HarnessError('Managed recipe changed; preserve: '+str(path))
                recipes[str(path.relative_to(root))]=image(path)
    if inventory(root,['runtimes'])!={**recipes,**value['files']}:
        raise HarnessError('Unowned runtime files or changed dependencies; preserve and reconcile: '+str(root))
    return value


def require_prepared_or_empty(root,area):
    path=Path(root)/area
    if not path.exists():return
    value=verify(root)
    if area not in value['areas'] and inventory(root,[area]):raise HarnessError('Unowned runtime directory; preserve: '+str(path))


def process_identity(pid):
    try:
        text=(Path('/proc')/str(pid)/'stat').read_text();return text[text.rfind(')')+2:].split()[19]
    except (OSError,IndexError):return None


def group_live(group):
    # Linux process groups include descendants which outlive their installer.
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw=path.read_text();fields=raw[raw.rfind(')')+2:].split()
            if int(fields[2])==group and fields[0] not in {'Z','X'}:return True
        except (OSError,ValueError,IndexError):continue
    return False


def require_writers_stopped(doc):
    if doc.get('writer_pending'):
        raise HarnessError('Package writer launch was interrupted before registration; preserve staging and reconcile journal')
    for writer in doc.get('writers',[]):
        if group_live(writer['group']):
            raise HarnessError('Package writer process group is still live; preserve staging and wait')


def require_preparation_idle(root):
    """Inspect durable package journals without restoring or blessing software."""
    root=Path(root)
    directory=root/'.harness-install/preparation-data'
    safe_parent(directory/'guard')
    for path in sorted(directory.glob('*.json')):
        safe_parent(path)
        if path.is_symlink():raise HarnessError('Package preparation journal is a symlink; preserve and reconcile')
        doc=read_json(path)
        if not isinstance(doc,dict) or doc.get('root')!=str(root):
            raise HarnessError('Package preparation journal ownership is unknown; preserve and reconcile')
        if doc.get('status')!='RESTORED':
            require_writers_stopped(doc)
            raise HarnessError('Package preparation is unfinished; recover its journal before acceptance or promotion')


def package_worker(journal,argv):
    # The pending marker is durable BEFORE spawn; registration happens BEFORE exec.
    path=Path(journal);doc=read_json(path)
    if not doc.get('writer_pending'):raise HarnessError('Package launch has no pending journal')
    doc.setdefault('writers',[]).append({'pid':os.getpid(),'identity':process_identity(os.getpid()),'group':os.getpgrp()})
    doc['writer_pending']=False;write_json(path,doc)
    os.execvpe(argv[0],argv,os.environ)


def recover_preparation(root):
    from . import movement
    root=Path(root)
    for path in sorted((root/'.harness-install/preparation-data').glob('*.json')):
        safe_parent(path)
        if path.is_symlink():raise HarnessError('Preparation journal is a symlink')
        doc=read_json(path)
        if doc.get('status')=='RESTORED':continue
        if doc.get('root')!=str(root):raise HarnessError('Preparation journal belongs to another root')
        if doc.get('process_identity') and process_identity(doc['pid'])==doc['process_identity']:
            raise HarnessError('Preparation process is still live; preserve its data staging and wait')
        require_writers_stopped(doc)
        log=path.with_suffix('.moves.jsonl');saved=root/'private/runtime-data-staging'/path.stem
        entries=movement.read_moves(log,root,saved,saved)
        movement.apply_restore(movement.preflight_restore(entries))
        # Partial package output cannot distinguish installer files from later user
        # additions after a crash. Restore data, but never bless changed software.
        if doc.get('software_before')!=inventory(root,[doc['area']]):
            doc['status']='DATA_RESTORED_SOFTWARE_CONFLICT';write_json(path,doc)
            raise HarnessError('Interrupted preparation software changed; retained data restored, unknown files preserved. Reconcile before rebuilding: '+str(root/doc['area']))
        capture(root,[doc['area']])
        doc['status']='RESTORED';write_json(path,doc)


def preserve_data(root,area):
    """Durable data separation before package tools delete owned trees."""
    import contextlib
    from .common import new_id
    from . import movement
    @contextlib.contextmanager
    def preservation():
        root_path=Path(root);base=root_path/area;identifier=new_id('data')
        saved=root_path/'private/runtime-data-staging'/identifier
        journal=root_path/'.harness-install/preparation-data'/f'{identifier}.json'
        safe_parent(journal);journal.parent.mkdir(parents=True,exist_ok=True)
        log=journal.with_suffix('.moves.jsonl');selected=[]
        if base.exists():
            for directory,dirs,files in os.walk(base,followlinks=False):
                for name in list(dirs):
                    path=Path(directory)/name
                    if retained_path(path.relative_to(root_path)):
                        selected.append(path);dirs.remove(name)
                    elif path.is_symlink():dirs.remove(name)
                for name in files:
                    path=Path(directory)/name
                    if retained_path(path.relative_to(root_path)):selected.append(path)
        doc={'root':str(root_path),'area':area,'status':'STARTED','log':str(log),'pid':os.getpid(),'process_identity':process_identity(os.getpid()),'software_before':inventory(root_path,[area]),'writers':[],'writer_pending':False}
        write_json(journal,doc)
        try:
            for original in selected:
                target=saved/original.relative_to(root_path);target.parent.mkdir(parents=True,exist_ok=True)
                movement.move(log,original,target,'RUNTIME_DATA')
            def run_package(argv,cwd=None,env=None):
                current=read_json(journal);current['writer_pending']=True;write_json(journal,current)
                package_env=dict(env or os.environ)
                package_env['PYTHONPATH']=str(root_path)+os.pathsep+package_env.get('PYTHONPATH','')
                subprocess.run([sys.executable,'-c','import sys;from harness_core.software import package_worker;package_worker(sys.argv[1],sys.argv[2:])',str(journal),*map(str,argv)],cwd=cwd or root_path,env=package_env,start_new_session=True,check=True)
            yield run_package
        finally:
            doc=read_json(journal)
            require_writers_stopped(doc)
            entries=movement.read_moves(log,root_path,saved,saved)
            actions=movement.preflight_restore(entries)
            movement.apply_restore(actions)
            capture(root_path,[area])
            doc['status']='RESTORED';write_json(journal,doc)
    return preservation()
