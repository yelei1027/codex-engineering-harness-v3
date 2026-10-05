import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from . import context, memory, review, workflow
from .inventory import endpoint_identity, inspect_registrations
from .common import ROOT,PWF_CODEX_RUNTIME_FILES,HarnessError,atomic,canonical,distribution_path,git,lock,new_id,now,private_path,read_json,repo_root,run,safe_id,sha,state_dir,write_json
from .transaction import Transaction,image,preflight_rollback,rollback

BEGIN='# BEGIN CODEX ENGINEERING HARNESS V3'
END='# END CODEX ENGINEERING HARNESS V3'

def output(value):
    print(json.dumps(value,ensure_ascii=False,indent=2))

def release_hash(root=None):
    root = ROOT if root is None else Path(root)
    # Portable control plane and lock; no runtime DBs, credentials or audit output.
    paths=[root/name for name in ['VERSION','harness.lock','plugin.json','LICENSE','THIRD_PARTY_NOTICES','SECURITY.md','contracts/workflow.json','.agents/plugins/marketplace.json','.codex-plugin/plugin.json']]
    trees=read_json(root/'manifests/source-trees.json',{})
    for name,tree in trees.items():
        paths.extend(root/'vendor'/name/relative for relative in tree.get('files',{}) if not private_path(relative))
    for directory in ['harness_core','bin','skills','plugins','manifests','templates','scripts','tests']:
        if (root/directory).exists():
            paths.extend(p for p in (root/directory).rglob('*') if p.is_file() and not private_path(p.relative_to(root)))
    return sha(canonical({str(p.relative_to(root)):sha(os.readlink(p).encode()) if p.is_symlink() else sha(p.read_bytes()) for p in sorted(set(paths)) if p.exists()}))

def machine_binding():
    binding=read_json(ROOT/'machine-binding.json',{})
    return binding

def defaults():
    return read_json(ROOT/'templates/project/defaults.json',{
        'profile':'L2','initialize_on_start':True,'context':{'warning_percent':70,'checkpoint_percent':80,'stop_new_work_percent':90,'handoff_after_compactions':2}})

def registry():
    return read_json(state_dir()/'projects.json',{})

def registered_project(value):
    """Resolve an enabled project from its root or any descendant path."""
    path=Path(value).expanduser().resolve()
    if not path.is_dir():raise HarnessError('Project directory does not exist: '+str(path))
    projects=registry();matches=[]
    for name in projects:
        root=Path(name).expanduser().resolve()
        if path==root or root in path.parents:matches.append(root)
    if matches:
        return max(matches,key=lambda item:len(item.parts))
    # Preserve idempotent disable for a valid Git project that is simply not enabled.
    probe=run(['git','-C',path,'rev-parse','--show-toplevel'],ok=False)
    if probe.returncode==0:
        return Path(probe.stdout.decode().strip()).resolve()
    available='\n'.join('- '+name for name in sorted(projects)) or '- (none)'
    raise HarnessError('Current path is not inside an enabled Harness project: '+str(path)+'\nEnabled projects:\n'+available)

def verify_binding_state(value):
    selected=value.get('state_root') if value else None
    if selected:
        path=Path(selected)
        if not path.is_absolute() or '..' in path.parts or path!=state_dir():
            raise HarnessError('Project belongs to another installation state: '+str(selected)+'; use its verified HARNESS_STATE_DIR. No permissions were changed')

def project_binding(repo):
    value=read_json(repo/'.harness/state/project.json')
    if not value:
        raise HarnessError(f'Project is not enabled: {repo}. Run harness init "{repo}" --profile L2 for planning hooks, or harness enable "{repo}" for L1')
    verify_binding_state(value)
    if value.get('harness_root')!=str(ROOT):
        raise HarnessError(f'Project belongs to another Harness root: {value.get("harness_root")}; current root: {ROOT}. Use its original launcher or explicitly migrate the project')
    return value

def select_project(value):
    path=Path(value).expanduser().resolve()
    if not path.is_dir():raise HarnessError('Project directory does not exist: '+str(path))
    result=run(['git','-C',path,'rev-parse','--show-toplevel'],ok=False)
    if result.returncode:raise HarnessError(f'Project has no Git working tree: {path}. Run harness init "{path}" first')
    return repo_root(result.stdout.decode().strip())

def initialize_project(value,profile):
    path=Path(value).expanduser().resolve()
    if not path.is_dir():raise HarnessError('Project directory does not exist: '+str(path))
    probe=run(['git','-C',path,'rev-parse','--show-toplevel'],ok=False)
    initialized=False
    if probe.returncode==0:
        repo=select_project(path)
    else:
        bare=run(['git','-C',path,'rev-parse','--is-bare-repository'],ok=False)
        if bare.returncode==0 and bare.stdout.strip()==b'true':
            raise HarnessError('Choose a Git working tree instead of a bare repository')
        if path==Path.home().resolve() or path==Path(path.anchor):
            raise HarnessError('Choose a project folder instead of the home directory or filesystem root')
        if (path/'.git').exists() or (path/'.git').is_symlink():
            raise HarnessError('Existing .git metadata cannot be resolved; preserve it and inspect Git configuration')
        for directory,dirs,files in os.walk(path,followlinks=False):
            if '.git' in dirs or '.git' in files:
                raise HarnessError('Folder contains a Git repository; select that project instead: '+str(Path(directory)))
        run(['git','-C',path,'init','--quiet']);initialized=True
        repo=repo_root(path)
    try:result=enable(repo,profile)
    except HarnessError as error:
        if initialized:raise HarnessError(str(error)+'; Git was initialized and preserved; reconcile configuration then retry') from error
        raise
    return {**result,'git':'INITIALIZED' if initialized else 'EXISTING','git_commit':'NOT_CREATED','remote':'UNCHANGED',
            'memory':memory.try_bind(repo)}

def refresh(repo,profile=None):
    doc=project_binding(repo)
    if doc['harness_root']!=str(ROOT):raise HarnessError('Use the project-owned Harness root for refresh')
    old=disable(repo)
    try:new=enable(repo,profile or doc['profile'])
    except BaseException:
        rollback(old['transaction_id']);raise
    refresh_id=new_id('refresh')
    record={'id':refresh_id,'status':'COMMITTED','repo':str(repo),'created':now(),
            'disable_transaction':old['transaction_id'],'enable_transaction':new['transaction_id']}
    try:write_json(state_dir()/'refreshes'/f'{refresh_id}.json',record)
    except BaseException:
        rollback(new['transaction_id']);rollback(old['transaction_id']);raise
    return {**new,'transaction_id':refresh_id,'enable_transaction_id':new['transaction_id'],
            'disable_transaction_id':old['transaction_id'],'memory':memory.try_bind(repo)}

def migration_plan(repo,source_root):
    """Observe a cross-root migration before touching project configuration."""
    doc=read_json(repo/'.harness/state/project.json',{})
    old_root=Path(source_root).expanduser().resolve()
    if not doc or doc.get('harness_root')!=str(old_root):
        raise HarnessError('Migration source must match the current project binding')
    if old_root==ROOT:raise HarnessError('Same-root project: use refresh instead')
    accepted=read_json(state_dir()/'accepted.json',{})
    if accepted.get('root')!=str(ROOT) or accepted.get('release_hash')!=release_hash():
        raise HarnessError('Migration target must be the unchanged accepted release')
    record=registry().get(str(repo))
    if not record or record.get('release_hash')!=doc.get('release_hash'):
        raise HarnessError('Project ownership registry does not match the source binding')
    verify_owned(record)
    if not old_root.is_dir() or release_hash(old_root)!=doc['release_hash']:
        raise HarnessError('Original release changed or unavailable; preserve binding and reconcile first')
    tasks=[]
    for path in sorted((repo/'.harness/task').glob('*/events.jsonl')):
        task=workflow.load(path.parent)
        if task.get('node') not in context.INACTIVE_TASK_NODES:tasks.append(task['task_id'])
    return {'status':'MIGRATION_DEFERRED' if tasks else 'MIGRATION_PLANNED','repo':str(repo),
            'from_root':str(old_root),'to_root':str(ROOT),'from_version':doc['version'],
            'to_version':accepted['version'],'profile':doc['profile'],'active_tasks':tasks,
            'preserved':'Business files, task state, journals and memory; old release stays available',
            'memory':memory.migration_status(repo,old_root),
            'hooks':'Native trust must be rechecked after hook changes' if doc['profile']=='L2' else 'NOT_REGISTERED',
            'authorization':'Preview only; apply after explicit migration scope approval'}

