"""Owned Compose deployments. Candidate roots never address another root's services."""
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from .common import HarnessError, lock, read_json, sha, state_dir, write_json
from .transaction import safe_parent

GROUPS={'powercontext':['powercontext'],'screenshot-to-code':['screenshot-backend','screenshot-frontend']}
VOLUMES={'powercontext':['powercontext-data'],'screenshot-to-code':['screenshot-logs','screenshot-assets']}
PORT_KEYS={'powercontext':'POWERCONTEXT_PORT','screenshot-backend':'SCREENSHOT_BACKEND_PORT','screenshot-frontend':'SCREENSHOT_FRONTEND_PORT'}
IMAGE_PINS={'powercontext':'6b2f6e8','screenshot-backend':'d026163','screenshot-frontend':'d026163'}


def root_id(root):return sha(str(Path(root).resolve()).encode())[:16]
def layout_path(root):return Path(root)/'.harness-install/services.json'

def owner_state(root):
    # Candidate tests use an isolated CLI state, while the actual copied runtime
    # remains owned by the shared state that created it. Its source receipt binds
    # this resource identity before preparation; promotion does not relabel DBs.
    receipt=Path(root)/'.harness-install/receipt.json';safe_parent(receipt)
    if receipt.is_symlink():raise HarnessError('Source ownership receipt is a symlink')
    source=read_json(receipt,{})
    if source.get('origin')=='COPIED_UPDATE_CANDIDATE':
        if source.get('target')!=str(root) or not Path(source.get('state','')).is_absolute():raise HarnessError('Candidate resource state identity differs')
        return Path(source['state'])
    return state_dir()


def layout(root,prepare=False):
    root=Path(root).absolute();safe_parent(root/'guard')
    path=layout_path(root);safe_parent(path)
    if path.is_symlink():raise HarnessError('Service ownership receipt is a symlink; preserve it')
    value=read_json(path)
    if value:
        namespace='codex-harness-'+root_id(root)
        if (value.get('root')!=str(root) or value.get('namespace')!=namespace
                or value.get('state_id')!=sha(str(owner_state(root)).encode())[:16]
                or not re.fullmatch(r'codex-harness-[0-9a-f]{16}',value.get('data_namespace',''))):
            raise HarnessError('Service ownership does not match this installation/state root')
        if set(value.get('ports',{}))!=set(PORT_KEYS) or len(set(value['ports'].values()))!=3:
            raise HarnessError('Invalid owned service ports')
        if not all(isinstance(p,int) and 1024<=p<=65535 for p in value['ports'].values()):raise HarnessError('Invalid owned service port')
        expected={name:f'{namespace}-{name}:{pin}' for name,pin in IMAGE_PINS.items()}
        if value.get('images')!=expected:raise HarnessError('Image identity differs from owned deployment')
        return value
    if not prepare:return None
    # No silent adoption of old or manually configured recipes.
    for group in GROUPS:
        runtime=root/'runtimes'/group
        if runtime.exists() and any(p.name!='.env' for p in runtime.iterdir()):raise HarnessError('Unowned service runtime; preserve and reconcile: '+str(runtime))
    namespace='codex-harness-'+root_id(root)
    held=[];ports={}
    try:
        for name in PORT_KEYS:
            sock=socket.socket();sock.bind(('127.0.0.1',0));held.append(sock);ports[name]=sock.getsockname()[1]
    finally:
        for sock in held:sock.close()
    value={'schema_version':1,'root':str(root),'namespace':namespace,'data_namespace':namespace,
           'state_id':sha(str(owner_state(root)).encode())[:16],'ports':ports,
           'images':{name:f'{namespace}-{name}:{pin}' for name,pin in IMAGE_PINS.items()},
           'recipes':{},'image_ids':{},'data_retention':'Named volumes are never deleted by stop/uninstall'}
    write_json(path,value)
    return value