def migrate(repo,source_root,apply=False):
    plan=migration_plan(repo,source_root)
    if not apply:return plan
    if plan['active_tasks']:raise HarnessError('Active or paused tasks remain pinned; finish them before migration')
    # Share the workflow lock with task creation and transitions, so no task can
    # start between the final active-consumer check and rebinding.
    with lock(repo/'.harness/state/workflow.lock'):
        plan=migration_plan(repo,source_root)
        if plan['active_tasks']:
            raise HarnessError('Task appeared during migration; old binding preserved')
        profile=plan['profile']
        old=disable(repo)
        try:new=enable(repo,profile)
        except BaseException:
            rollback(old['transaction_id']);raise
        identifier=new_id('migration')
        record={'id':identifier,'status':'COMMITTED','kind':'MIGRATION','repo':str(repo),'created':now(),
                'from_root':plan['from_root'],'to_root':plan['to_root'],
                'disable_transaction':old['transaction_id'],'enable_transaction':new['transaction_id']}
        try:write_json(state_dir()/'refreshes'/f'{identifier}.json',record)
        except BaseException:
            rollback(new['transaction_id']);rollback(old['transaction_id']);raise
    return {**new,'status':'MIGRATED','transaction_id':identifier,'from_root':plan['from_root'],
            'to_root':plan['to_root'],'old_release':'PRESERVED','task_state':'PRESERVED',
            'memory':memory.migration_status(repo,Path(plan['from_root']))}

def rollback_operation(txid):
    txid=safe_id(txid)
    path=state_dir()/'refreshes'/f'{txid}.json'
    composite=read_json(path)
    if not composite:return rollback(txid)
    if composite.get('status')=='ROLLED_BACK':
        return {'transaction_id':txid,'status':'ALREADY_ROLLED_BACK','kind':'REFRESH'}
    try:
        # Validate the two-step restoration as one virtual transaction before
        # either journal writes. A conflict in the old binding must not leave
        # the project half-rolled-back/disabled.
        virtual=preflight_rollback(composite['enable_transaction'])
        preflight_rollback(composite['disable_transaction'],virtual)
        first=rollback(composite['enable_transaction'])
        second=rollback(composite['disable_transaction'])
    except BaseException as error:
        composite.update(status='ROLLBACK_PARTIAL',error=str(error),updated=now());write_json(path,composite)
        raise
    composite.update(status='ROLLED_BACK',rolled_back=now());write_json(path,composite)
    return {'transaction_id':txid,'status':'ROLLED_BACK','kind':'REFRESH',
            'enable_rollback':first['status'],'disable_rollback':second['status']}

def preparation_plan(value,profile=None):
    """Public observational seam; it must not create Git, config or state."""
    path=Path(value).expanduser().resolve()
    if not path.is_dir():raise HarnessError('Project directory does not exist: '+str(path))
    probe=run(['git','-C',path,'rev-parse','--show-toplevel'],ok=False)
    repo=select_project(path) if probe.returncode==0 else path
    if probe.returncode:
        bare=run(['git','-C',path,'rev-parse','--is-bare-repository'],ok=False)
        if bare.returncode==0 and bare.stdout.strip()==b'true':raise HarnessError('Choose a Git working tree instead of a bare repository')
        if path in {Path.home().resolve(),Path(path.anchor)}:raise HarnessError('Choose a project folder instead of home or filesystem root')
        if (path/'.git').exists() or (path/'.git').is_symlink():raise HarnessError('Existing .git metadata cannot be resolved; preserve and inspect it')
        for directory,dirs,files in os.walk(path,followlinks=False):
            if '.git' in dirs or '.git' in files:raise HarnessError('Folder contains a Git repository; select that project instead: '+str(directory))
    if (repo/'.harness/state/project.json').exists():
        doc=project_binding(repo);record=registry().get(str(repo),{})
        if not record:raise HarnessError('Project ownership registration is missing; preserve and reconcile')
        verify_owned(record)
        if doc.get('release_hash')!=release_hash():raise HarnessError('Bound source changed; review and refresh this project explicitly')
        return {'status':'PROJECT_READY','repo':str(repo),'profile':doc['profile'],
                'confirmation':'NOT_REQUIRED_FOR_VALID_EXISTING_SETUP','permissions':'VERIFY_EFFECTIVE_NATIVE_CHAT_STATE',
                'activation':'Explicit user request in this chat; not inferred from project installation'}
    chosen=profile or defaults()['profile']
    _,plans=project_mutation_plan(repo,chosen)
    return {'status':'PREPARATION_REQUIRED','repo':str(repo),'profile':chosen,
            'git':'WILL_INITIALIZE' if probe.returncode else 'EXISTING_NO_COMMIT_OR_REMOTE_CHANGE',
            'changes':[str(p.relative_to(repo)) for p,_,_ in plans],
            'preserved':'Business files, existing edits and unrelated configuration',
            'hooks':'PENDING_NATIVE_USER_TRUST' if chosen=='L2' else 'NOT_REGISTERED',
            'permissions':'READ_ONLY_DEFAULT_FOR_NEW_TRUSTED_SESSION; current chat is not changed',
            'confirmation':'Confirm this preparation scope once, then use --apply; activation does not approve implementation'}

def prepare_project(value,profile=None,apply=False):
    plan=preparation_plan(value,profile)
    if not apply or plan['status']=='PROJECT_READY':return plan
    return initialize_project(plan['repo'],plan['profile'])

def start_project(value,profile=None,apply=False):
    path=Path(value).expanduser().resolve()
    if not path.is_dir():raise HarnessError('Project directory does not exist: '+str(path))
    probe=run(['git','-C',path,'rev-parse','--show-toplevel'],ok=False)
    repo=select_project(path) if probe.returncode==0 else path
    if not (repo/'.harness/state/project.json').exists():
        if not apply:raise HarnessError('Preview harness prepare first; confirm the setup scope before using --apply')
        with lock(state_dir()/'operation.lock'):
            result=prepare_project(repo,profile,True)
        print(json.dumps(result,ensure_ascii=False),file=sys.stderr,flush=True)
        repo=Path(result['repo'])
    doc=project_binding(repo);verify_owned(registry().get(str(repo),{}))
    return repo,doc

def managed_regions(text):
    starts=list(re.finditer(r'(?m)^'+re.escape(BEGIN)+r'(?: ([A-Z0-9_-]+))?$',text))
    ends=list(re.finditer(r'(?m)^'+re.escape(END)+r'(?: ([A-Z0-9_-]+))?$',text))
    if len(starts)!=len(ends):raise HarnessError('Malformed/duplicate Harness markers')
    regions=[];labels=set()
    for begin,end in zip(starts,ends):
        label=begin.group(1) or ''
        if label in labels or end.start()<begin.end() or label!=(end.group(1) or ''):
            raise HarnessError('Malformed/duplicate Harness markers')
        if regions and begin.start()<regions[-1][2]:raise HarnessError('Nested Harness markers')
        labels.add(label);regions.append((label,begin.start(),end.end()))
    return regions

def block_images(text):
    return {label:sha(text[begin:end].encode()) for label,begin,end in managed_regions(text)}

def strip_block(text):
    for _,begin,end in reversed(managed_regions(text)):
        if end<len(text) and text[end]=='\n':end+=1
        text=text[:begin]+text[end:]
    return text

def merge_block(text,block):
    clean=strip_block(text)
    return clean+('' if not clean or clean.endswith('\n') else '\n')+BEGIN+'\n'+block.rstrip()+'\n'+END+'\n'

def json_toml(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,list):return '['+', '.join(json_toml(x) for x in value)+']'
    if isinstance(value,str):return json.dumps(value,ensure_ascii=False)
    raise HarnessError('Unsupported MCP TOML field type')

def mcp_fragment(existing,repo=None):
    data=tomllib.loads(strip_block(existing)) if existing else {}
    existing_servers=data.get('mcp_servers',{})
    global_path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'config.toml'
    globals_=tomllib.loads(global_path.read_text()).get('mcp_servers',{}) if global_path.exists() else {}
    lines=[]
    for name,server in machine_binding().get('mcp_servers',{}).items():
        if not isinstance(server,dict):raise HarnessError('mcp_servers must map names to command tables')
        if name=='powercontext' and repo is not None:
            planned=memory.migration_status(repo)
            if planned['status'] in {'MEMORY_CURRENT_SERVICE','MEMORY_PINNED_TO_ORIGINAL_SERVICE','MEMORY_RECOVERED_SERVICE'}:
                server={**server,'url':planned['url']+'/mcp/'}
            elif planned['status']=='UNSYNCED':
                # Keep core local work available, while preventing a retained
                # project's raw MCP tools from silently addressing a new DB.
                server={**server,'enabled':False}
        for location,servers in [('project',existing_servers),('global',globals_)]:
            if name in servers and servers[name]!=server:
                raise HarnessError(f'Conflicting {location} MCP server: {name}')
            for other,cfg in servers.items():
                if other!=name and endpoint_identity(cfg) is not None and endpoint_identity(cfg)==endpoint_identity(server):
                    raise HarnessError(f'Same MCP component already registered as {other} in {location}')
        if name in existing_servers or name in globals_:continue
        if repo is not None and 'command' in server:
            forwarded=server.get('env_vars',[])
            if not isinstance(forwarded,list) or not all(isinstance(v,str) for v in forwarded):
                raise HarnessError('Scoped stdio env_vars must be local variable names; preserve binding and reconcile')
            forwarded=list(dict.fromkeys([*forwarded,'XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS']))
            server={**server,'command':sys.executable,'args':[str(ROOT/'bin/harness'),'mcp-run',str(repo),'--server',name],
                    'env':{**server.get('env',{}),'HARNESS_STATE_DIR':str(state_dir())},'env_vars':forwarded}
        lines.append('[mcp_servers.'+json.dumps(name)+']')
        if 'default_tools_approval_mode' not in server:lines.append('default_tools_approval_mode = "prompt"')
        for field,value in server.items():
            if not isinstance(value,dict):lines.append(field+' = '+json_toml(value))
        for field,value in server.items():
            if isinstance(value,dict):
                lines.append('[mcp_servers.'+json.dumps(name)+'.'+field+']')
                lines.extend(json.dumps(k)+' = '+json_toml(v) for k,v in value.items())
        lines.append('')
    return '\n'.join(lines)

def project_config(existing,repo=None):
    """Place owned root settings before user tables; never shadow user policies."""
    data=tomllib.loads(existing) if existing else {}
    desired={'sandbox_mode':'read-only','approval_policy':'on-request','approvals_reviewer':'user'}
    if 'default_permissions' in data:
        if data['default_permissions']!=':read-only':raise HarnessError('Conflicting default_permissions; preserve config and review native read-only setup')
        desired.pop('sandbox_mode')
    root=[]
    for name,value in desired.items():
        if name in data and data[name]!=value:raise HarnessError('Conflicting native '+name+'; preserve config and confirm reconciliation')
        if name not in data:root.append(name+' = '+json_toml(value))
    home_config=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'config.toml'
    global_data=tomllib.loads(home_config.read_text()) if home_config.is_file() else {}
    profiles=[]
    for location,config in [('project',data),('global',global_data)]:
        profile=config.get('permissions',{}).get('harness-inspect')
        if profile is None:continue
        # Profiles merge across layers: unknown filesystem/network overrides
        # must not turn a profile labelled read-only into a writable policy.
        if (not isinstance(profile,dict) or profile.get('extends')!=':read-only'
                or set(profile)-{'extends','description'}):
            raise HarnessError('Conflicting '+location+' harness-inspect permission profile; preserve config and reconcile')
        profiles.append(profile)
    apps=data.get('apps',{}).get('_default',{})
    if apps.get('default_tools_approval_mode','prompt')!='prompt':raise HarnessError('Conflicting app approval defaults; preserve and reconcile')
    tables=mcp_fragment(existing,repo)
    if '_default' not in data.get('apps',{}):
        tables+='\n[apps._default]\ndefault_tools_approval_mode = "prompt"\napprovals_reviewer = "user"\n'
    elif 'default_tools_approval_mode' not in apps or apps.get('approvals_reviewer','user')!='user':
        raise HarnessError('Existing apps._default needs explicit native user-review policy; preserve and reconcile')
    if not profiles:
        tables+='\n[permissions.harness-inspect]\ndescription = "Harness 需求检查：只读"\nextends = ":read-only"\n'
    managed=merge_block(existing,tables)
    if root:managed=BEGIN+' PERMISSIONS\n'+'\n'.join(root)+'\n'+END+' PERMISSIONS\n'+managed
    tomllib.loads(managed)
    return managed

def verify_owned(record):
    for path,expected in record.get('owned',{}).items():
        if image(Path(path))!=expected:
            if path in record.get('managed_blocks',{}) and Path(path).is_file():
                text=Path(path).read_text()
                expected=record['managed_blocks'][path]
                expected={'':expected} if isinstance(expected,str) else expected
                if block_images(text)==expected:continue
            raise HarnessError('Managed file changed; preserve user changes and reconcile first: '+path)