def environment(root):
    value=layout(root)
    if not value:raise HarnessError('Services are not prepared; prepare full components first')
    return {**os.environ,'HARNESS_SERVICE_NAMESPACE':value['namespace'],'HARNESS_VOLUME_NAMESPACE':value['data_namespace'],
            'HARNESS_STATE_ID':value['state_id'],**{key:str(value['ports'][name]) for name,key in PORT_KEYS.items()},
            **{('HARNESS_'+name.upper().replace('-','_')+'_IMAGE'):image for name,image in value['images'].items()}}


def prepare_runtime(root,group):
    if group not in GROUPS:raise HarnessError('Unknown service')
    root=Path(root);value=layout(root,True);recipes=root/'templates/services'/group;runtime=root/'runtimes'/group
    safe_parent(runtime/'guard');runtime.mkdir(parents=True,exist_ok=True)
    owned=value['recipes'].get(group,{})
    for name,digest in owned.items():
        path=runtime/name
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=digest:
            raise HarnessError('Managed service recipe changed; preserve and reconcile: '+str(path))
    expected={p.name:sha(p.read_bytes()) for p in recipes.iterdir() if p.is_file() and p.name!='env.example'}
    for name,digest in expected.items():
        target=runtime/name
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or name not in owned:raise HarnessError('Unowned service recipe: '+str(target))
        if name in owned and owned[name]!=digest:raise HarnessError('Recipe version changed; use a fresh isolated candidate')
    for name in expected:
        from .common import atomic
        atomic(runtime/name,(recipes/name).read_bytes(),0o644)
    env=runtime/'.env'
    if env.is_symlink():raise HarnessError('Refuse symlink provider environment')
    if not env.exists():
        from .common import atomic
        atomic(env,(recipes/'env.example').read_bytes(),0o600)
    value['recipes'][group]=expected;write_json(layout_path(root),value)
    return runtime


def docker(argv,env=None,allow_missing=False):
    result=subprocess.run(['docker',*map(str,argv)],env=env,capture_output=True,text=True,timeout=90)
    if result.returncode:
        if allow_missing and re.search(r'no such (?:object|image|container|volume)',result.stderr.lower()):return None
        # Docker config/inspect may include provider env. Never return raw error bodies.
        raise HarnessError('Docker operation failed: '+str(argv[0])+'; check daemon/ownership and retry')
    return result.stdout


def inspect_owned(root,group):
    value=layout(root)
    if not value:return []
    containers=[]
    for name in GROUPS[group]:
        container=value['namespace']+'-'+name
        raw=docker(['inspect',container],allow_missing=True)
        if raw is None:continue
        info=json.loads(raw)[0];labels=info.get('Config',{}).get('Labels',{})
        compose=str(Path(root)/'runtimes'/group/'compose.yaml')
        if (labels.get('com.docker.compose.project')!=value['namespace']+'-'+group
                or labels.get('com.docker.compose.project.config_files')!=compose):
            raise HarnessError('Unknown container ownership; preserve: '+container)
        if info.get('Image')!=value.get('image_ids',{}).get(name):raise HarnessError('Container image differs from owned receipt; preserve: '+container)
        containers.append({'name':container,'state':info.get('State',{}).get('Status'),'image_id':info.get('Image')})
    return containers

def inspect_volumes(root,group):
    value=layout(root)
    for suffix in VOLUMES[group]:
        name=value['data_namespace']+'-'+suffix
        raw=docker(['volume','inspect',name],allow_missing=True)
        if raw is None:continue
        labels=json.loads(raw)[0].get('Labels') or {}
        if labels.get('com.harness.state_id')!=value['state_id'] or labels.get('com.harness.data_namespace')!=value['data_namespace']:
            raise HarnessError('Unknown data volume ownership; preserve: '+name)