def project_mutation_plan(repo,profile):
    codex_path=machine_binding().get('codex_binary','/usr/lib/chatgpt/resources/codex')
    if not Path(codex_path).is_file():raise HarnessError('Locked Codex binary unavailable: '+codex_path)
    plans=[]
    for p in [repo/'harness.yaml',repo/'harness.lock',repo/'.harness/state/project.json']:
        if p.exists() or p.is_symlink():raise HarnessError('Unknown existing project configuration: '+str(p))
    for name in ['AGENTS.md','.codex/config.toml']:
        p=repo/name
        if p.is_symlink():raise HarnessError('Refuse modifying a symlink configuration: '+str(p))
        text=p.read_text() if p.exists() else ''
        if BEGIN in text:raise HarnessError('Unowned Harness marker in '+str(p))
        if name=='AGENTS.md':
            body=(ROOT/'templates/project/AGENTS.harness.md').read_text()
        else:
            plans.append((p,project_config(text,repo).encode(),None));continue
        plans.append((p,merge_block(text,body).encode(),None))
    skills=[p for p in sorted((ROOT/'skills').glob('*/SKILL.md')) if p.parent.name!='harness-entry']
    if not skills:raise HarnessError('No prepared skills in release')
    for skill in skills:
        p=repo/'.agents/skills'/skill.parent.name
        if p.exists() or p.is_symlink():raise HarnessError('Unknown existing skill: '+str(p))
        plans.append((p,None,skill.parent))
    hooks=repo/'.codex/hooks.json'
    if profile=='L2':
        existing_hooks=inspect_registrations(repo)['HOOKS']
        if existing_hooks.get('pwf_plugin') or existing_hooks.get('pwf_standalone'):
            raise HarnessError('PWF already registered through plugin/standalone configuration; choose one project-owned hook source')
        if hooks.exists():raise HarnessError('Existing project hooks require explicit reconciliation; refusing duplicate ownership')
        global_hooks=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'hooks.json'
        if global_hooks.exists() and 'planning-with-files' in global_hooks.read_text():
            raise HarnessError('PWF already registered in global hooks')
        origin=ROOT/'vendor/planning-with-files/hooks/codex-hooks.json'
        if not origin.exists():raise HarnessError('Locked PWF Codex hooks unavailable')
        missing,errors=pwf_hook_problems(ROOT)
        if missing or errors:raise HarnessError('Locked PWF hook files unavailable or changed; prepare a complete release before enabling: '+str(missing+errors))
        hook_doc=json.loads(origin.read_text().replace('${PLUGIN_ROOT}',str(origin.parents[1])))
        command='HARNESS_STATE_DIR='+shlex.quote(str(state_dir()))+' python3 '+shlex.quote(str(ROOT/'scripts/context_hook.py'))
        for event in ['PreCompact','PostCompact','SessionStart']:
            hook_doc['hooks'].setdefault(event,[]).append({'matcher':'manual|auto' if event!='SessionStart' else 'startup|resume|compact',
                'hooks':[{'type':'command','command':command,'timeout':30,'statusMessage':'Harness context checkpoint and recovery'}]})
        plans.append((hooks,canonical(hook_doc)+b'\n',None))
    doc={'harness_root':str(ROOT),'state_root':str(state_dir()),'version':(ROOT/'VERSION').read_text().strip(),'release_hash':release_hash(),'profile':profile,'codex_binary':codex_path,'codex_launch_mode':machine_binding().get('codex_launch_mode','cli'),'enabled':now(),'defaults':defaults()}
    plans += [(repo/'harness.yaml',f"version: {doc['version']}\nprofile: {profile}\n".encode(),None),
              (repo/'harness.lock',canonical({'version':doc['version'],'release_hash':doc['release_hash']})+b'\n',None),
              (repo/'.harness/state/project.json',canonical(doc)+b'\n',None)]
    return doc,plans

def _enable(repo,profile):
    projects=registry(); key=str(repo); old=projects.get(key)
    if old:
        verify_owned(old)
        if old.get('profile')==profile and old.get('release_hash')==release_hash():
            return {'status':'ALREADY_ENABLED','repo':key,'profile':profile,'trust':'NEW_SESSION_REQUIRED'}
        raise HarnessError('Existing managed release/profile differs; disable then enable after reviewing plan')
    doc,plans=project_mutation_plan(repo,profile)
    with Transaction('Enable project '+key) as tx:
        for path,data,link in plans:tx.change(path,data=data,link=link)
        owned={str(p):image(p) for p,_,_ in plans}
        blocks={str(p):block_images(p.read_text()) for p,_,_ in plans if p.is_file() and p.name in {'AGENTS.md','config.toml'}}
        before={str(p):e['before'] for p,_,_ in plans for e in tx.doc['touch_set'] if e['path']==str(p)}
        projects[key]={'profile':profile,'release_hash':doc['release_hash'],'owned':owned,'managed_blocks':blocks,'before':before,'enable_transaction':tx.id}
        tx.change(state_dir()/'projects.json',canonical(projects)+b'\n',mode=0o600)
    return {'status':'ENABLED','repo':key,'transaction_id':tx.id,'profile':profile,'hooks':'PENDING_TRUST' if profile=='L2' else 'NOT_REGISTERED','session':'NEW_SESSION_REQUIRED'}

def enable(repo,profile):
    with lock(ROOT/'.harness-install/preparation.lock'):
        with lock(ROOT/'.harness-install/services.lock'):
            return _enable(repo,profile)


def disable(value):
    repo=registered_project(value)
    verify_binding_state(read_json(repo/'.harness/state/project.json',{}))
    projects=registry();record=projects.get(str(repo))
    if not record:return {'status':'ALREADY_DISABLED','repo':str(repo)}
    verify_owned(record)
    with Transaction('Disable project '+str(repo)) as tx:
        original=read_json(state_dir()/'transactions'/record['enable_transaction']/'journal.json')
        for entry in original['touch_set']:
            p=Path(entry['path'])
            if str(p) not in record['owned']:continue
            before=entry['before']
            if p.name=='AGENTS.md' or p==repo/'.codex/config.toml':
                # Restore exact original bytes, including missing trailing newlines.
                if image(p)==entry['after']:
                    if before['kind']=='file':tx.change(p,Path(entry['backup']).read_bytes(),before['mode'])
                    else:tx.change(p,delete=True)
                else:
                    remaining=strip_block(p.read_text()).encode()
                    tx.change(p,remaining,image(p)['mode'])
            elif before['kind']=='absent':tx.change(p,delete=True)
            else:raise HarnessError('Unexpected prior user ownership; refusing removal: '+str(p))
        del projects[str(repo)]
        tx.change(state_dir()/'projects.json',canonical(projects)+b'\n',mode=0o600)
    return {'status':'DISABLED','transaction_id':tx.id,'task_state':'PRESERVED'}

def pwf_hook_problems(root):
    missing=[];errors=[]
    descriptor=read_json(root/'manifests/source-trees.json',{}).get('planning-with-files',{})
    locked=read_json(root/'harness.lock',{}).get('sources',{}).get('planning-with-files')
    if locked:
        for field in ('source','commit'):
            if descriptor.get(field)!=locked.get(field):
                errors.append('Locked PWF source declaration differs from harness.lock: '+field)
    files=descriptor.get('files',{})
    if not isinstance(files,dict):return missing,['Invalid locked PWF source file declarations']
    definition='hooks/codex-hooks.json'
    origin=root/'vendor/planning-with-files'/definition
    # A pinned PWF source or native Hook references claim the complete runtime.
    # An empty definition in a minimal control-plane release claims no PWF code.
    native=(bool(locked or descriptor.get('commit') or descriptor.get('source')) or
            any(name.startswith('.codex/') for name in files) or
            origin.is_file() and not origin.is_symlink() and b'.codex/hooks/' in origin.read_bytes())
    required=set(PWF_CODEX_RUNTIME_FILES)|{definition} if native else ({definition} if origin.exists() or definition in files else set())
    for name in sorted(required):
        entry=files.get(name)
        relative=Path('vendor/planning-with-files')/name
        if not isinstance(entry,dict) or entry.get('kind')!='file' or not isinstance(entry.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):
            errors.append('Locked PWF hook declaration missing or invalid: '+str(relative));continue
        path=root/relative
        if not path.is_file():missing.append(str(relative))
        elif path.is_symlink() or entry.get('kind')!='file' or sha(path.read_bytes())!=entry.get('sha256'):
            errors.append('Locked PWF hook differs from source: '+str(relative))
    return missing,errors

def static_check(root=None):
    root = ROOT if root is None else Path(root)
    required=['VERSION','bin/harness','harness_core/cli.py','contracts/workflow.json','skills/harness-dev/SKILL.md','manifests/components.yaml','manifests/compatibility.yaml','manifests/licenses.yaml','manifests/ownership.yaml']
    missing=[p for p in required if not (root/p).is_file()]
    hook_missing,errors=pwf_hook_problems(root)
    missing.extend(hook_missing)
    cfg=read_json(root/'contracts/workflow.json')
    if cfg:
        ids=[n['id'] for n in cfg['nodes']]
        if len(ids)!=len(set(ids)):errors.append('Duplicate contract node IDs')
        unknown={e['target'] for n in cfg['nodes'] for e in n['edges']} - set(ids)-{'RESUME'}
        if unknown:errors.append('Unknown contract edges: '+str(sorted(unknown)))
    return {'status':'PASS' if not missing and not errors else 'FAIL','missing':missing,'errors':errors,'release_hash':release_hash(root),'scope':'STATIC_STRUCTURE_ONLY'}

def native_hook_status(repo,binary):
    script=ROOT/'scripts/codex_inventory.py'
    if not script.is_file():return {'status':'UNVERIFIED','reason':'codex_inventory.py unavailable'}
    with tempfile.TemporaryDirectory() as directory:
        output_path=Path(directory)/'inventory.json'
        home=os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))
        try:p=run([sys.executable,str(script),'--binary',binary,'--home',home,'--output',str(output_path),str(repo)],ok=False,timeout=45)
        except (OSError,subprocess.TimeoutExpired) as error:return {'status':'UNVERIFIED','reason':str(error)}
        if p.returncode!=0 or not output_path.is_file():
            return {'status':'UNVERIFIED','reason':'native hooks/list inventory failed','stderr':p.stderr.decode(errors='replace')[:1000]}
        data=read_json(output_path,{})
        entries=data.get('hooks',{}).get('data',[])
        entry=next((item for item in entries if item.get('cwd')==str(repo)),{})
        hooks=entry.get('hooks',[])
        project=[item for item in hooks if item.get('source')=='project']
        warnings=entry.get('warnings',[]);errors=entry.get('errors',[])
        untrusted=[item.get('key') for item in project if item.get('trustStatus')!='trusted' or not item.get('enabled')]
        status='FAIL' if errors else 'PASS' if project and not untrusted else 'PENDING_TRUST'
        return {'status':status,'native_discovery':'FAIL' if errors else 'PASS','project_handlers':len(project),
                'untrusted_or_disabled':untrusted,'warnings':warnings,'errors':errors,
                'reason':'Native hooks/list returned load errors' if errors else 'Native hooks/list trust discovery completed',
                'execution':'TRUST_DISCOVERY_ONLY; lifecycle execution requires separate smoke evidence'}

def doctor(repo=None,deep=False):
    binding=machine_binding();groups={'STATIC':static_check(),'RUNTIME':{'status':'UNVERIFIED'},'TRIGGER':{'status':'UNVERIFIED','reason':'Requires fresh Codex session with actual skill selection'},
        'MCP':{'status':'UNVERIFIED'},'HOOKS':{'status':'NOT_REGISTERED'},'SUBAGENT':{'status':'UNVERIFIED'},'REVIEW':{'status':'UNVERIFIED'},'RECOVERY':{'status':'UNVERIFIED'},'ISOLATION':{'status':'UNVERIFIED'},'OPTIONAL_PROVIDERS':{'status':'UNVERIFIED'}}
    inventory=inspect_registrations(repo)
    groups['HOOKS']=inventory['HOOKS']
    groups['MCP'].update(inventory['MCP'])
    if repo:
        doc=project_binding(repo)
        verify_owned(registry().get(str(repo),{}))
        groups['ISOLATION']={'status':'PASS','scope':'PROJECT_OVERLAY_OWNERSHIP','profile':doc['profile']}
        if doc['profile']=='L2' and groups['HOOKS']['status']!='FAIL':groups['HOOKS'].update(status='PENDING_TRUST',reason='Registration verified; native user trust and real lifecycle execution not verified')
    if deep:
        binary=binding.get('codex_binary','/usr/lib/chatgpt/resources/codex')
        p=run([binary,'--version'],ok=False)
        groups['RUNTIME']={'status':'PASS' if p.returncode==0 else 'FAIL','command':[binary,'--version'],'output':p.stdout.decode(errors='replace')[:1000]}
        results={}
        for name,argv in binding.get('component_commands',{}).items():
            if not isinstance(argv,list):continue
            try:
                p=run(argv,ok=False,timeout=45)
                results[name]={'status':'PASS' if p.returncode==0 else 'FAIL','command':argv,'returncode':p.returncode,'stdout':p.stdout.decode(errors='replace')[:1500],'stderr':p.stderr.decode(errors='replace')[:1000]}
            except (OSError,subprocess.TimeoutExpired) as e:results[name]={'status':'FAIL','error':str(e)}
        groups['MCP'].update(reason='Version commands do not verify MCP handshake/tool call',component_commands=results)
        if repo and project_binding(repo)['profile']=='L2' and groups['HOOKS'].get('status')!='FAIL':
            groups['HOOKS'].update(native_hook_status(repo,binary))
    # Real component smoke evidence is referenced separately; never turn static metadata into PASS.
    reports=sorted(str(p) for base in [ROOT/'reports',ROOT/'private/service-reports'] for p in base.glob('*smoke*.json'))
    return {'status':'FAIL' if any(v.get('status')=='FAIL' for v in groups.values()) else 'UNVERIFIED','repo':str(repo) if repo else None,'categories':groups,'component_smoke_reports':reports,'scope':'CLI doctor does not perform provider generation, trigger or independent-agent tests'}

def launcher_path():
    return Path(os.environ.get('HARNESS_BIN_DIR', str(Path.home()/'.local/bin'))).absolute()/'harness'

def launcher_bytes():
    from .rescue import launcher_fragment
    return ("#!/usr/bin/env python3\n"
        "# Managed Codex Engineering Harness V3 launcher\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "state=Path(os.environ.get('HARNESS_STATE_DIR',str(Path.home()/'.local/share/codex-engineering-harness')))\n"
        +launcher_fragment()+
        "try: root=Path(json.loads((state/'accepted.json').read_text())['root'])\n"
        "except (OSError,KeyError,ValueError): sys.exit('Harness has no accepted release; run the prepared release bin/harness install')\n"
        "program=root/'bin/harness'\n"
        "os.execv(sys.executable,[sys.executable,str(program),*sys.argv[1:]])\n").encode()

def entry_registration(root,record):
    target=Path(root)/'skills/harness-entry'
    if not (target/'SKILL.md').is_file():raise HarnessError('Prepared release has no natural-language Harness entry skill')
    path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).absolute()/'skills/harness-entry'
    prior=(record or {}).get('entry_skill',{})
    if path.exists() or path.is_symlink():
        if prior.get('path')!=str(path) or prior.get('image')!=image(path):
            raise HarnessError('Unowned or changed entry skill; preserve and reconcile: '+str(path))
    return path,target

def register_entry(tx,root,record):
    path,target=entry_registration(root,record)
    tx.change(path,link=target)
    return {'path':str(path),'image':image(path)}

def install():
    from . import installations
    check=static_check()
    if check['status']!='PASS':raise HarnessError('Release not ready: '+json.dumps(check))
    path=state_dir()/'accepted.json';current=read_json(path)
    wanted={'version':(ROOT/'VERSION').read_text().strip(),'root':str(ROOT),'release_hash':release_hash()}
    launch=launcher_path();ownership=state_dir()/'launcher.json'
    record=read_json(ownership)
    if launch.exists() or launch.is_symlink():
        if not record or str(launch)!=record.get('path') or image(launch)!=record.get('image'):
            raise HarnessError('Unowned or modified launcher; preserve and reconcile: '+str(launch))
    entry,target=entry_registration(ROOT,record)
    if current==wanted and record and launch.exists() and entry.is_symlink() and entry.resolve()==target.resolve() and str(ROOT) in installations.load()["roots"]:
        return {'status':'ALREADY_INSTALLED',**wanted,'launcher':str(launch),'entry_skill':str(entry)}
    if current and current!=wanted:raise HarnessError('Accepted release differs. Use update with an explicit candidate; no automatic overwrite')
    with Transaction('Install shared control plane and launcher') as tx:
        installations.register(tx,wanted,current)
        tx.change(path,canonical(wanted)+b'\n',mode=0o600)
        tx.change(launch,launcher_bytes(),mode=0o755)
        skill_record=register_entry(tx,ROOT,record)
        tx.change(ownership,canonical({'path':str(launch),'image':image(launch),'entry_skill':skill_record})+b'\n',mode=0o600)
    return {'status':'INSTALLED','transaction_id':tx.id,**wanted,'launcher':str(launch),'entry_skill':str(entry),
            'activation':'Explicit natural-language request; fresh native skill discovery required','components':'PREPARED_SEPARATELY_BY_BOOTSTRAP'}