def require_live_binding(root, *, allow_dormant=False):
    """Verify owned resources; dormant mode still requires full containers and volumes."""
    value=layout(root)
    if not value:raise HarnessError('Candidate service ownership is not prepared')
    urls=set()
    for group in value['recipes']:
        containers=inspect_owned(root,group)
        allowed_states={'running','exited'} if allow_dormant else {'running'}
        if len(containers)!=len(GROUPS[group]) or any(v['state'] not in allowed_states for v in containers):
            raise HarnessError('Candidate owned services are missing or not running')
        inspect_volumes(root,group)
        for suffix in VOLUMES[group]:
            if docker(['volume','inspect',value['data_namespace']+'-'+suffix],allow_missing=True) is None:
                raise HarnessError('Candidate owned data volume is missing')
        urls.update(verify_container_specs(root,group,require_running=not allow_dormant))
    return urls

def verify_container_specs(root,group,require_running=False):
    """Validate existing partial or stopped containers against owned recipes."""
    value=layout(root);urls=set()
    resolved=json.loads(compose(root,group,['config','--format','json']))
    for name,config in resolved['services'].items():
        raw=docker(['inspect',value['namespace']+'-'+name],allow_missing=True)
        if raw is None:
            if require_running:raise HarnessError('Candidate owned service is missing')
            continue
        info=json.loads(raw)[0]
        if require_running and not info.get('State',{}).get('Running'):
            raise HarnessError('Candidate owned service is not running')
        expected={}
        for port in config.get('ports',[]):
            if port.get('host_ip')!='127.0.0.1' or int(port['published'])!=value['ports'][name]:
                raise HarnessError('Candidate service port differs from owned layout')
            expected[str(port['target'])+'/'+port.get('protocol','tcp')]=[{'HostIp':'127.0.0.1','HostPort':str(port['published'])}]
            urls.add('http://127.0.0.1:'+str(port['published']))
        bindings=[info.get('HostConfig',{}).get('PortBindings',{})]
        if info.get('State',{}).get('Running'):bindings.append(info.get('NetworkSettings',{}).get('Ports',{}))
        for actual in bindings:
            # Docker also reports exposed but unpublished image ports as null.
            published={key:ports for key,ports in (actual or {}).items() if ports}
            if published!=expected:raise HarnessError('Candidate running service ports changed')
        expected_mounts=[]
        for mount in config.get('volumes',[]):
            if mount.get('type')!='volume':raise HarnessError('Candidate service has an unexpected mount type')
            volume=resolved.get('volumes',{}).get(mount['source'],{}).get('name')
            if not volume:raise HarnessError('Candidate service volume ownership is unknown')
            expected_mounts.append(('volume',volume,mount['target'],not mount.get('read_only',False)))
        actual_mounts=[(v.get('Type'),v.get('Name'),v.get('Destination'),v.get('RW',True)) for v in info.get('Mounts',[])]
        if sorted(actual_mounts,key=repr)!=sorted(expected_mounts,key=repr):
            raise HarnessError('Candidate running service volume mounts changed')
    return urls

def preflight_build(root,group):
    value=layout(root)
    if unowned_resource_consumers(root):raise HarnessError('Another container uses this deployment volume/image; preserve and reconcile before rebuilding')
    if inspect_owned(root,group):raise HarnessError('Existing container consumers must be removed before rebuilding; use an isolated candidate')
    for name in read_json(state_dir()/'projects.json',{}):
        if read_json(Path(name)/'.harness/state/project.json',{}).get('harness_root')==str(root):
            raise HarnessError('Enabled project consumers use this root; build an isolated candidate')
    for name in GROUPS[group]:
        raw=docker(['image','inspect',value['images'][name]],allow_missing=True)
        if raw is not None and json.loads(raw)[0].get('Id')!=value['image_ids'].get(name):
            raise HarnessError('Unowned or changed image tag; preserve: '+value['images'][name])