def install_prepared():
    """One public command for complete preparation, retained data and registration."""
    from . import recovery, software, installations
    accepted=read_json(state_dir()/'accepted.json',{})
    if accepted and (accepted.get('root')!=str(ROOT) or accepted.get('release_hash')!=release_hash()):raise HarnessError('An accepted release exists or its source changed; use an isolated update candidate')
    from . import preflight
    prerequisites=preflight.require()
    with Transaction('Record component preparation before dependencies') as tx:
        installations.register_preparing(tx,{'root':str(ROOT),'version':(ROOT/'VERSION').read_text().strip(),'release_hash':release_hash()})
    software.recover_preparation(ROOT)
    restored=recovery.restore(ROOT)
    if restored['status']=='RECOVERY_PLANNED':restored=recovery.restore(ROOT,True)
    owned=software.verify(ROOT)
    full={'runtimes/node','runtimes/markitdown','runtimes/powercontext','runtimes/screenshot-to-code'}
    if not full<=set(owned['areas']):
        from . import preparation
        log=ROOT/'private/install-logs'/f'{new_id("prepare")}.log'
        from .service_reports import validate_output
        validate_output(log)
        log.parent.mkdir(parents=True,exist_ok=True)
        preparation.start(ROOT,log.stem)
        with log.open('x') as stream:
            log.chmod(0o600)
            process=subprocess.run([sys.executable,str(ROOT/'scripts/bootstrap.py'),'--services'],stdout=stream,stderr=subprocess.STDOUT,
                                   env={**os.environ,'HARNESS_PREPARATION_ID':log.stem})
        if process.returncode:raise HarnessError(preparation.failure(ROOT,log.stem)+' 已有文件和数据保留。诊断日志：'+str(log))
    installed=install()
    from . import memory_services
    remembered=memory_services.restore(ROOT,True)
    return {**installed,'retained_data':restored['status'],'retained_memory':remembered,
            'preflight':prerequisites,
            'preparation':'COMPLETE_COMPONENTS_REQUESTED; live capability acceptance is separate'}

def uninstall(removed_roots=None,retained_paths=None):
    from . import installations
    if registry():raise HarnessError('Disable enabled projects before uninstall')
    ownership=state_dir()/'launcher.json';record=read_json(ownership)
    if record and image(Path(record['path']))!=record['image']:
        raise HarnessError('Modified launcher; preserve user changes and reconcile before uninstall')
    entry=(record or {}).get('entry_skill')
    if entry and image(Path(entry['path']))!=entry['image']:raise HarnessError('Modified entry skill; preserve changes and reconcile before uninstall')
    with Transaction('Uninstall accepted registration and owned launcher') as tx:
        accepted=read_json(state_dir()/'accepted.json',{})
        installations.remove(tx,removed_roots or ([accepted['root']] if accepted else []),retained_paths)
        if record:tx.change(Path(record['path']),delete=True)
        if entry:tx.change(Path(entry['path']),delete=True)
        tx.change(ownership,delete=True)
        tx.change(state_dir()/'accepted.json',delete=True)
    return {'status':'UNINSTALLED','transaction_id':tx.id,'preserved':'Source, dependencies, data, reports, journals and backups'}

def update(source,promote=False,acceptance_plan=None,acceptance=None):
    from .acceptance import update_candidate
    return update_candidate(source,promote,acceptance_plan,acceptance)

def package(destination):
    destination=Path(destination).expanduser().absolute()
    missing,errors=pwf_hook_problems(ROOT)
    if missing or errors:raise HarnessError('Locked PWF hook files unavailable or changed: '+str(missing+errors))
    source_digest=release_hash()
    files=[]
    trees=read_json(ROOT/'manifests/source-trees.json',{})
    for p in sorted(ROOT.rglob('*')):
        relative=p.relative_to(ROOT)
        if not distribution_path(relative):continue
        if any(private_path(Path(*relative.parts[:i])) for i in range(1,len(relative.parts)+1)):continue
        if relative.parts[0] in {'state','data','.codex','.pytest_cache'}:continue
        if relative.parts[0]=='reports' and str(relative) not in {'reports/acceptance-summary.json','reports/known-limitations.md'}:continue
        if relative.parts[0]=='.agents' and str(relative)!='.agents/plugins/marketplace.json':continue
        if relative.parts[0]=='vendor':
            if len(relative.parts)<3:continue
            name=relative.parts[1];item=str(Path(*relative.parts[2:]))
            if item not in trees.get(name,{}).get('files',{}):continue
            known=trees[name]['files'][item]
            if p.is_symlink():
                target=os.readlink(p);link=Path(target)
                normalized=Path(os.path.normpath(str(relative.parent/link)))
                if link.is_absolute() or not normalized.parts or normalized.parts[0]!='vendor' or len(normalized.parts)<2 or normalized.parts[1]!=name:
                    raise HarnessError('Unsafe vendor symlink: '+str(relative))
                if known.get('kind')!='symlink' or known.get('target')!=target:raise HarnessError('Vendor symlink differs from locked source: '+str(relative))
                files.append(p);continue
            if not p.is_file() or sha(p.read_bytes())!=known.get('sha256'):raise HarnessError('Vendor file differs from locked source: '+str(relative))
        if p.is_symlink():raise HarnessError('Portable package refuses unknown symlink inputs: '+str(relative))
        if p.is_file():files.append(p)
    digest={str(p.relative_to(ROOT)):sha(os.readlink(p).encode()) if p.is_symlink() else sha(p.read_bytes()) for p in files}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        stage=Path(temporary)/'codex-engineering-harness';stage.mkdir()
        for p in files:
            out=stage/p.relative_to(ROOT);out.parent.mkdir(parents=True,exist_ok=True)
            if p.is_symlink():out.symlink_to(os.readlink(p))
            else:shutil.copy2(p,out)
        entries={str(p.relative_to(ROOT)):({'kind':'symlink','target':os.readlink(p),'sha256_target':digest[str(p.relative_to(ROOT))]} if p.is_symlink() else {'kind':'file','sha256':digest[str(p.relative_to(ROOT))]}) for p in files}
        for name,entry in entries.items():
            staged=stage/name
            if entry['kind']=='file' and sha(staged.read_bytes())!=entry['sha256']:raise HarnessError('File changed while staging package: '+name)
        summary_path=stage/'reports/acceptance-summary.json'
        supplied=read_json(summary_path,{})
        if not isinstance(supplied,dict) or not isinstance(supplied.get('categories',{}),dict):raise HarnessError('Acceptance summary must be an object with category objects')
        statuses={'PASS','FAIL','UNVERIFIED','PASS_WITH_OPTIONAL_BLOCK','PASS_WITH_SCOPE_LIMIT','PENDING_TRUST',
                  'OPTIONAL_NOT_CONFIGURED','OPTIONAL_NOT_CONFIGURED_FOR_MODEL_GENERATION','PASS_BY_CURRENT_DESKTOP_CAPABILITY'}
        if release_hash()!=source_digest:raise HarnessError('Source changed while packaging; retry a stable snapshot')
        bound=supplied.get('release_hash')==source_digest
        status=supplied.get('status','UNVERIFIED') if bound else 'UNVERIFIED'
        if not isinstance(status,str) or status not in statuses:status='UNVERIFIED'
        categories={}
        known={'STATIC','CORE_TESTS','DISABLE_PATH_RESOLUTION','RUNTIME','SKILLS','MCP','HOOKS','SUBAGENT','REVIEW',
               'RECOVERY','ISOLATION','PACKAGE_CLEAN_INSTALL','SCREENSHOT_NATIVE_ROUTE','SCREENSHOT_TO_CODE_PROVIDER','POWERCONTEXT_FULL_MEMORY'}
        for name,value in supplied.get('categories',{}).items():
            if name.upper() not in known:continue
            recorded=value.get('status','UNVERIFIED') if isinstance(value,dict) else value
            categories[name.upper()]={'status':recorded if bound and isinstance(recorded,str) and recorded in statuses else 'UNVERIFIED'}
        summary={'status':status,'version':(ROOT/'VERSION').read_text().strip(),'release_hash':source_digest,'categories':categories,
                 'source_binding':'MATCHED' if bound else 'MISSING_OR_STALE',
                 'scope':'Source-bound declared summary; actual validation evidence and machine acceptance remain separate'}
        write_json(summary_path,summary)
        entries['reports/acceptance-summary.json']={'kind':'file','sha256':sha(summary_path.read_bytes())}
        write_json(stage/'package-manifest.json',{'acceptance_summary':{'path':'reports/acceptance-summary.json','status':summary.get('status','UNVERIFIED')},'version':(ROOT/'VERSION').read_text().strip(),'created':now(),'files':entries,
            'hash_semantics':'file.sha256 hashes file bytes; symlink.sha256_target hashes literal UTF-8 target bytes; SHA256SUMS includes regular files only',
            'offline_install':'UNVERIFIED_REQUIRES_DEPENDENCY_DOWNLOADS'})
        atomic(stage/'SHA256SUMS',''.join(f"{entry['sha256']}  {name}\n" for name,entry in entries.items() if entry['kind']=='file').encode(),0o644)
        atomic(stage/'ACCEPTANCE.md',('This archive contains locked source and control utilities. Runtime installations, machine binding, secrets, task state and business data are excluded. Reinstallation and machine-specific acceptance are required.\nInstallation acceptance summary: reports/acceptance-summary.json (status: '+str(summary.get('status','UNVERIFIED'))+'). Any included reports/known-limitations.md records remaining verification or provider requirements. These installation results do not certify this archive on a new machine.\n').encode(),0o644)
        with tarfile.open(destination,'w:gz') as tar:tar.add(stage,arcname=stage.name)
    atomic(Path(str(destination)+'.sha256'),(sha(destination.read_bytes())+'  '+destination.name+'\n').encode(),0o644)
    return {'status':'PACKAGED','path':str(destination),'sha256':sha(destination.read_bytes()),'files':len(files),'offline_install':'UNVERIFIED'}