def build_identity(root,group):
    """Recompute build identity from independently checked source/software/resources."""
    from . import cli,software,processes
    root=Path(root);value=layout(root)
    if not value or group not in value['recipes']:raise HarnessError('Service build ownership is incomplete')
    processes.require_idle(root)
    software.require_preparation_idle(root)
    software.verify_all(root)
    if unowned_resource_consumers(root):raise HarnessError('Another container uses this service build; preserve and reconcile')
    for name in read_json(state_dir()/'projects.json',{}):
        if read_json(Path(name)/'.harness/state/project.json',{}).get('harness_root')==str(root):
            raise HarnessError('Enabled consumers use this runtime; prepare an isolated candidate')
    inspect_owned(root,group);inspect_volumes(root,group)
    verify_container_specs(root,group)
    images={}
    for name in GROUPS[group]:
        expected=value['image_ids'].get(name)
        raw=docker(['image','inspect',value['images'][name]],allow_missing=True)
        if not expected or raw is None or json.loads(raw)[0].get('Id')!=expected:
            raise HarnessError('Service build image is missing or changed; preserve and reconcile')
        images[name]=expected
    return {'root':str(root),'group':group,'state_id':value['state_id'],
            'release_hash':cli.release_hash(root),'recipes':value['recipes'][group],
            'deployment':{key:value[key] for key in ('schema_version','namespace','data_namespace','ports','images')},
            'image_ids':images,'runtime_files':software.inventory(root,['runtimes/'+group])}


def build_receipts_path(root):
    path=Path(root)/'.harness-install/service-builds.json';safe_parent(path)
    if path.is_symlink():raise HarnessError('Service build receipt is a symlink; preserve')
    return path


def record_build(root,group):
    path=build_receipts_path(root);receipts=read_json(path,{})
    if not isinstance(receipts,dict) or set(receipts)-set(GROUPS):raise HarnessError('Unknown service build receipt; preserve')
    receipts[group]=build_identity(root,group);write_json(path,receipts)


def reusable_build(root,group):
    """Caller holds services lock; no stage flag alone authorizes build reuse."""
    path=build_receipts_path(root);receipts=read_json(path,{})
    if not isinstance(receipts,dict) or set(receipts)-set(GROUPS):raise HarnessError('Unknown service build receipt; preserve')
    if group not in receipts:return False
    if receipts[group]!=build_identity(root,group):
        raise HarnessError('Service build source or runtime identity changed; preserve and use an isolated candidate')
    return True


def record_images(root,group):
    for name in GROUPS[group]:record_image(root,name)

def record_image(root,name):
    value=layout(root)
    info=json.loads(docker(['image','inspect',value['images'][name]]))[0]
    value['image_ids'][name]=info['Id']
    write_json(layout_path(root),value)

def verify_image_tags(value):
    for name,tag in value['images'].items():
        raw=docker(['image','inspect',tag],allow_missing=True)
        if raw is not None and json.loads(raw)[0].get('Id')!=value['image_ids'].get(name):
            raise HarnessError('Image tag ownership changed; preserve: '+tag)

def remove_image_tags(value):
    # Remove only this root's exact tags, never force shared image layers.
    verify_image_tags(value)
    removed=[]
    for tag in value['images'].values():
        if docker(['image','inspect',tag],allow_missing=True) is not None:
            docker(['image','rm',tag]);removed.append(tag)
    return removed


def compose(root,group,arguments):
    value=layout(root);env=environment(root)
    return docker(['compose','-p',value['namespace']+'-'+group,'-f',str(Path(root)/'runtimes'/group/'compose.yaml'),*arguments],env)