def session_environment():
    """Expose locked tool CLIs only to the new Codex process, after the user's PATH."""
    binding=machine_binding()
    defaults=[ROOT/'runtimes/node/node_modules/.bin',ROOT/'runtimes/markitdown/bin',ROOT/'runtimes/powercontext/client-venv/bin']
    configured=binding.get('tool_path_dirs',[])
    if not isinstance(configured,list) or not all(isinstance(p,str) and Path(p).is_absolute() for p in configured):
        raise HarnessError('machine-binding tool_path_dirs must be an array of absolute directories')
    environment=os.environ.copy()
    paths=environment.get('PATH','').split(os.pathsep) if environment.get('PATH') else []
    for directory in [*defaults,*map(Path,configured)]:
        value=str(directory)
        if directory.is_dir() and value not in paths:paths.append(value)
    environment['PATH']=os.pathsep.join(paths)
    return environment

def main():
    parser=argparse.ArgumentParser(description='Project-scoped Codex Engineering Harness V3; preserves unknown user configuration.')
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('install',help='登记已准备程序；--prepare 一次准备完整组件并恢复保留数据')
    p.add_argument('--prepare',action='store_true')
    for command in ['init','enable','refresh','disable','start','status','doctor']:
        p=subs.add_parser(command,help='Initialize Git if needed and enable the current project' if command=='init' else None)
        optional=command in {'init','enable','refresh','disable','start','status','doctor'}
        p.add_argument('repo',nargs='?' if optional else None,default='.' if command in {'init','enable','refresh','disable','start'} else None)
        if command in {'init','enable','refresh'}:p.add_argument('--profile',choices=['L0','L1','L2'],default=None if command=='refresh' else defaults()['profile'])
        if command=='doctor':p.add_argument('--deep',action='store_true')
        if command=='start':p.add_argument('--apply',action='store_true',help='Apply the explicitly approved first project preparation')
    p=subs.add_parser('prepare',help='Read-only project setup impact preview; apply only after scope approval')
    p.add_argument('repo',nargs='?',default='.');p.add_argument('--profile',choices=['L0','L1','L2'])
    p.add_argument('--apply',action='store_true')
    p=subs.add_parser('migrate',help='Preview/apply an owned project migration to the accepted root')
    p.add_argument('repo',nargs='?',default='.');p.add_argument('--from',dest='source_root',required=True)
    p.add_argument('--apply',action='store_true',help='Apply the explicitly approved migration')
    subs.add_parser('check',help='Read-only structural/release check')
    subs.add_parser('defaults',help='Show initialization, context and provider defaults')
    subs.add_parser('preflight',help='只读检查完整准备的环境条件；缺失项给出下一步，不读取登录凭据')
    p=subs.add_parser('mcp-run',help='启动已接入项目的受管stdio组件；由原生MCP配置调用')
    p.add_argument('repo');p.add_argument('--server',required=True)
    p=subs.add_parser('processes',help='受管组件进程状态；停止只处理本安装已登记的实例')
    p.add_argument('action',choices=['status','stop'])
    p=subs.add_parser('services',help='统一管理本安装的受管服务；停止保留数据卷')
    p.add_argument('action',choices=['status','start','stop']);p.add_argument('--service',choices=['all','powercontext','screenshot-to-code'],default='all')
    p=subs.add_parser('recover-data',help='重装后预览/恢复保留的任务和记忆数据；不恢复旧程序或信任')
    p.add_argument('--apply',action='store_true')
    p=subs.add_parser('recover-memory',help='用当前锁定组件恢复已卸载版本的记忆服务；保留原卷和绑定')
    p.add_argument('--apply',action='store_true');p.add_argument('--from',dest='origin_root')
    p=subs.add_parser('uninstall-recover',help='预览/恢复被中断的卸载；保留后来修改和数据')
    p.add_argument('operation_id');p.add_argument('--apply',action='store_true')
    p=subs.add_parser('context',help='Checkpoint, verify and hand off task state independently of chat');s=p.add_subparsers(dest='context_command',required=True)
    for command in ['status','checkpoint','verify','handoff','recall']:
        q=s.add_parser(command);q.add_argument('repo',nargs='?',default='.')
        if command=='status':q.add_argument('--used-percent',type=float);q.add_argument('--compactions',type=int,default=0)
        else:q.add_argument('--task',required=True)
        if command=='checkpoint':
            q.add_argument('--facts',required=True,help='Small JSON task summary; see harness-dev context reference');q.add_argument('--resume-node');q.add_argument('--session')
            q.add_argument('--plan-id',help='Bind .planning/<ID>; otherwise retain the verified checkpoint binding or use the task ID')
    p=subs.add_parser('update',help='Build/test isolated candidate, collect process acceptance and transactionally promote');p.add_argument('--to',required=True);p.add_argument('--promote',action='store_true');p.add_argument('--acceptance-plan');p.add_argument('--acceptance')
    p=subs.add_parser('rollback');p.add_argument('transaction_id')
    p=subs.add_parser('package');p.add_argument('--output',default=str(ROOT/'dist'/f"codex-engineering-harness-{(ROOT/'VERSION').read_text().strip()}.tar.gz"))
    p=subs.add_parser('uninstall',help='预览完整卸载：停止受管服务、解除接入、清理程序，保留任务与记忆')
    p.add_argument('--apply',action='store_true',help='执行已经确认的卸载范围')
    p=subs.add_parser('freeze');p.add_argument('repo');p.add_argument('--task',required=True);p.add_argument('--base',required=True);p.add_argument('--spec');p.add_argument('--checks');p.add_argument('--allowlist',help='JSON array of authorized task paths; overrides baseline ownership exclusions')
    p=subs.add_parser('review-run');p.add_argument('repo');p.add_argument('--task',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--kind',choices=['main','risk'],required=True);p.add_argument('reviewer_command',nargs='*')
    p=subs.add_parser('review-status');p.add_argument('repo');p.add_argument('--task',required=True);p.add_argument('--snapshot',required=True)
    p=subs.add_parser('task',help='Contract-validated task event journal and checkpoint recovery');s=p.add_subparsers(dest='task_command',required=True)
    for command in ['create','transition','pause','resume','status']:
        q=s.add_parser(command);q.add_argument('repo');q.add_argument('--id',required=True)
        if command=='create':q.add_argument('--risk',choices=['L0','L1','L2'],default='L1');q.add_argument('--intent',choices=['development','review_only','explanation','maintenance','recovery'],default='development')
        if command=='transition':q.add_argument('--to',required=True);q.add_argument('--reason',required=True);q.add_argument('--evidence');q.add_argument('--risk',choices=['L0','L1','L2'])
        if command=='pause':q.add_argument('--resume',required=True);q.add_argument('--reason',required=True)
    raw=sys.argv[1:];codex_args=[]
    if raw[:1]==['start'] and '--' in raw:
        delimiter=raw.index('--');codex_args=raw[delimiter+1:];raw=raw[:delimiter]
    args=parser.parse_args(raw)
    try:
        command=args.command
        if command=='install' and args.prepare:
            from . import preflight
            preflight.require()
        repo=None
        if getattr(args,'repo',None) and command not in {'init','start','prepare'}:
            if command=='disable':repo=args.repo
            elif command in {'enable','refresh','migrate','context','status','doctor'}:repo=select_project(args.repo)
            else:repo=repo_root(args.repo)
        if command=='mcp-run':
            from . import processes
            sys.exit(processes.launch(ROOT,repo,args.server))
        if command=='processes':
            from . import processes
            if args.action=='status':output(processes.plan(ROOT))
            else:
                with lock(ROOT/'.harness-install/services.lock'):output(processes.stop(ROOT))
            return
        if command=='start':
            preview=preparation_plan(args.repo)
            if preview['status']=='PREPARATION_REQUIRED' and not args.apply:
                output(preview);return
            repo,doc=start_project(args.repo,apply=args.apply)
            launch_mode=doc.get('codex_launch_mode') or machine_binding().get('codex_launch_mode','cli')
            if launch_mode=='desktop':
                if codex_args:raise HarnessError('Desktop launch mode does not accept Codex CLI arguments; open the reported repo as the primary folder in Codex Desktop')
                result={'status':'DESKTOP_PROJECT_READY','repo':str(repo),'profile':doc['profile'],'codex_binary':doc['codex_binary'],
                        'action':'Open this repo as the primary folder in Codex Desktop and start a new chat; project Skills, MCP and trusted Hooks load from this repo scope',
                        'automatic_gui_launch':'NOT_ATTEMPTED_NO_STABLE_PUBLIC_DESKTOP_CLI'}
            else:
                argv=[doc['codex_binary'],*codex_args]
                os.chdir(repo);os.execve(argv[0],argv,session_environment())
        if command=='prepare' and not args.apply:
            output(prepare_project(args.repo,args.profile));return
        if command=='uninstall' and not args.apply:
            from .lifecycle import uninstall as full_uninstall
            output(full_uninstall(ROOT));return
        if command=='uninstall-recover':
            from .lifecycle import recover_uninstall
            if args.apply:
                with lock(state_dir()/'operation.lock'):result=recover_uninstall(args.operation_id,True)
            else:result=recover_uninstall(args.operation_id)
        elif command=='recover-data':
            from .recovery import restore
            if args.apply:
                with lock(state_dir()/'operation.lock'):result=restore(ROOT,True)
            else:result=restore(ROOT)
        elif command=='preflight':
            from . import preflight
            result=preflight.inspect()
        elif command=='recover-memory':
            from . import memory_services
            if args.apply:
                with lock(state_dir()/'operation.lock'):result=memory_services.restore(ROOT,True,args.origin_root)
            else:result=memory_services.restore(ROOT,origin=args.origin_root)
        elif command=='services':
            from . import sidecars
            from . import memory_services
            if args.action=='status':
                result=sidecars.manage(ROOT,args.action,args.service)
                if args.service!='screenshot-to-code':result['retained_memory_services']=memory_services.manage(args.action)
            else:
                with lock(state_dir()/'operation.lock'):
                    if args.service!='screenshot-to-code':
                        if args.action=='start':memory_services.start_preflight()
                        else:memory_services.manage('status')
                    result=sidecars.manage(ROOT,args.action,args.service)
                    if args.service!='screenshot-to-code':result['retained_memory_services']=memory_services.manage(args.action)
        elif command in {'install','init','enable','prepare','refresh','migrate','disable','rollback','uninstall','update'}:
            with lock(state_dir()/'operation.lock'):
                if command=='install':result=install_prepared() if args.prepare else install()
                elif command=='init':result=initialize_project(args.repo,args.profile)
                elif command=='prepare':result=prepare_project(args.repo,args.profile,args.apply)
                elif command=='enable':result=enable(repo,args.profile)
                elif command=='refresh':result=refresh(repo,args.profile)
                elif command=='migrate':result=migrate(repo,args.source_root,args.apply)
                elif command=='disable':result=disable(repo)
                elif command=='rollback':result=rollback_operation(args.transaction_id)
                elif command=='update':result=update(args.to,args.promote,args.acceptance_plan,args.acceptance)
                else:
                    from .lifecycle import uninstall as full_uninstall
                    result=full_uninstall(ROOT,True)
        elif command=='check':result=static_check()
        elif command=='defaults':result=defaults()
        elif command=='context':
            project_binding(repo)
            if args.context_command=='status':result=context.guard(args.used_percent,args.compactions)
            elif args.context_command=='checkpoint':result=context.checkpoint(repo,args.task,read_json(Path(args.facts)),args.resume_node,args.session,plan_id=args.plan_id)
            elif args.context_command=='verify':result=context.verify(repo,args.task)
            elif args.context_command=='handoff':result=context.handoff(repo,args.task)
            else:result=memory.recall(repo,args.task)
        elif command=='status':result={'accepted':read_json(state_dir()/'accepted.json'),'projects':registry() if repo is None else registry().get(str(repo)), 'root':str(ROOT)}
        elif command=='doctor':result=doctor(repo,args.deep)
        elif command=='package':result=package(args.output)
        elif command=='freeze':result=review.freeze(repo,args.task,args.base,args.spec,args.checks,read_json(Path(args.allowlist)) if args.allowlist else None)
        elif command=='review-run':result=review.run_review(repo,args.task,args.snapshot,args.kind,args.reviewer_command[1:] if args.reviewer_command[:1]==['--'] else args.reviewer_command)
        elif command=='review-status':result=review.review_status(repo,args.task,args.snapshot)
        elif command=='task':
            if args.task_command=='create':result=workflow.create(repo,args.id,args.risk,args.intent)
            elif args.task_command=='transition':result=workflow.transition(repo,args.id,args.to,args.reason,args.evidence,args.risk)
            elif args.task_command=='pause':result=workflow.pause(repo,args.id,args.resume,args.reason)
            elif args.task_command=='resume':result=workflow.resume(repo,args.id)
            else:result=workflow.load(workflow.task_path(repo,args.id))
        output(result)
        if result.get('status') in {'FAIL','RESUME_CONFLICT','UNSYNCED','PREFLIGHT_BLOCKED'}:sys.exit(1)
    except (HarnessError,OSError,ValueError,subprocess.TimeoutExpired) as error:
        if args.command=='mcp-run':print(str(error),file=sys.stderr,flush=True)
        else:output({'status':'ERROR','error':str(error)})
        sys.exit(2)

if __name__=='__main__':main()