def _manage(root,action,group='all'):
    if group!='all' and group not in GROUPS:raise HarnessError('Unknown service')
    groups=list(GROUPS) if group=='all' else [group]
    value=layout(root)
    if not value and action in {'status','stop','remove'}:return {'status':'NOT_PREPARED','services':[],'root':str(root)}
    if not value:raise HarnessError('Full service preparation is required before starting')
    if group=='all' and action in {'status','stop','remove'}:groups=list(value['recipes'])
    results=[]
    for selected in groups:
        # Do not rewrite or bless recipes during status/start/stop.
        owned=value['recipes'].get(selected)
        if not owned:raise HarnessError('Service recipes not owned/prepared: '+selected)
        for name,digest in owned.items():
            path=Path(root)/'runtimes'/selected/name
            if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=digest:raise HarnessError('Service recipe changed; preserve and reconcile: '+str(path))
        containers=inspect_owned(root,selected)
        if action=='status':results.append({'service':selected,'containers':containers});continue
        inspect_volumes(root,selected)
        if action=='start':
            if unowned_resource_consumers(root):raise HarnessError('Another container uses this deployment volume/image; preserve and reconcile before starting')
            for name in GROUPS[selected]:
                expected=value['image_ids'].get(name)
                info=json.loads(docker(['image','inspect',value['images'][name]]))[0]
                if not expected or info['Id']!=expected:raise HarnessError('Managed image changed/unverified; preserve and reconcile: '+name)
            compose(root,selected,['up','-d','--no-build'])
        elif action=='stop':compose(root,selected,['stop'])
        elif action=='remove':compose(root,selected,['down']) # Deliberately no --volumes or image removal.
        else:raise HarnessError('Unsupported service action')
        results.append({'service':selected,'containers':inspect_owned(root,selected)})
    if action=='start':write_binding(root)
    return {'status':'SERVICE_STATUS' if action=='status' else 'SERVICES_'+action.upper(),'root':str(root),
            'namespace':value['namespace'],'data_namespace':value['data_namespace'],'services':results,'volumes':'PRESERVED'}


def manage(root,action,group='all'):
    if action=='status':return _manage(root,action,group)
    with lock(Path(root)/'.harness-install/services.lock'):
        return _manage(root,action,group)


def write_binding(root):
    value=layout(root);binding={'private_machine_binding':True,'mcp_servers':{},'component_commands':{},'service_urls':{},
                              'deployment_namespace':value['namespace'],'data_namespace':value['data_namespace']}
    for group in GROUPS:
        if group not in value['recipes']:continue
        resolved=json.loads(compose(root,group,['config','--format','json']))
        urls={}
        for name,configuration in resolved['services'].items():
            for port in configuration.get('ports',[]):
                if port.get('host_ip')!='127.0.0.1' or int(port['published'])!=value['ports'][name]:raise HarnessError('Service binding is not the owned loopback port')
                urls[name]=f'http://127.0.0.1:{port["published"]}'
        if group=='powercontext':
            url=urls['powercontext'];binding['service_urls']['powercontext']=url
            binding['mcp_servers']['powercontext']={'url':url+'/mcp/','default_tools_approval_mode':'prompt'}
            binding['component_commands']['powercontext']=[str(Path(root)/'runtimes/powercontext/client-venv/bin/powercontext'),'doctor','--server-url',url,'--json']
        else:
            binding['service_urls'].update(screenshot_backend=urls['screenshot-backend'],screenshot_frontend=urls['screenshot-frontend'])
    from . import resource_manager
    if resource_manager.descriptor_path(root).exists():
        binding=resource_manager.public_binding(root, binding)
    write_json(Path(root)/'services-binding.json',binding)
    return binding


def unowned_resource_consumers(root):
    value=layout(root)
    if not value:return []
    expected={value['namespace']+'-'+name for group in value['recipes'] for name in GROUPS[group]}
    tags=set(value['images'].values())
    volumes={value['data_namespace']+'-'+suffix for suffixes in VOLUMES.values() for suffix in suffixes}
    ids=docker(['ps','-aq']).split()
    if not ids:return []
    # Read only the fields needed for dependency references; never collect env.
    lines=docker(['inspect','--format','{{json .Name}} {{json .Config.Image}} {{json .Mounts}}',*ids]).splitlines()
    found=[]
    decoder=json.JSONDecoder()
    for line in lines:
        values=[];remaining=line
        for _ in range(3):
            item,end=decoder.raw_decode(remaining.lstrip());remaining=remaining.lstrip()[end:];values.append(item)
        name,tag,mounts=values;name=name.removeprefix('/')
        if name not in expected and (tag in tags or any(m.get('Name') in volumes for m in mounts)):
            found.append({'container':name,'status':'UNOWNED_CONTAINER_USES_MANAGED_RESOURCE'})
    return found
