import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from . import review, workflow
from .inventory import endpoint_identity, inspect_registrations
from .common import ROOT,HarnessError,atomic,canonical,git,lock,new_id,now,private_path,read_json,repo_root,run,sha,state_dir,write_json
from .transaction import Transaction,image,rollback

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

def registry():
    return read_json(state_dir()/'projects.json',{})

def project_binding(repo):
    value=read_json(repo/'.harness/state/project.json')
    if not value or value.get('harness_root')!=str(ROOT):
        raise HarnessError('Project is not enabled for this Harness root')
    return value

def strip_block(text):
    if text.count(BEGIN)!=text.count(END) or text.count(BEGIN)>1:
        raise HarnessError('Malformed/duplicate Harness markers')
    if BEGIN not in text:
        return text
    begin=text.index(BEGIN);end=text.index(END,begin)+len(END)
    if end<len(text) and text[end]=='\n':end+=1
    return text[:begin]+text[end:]

def merge_block(text,block):
    clean=strip_block(text)
    return clean+('' if not clean or clean.endswith('\n') else '\n')+BEGIN+'\n'+block.rstrip()+'\n'+END+'\n'

def json_toml(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,list):return '['+', '.join(json_toml(x) for x in value)+']'
    if isinstance(value,str):return json.dumps(value,ensure_ascii=False)
    raise HarnessError('Unsupported MCP TOML field type')

def mcp_fragment(existing):
    data=tomllib.loads(strip_block(existing)) if existing else {}
    existing_servers=data.get('mcp_servers',{})
    global_path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'config.toml'
    globals_=tomllib.loads(global_path.read_text()).get('mcp_servers',{}) if global_path.exists() else {}
    lines=[]
    for name,server in machine_binding().get('mcp_servers',{}).items():
        if not isinstance(server,dict):raise HarnessError('mcp_servers must map names to command tables')
        for location,servers in [('project',existing_servers),('global',globals_)]:
            if name in servers and servers[name]!=server:
                raise HarnessError(f'Conflicting {location} MCP server: {name}')
            for other,cfg in servers.items():
                if other!=name and endpoint_identity(cfg) is not None and endpoint_identity(cfg)==endpoint_identity(server):
                    raise HarnessError(f'Same MCP component already registered as {other} in {location}')
        if name in existing_servers or name in globals_:continue
        lines.append('[mcp_servers.'+json.dumps(name)+']')
        for field,value in server.items():
            if not isinstance(value,dict):lines.append(field+' = '+json_toml(value))
        for field,value in server.items():
            if isinstance(value,dict):
                lines.append('[mcp_servers.'+json.dumps(name)+'.'+field+']')
                lines.extend(json.dumps(k)+' = '+json_toml(v) for k,v in value.items())
        lines.append('')
    return '\n'.join(lines)

def verify_owned(record):
    for path,expected in record.get('owned',{}).items():
        if image(Path(path))!=expected:
            if path in record.get('managed_blocks',{}) and Path(path).is_file():
                text=Path(path).read_text()
                if text.count(BEGIN)==text.count(END)==1:
                    block=text[text.index(BEGIN):text.index(END)+len(END)]
                    if sha(block.encode())==record['managed_blocks'][path]:continue
            raise HarnessError('Managed file changed; preserve user changes and reconcile first: '+path)

def enable(repo,profile):
    projects=registry(); key=str(repo); old=projects.get(key)
    if old:
        verify_owned(old)
        if old.get('profile')==profile and old.get('release_hash')==release_hash():
            return {'status':'ALREADY_ENABLED','repo':key,'profile':profile,'trust':'NEW_SESSION_REQUIRED'}
        raise HarnessError('Existing managed release/profile differs; disable then enable after reviewing plan')
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
        else:body=mcp_fragment(text)
        plans.append((p,merge_block(text,body).encode(),None))
    skills=list(sorted((ROOT/'skills').glob('*/SKILL.md')))
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
        body=origin.read_text().replace('${PLUGIN_ROOT}',str(origin.parents[1]))
        json.loads(body)
        plans.append((hooks,body.encode(),None))
    doc={'harness_root':str(ROOT),'version':(ROOT/'VERSION').read_text().strip(),'release_hash':release_hash(),'profile':profile,'codex_binary':codex_path,'enabled':now()}
    plans += [(repo/'harness.yaml',f'version: 3.0.0\nprofile: {profile}\n'.encode(),None),
              (repo/'harness.lock',canonical({'version':doc['version'],'release_hash':doc['release_hash']})+b'\n',None),
              (repo/'.harness/state/project.json',canonical(doc)+b'\n',None)]
    with Transaction('Enable project '+key) as tx:
        for path,data,link in plans:tx.change(path,data=data,link=link)
        owned={str(p):image(p) for p,_,_ in plans}
        blocks={str(p):sha(p.read_text()[p.read_text().index(BEGIN):p.read_text().index(END)+len(END)].encode()) for p,_,_ in plans if p.is_file() and p.name in {'AGENTS.md','config.toml'}}
        before={str(p):e['before'] for p,_,_ in plans for e in tx.doc['touch_set'] if e['path']==str(p)}
        projects[key]={'profile':profile,'release_hash':doc['release_hash'],'owned':owned,'managed_blocks':blocks,'before':before,'enable_transaction':tx.id}
        tx.change(state_dir()/'projects.json',canonical(projects)+b'\n',mode=0o600)
    return {'status':'ENABLED','repo':key,'transaction_id':tx.id,'profile':profile,'hooks':'PENDING_TRUST' if profile=='L2' else 'NOT_REGISTERED','session':'NEW_SESSION_REQUIRED'}

def disable(repo):
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

def static_check(root=None):
    root = ROOT if root is None else Path(root)
    required=['VERSION','bin/harness','harness_core/cli.py','contracts/workflow.json','skills/harness-dev/SKILL.md','manifests/components.yaml','manifests/compatibility.yaml','manifests/licenses.yaml','manifests/ownership.yaml']
    missing=[p for p in required if not (root/p).is_file()]
    errors=[]
    cfg=read_json(root/'contracts/workflow.json')
    if cfg:
        ids=[n['id'] for n in cfg['nodes']]
        if len(ids)!=len(set(ids)):errors.append('Duplicate contract node IDs')
        unknown={e['target'] for n in cfg['nodes'] for e in n['edges']} - set(ids)-{'RESUME'}
        if unknown:errors.append('Unknown contract edges: '+str(sorted(unknown)))
    return {'status':'PASS' if not missing and not errors else 'FAIL','missing':missing,'errors':errors,'release_hash':release_hash(root),'scope':'STATIC_STRUCTURE_ONLY'}

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
    # Real component smoke evidence is referenced separately; never turn static metadata into PASS.
    reports=sorted(str(p) for p in (ROOT/'reports').glob('*smoke*.json'))
    return {'status':'FAIL' if any(v.get('status')=='FAIL' for v in groups.values()) else 'UNVERIFIED','repo':str(repo) if repo else None,'categories':groups,'component_smoke_reports':reports,'scope':'CLI doctor does not perform provider generation, trigger or independent-agent tests'}

def launcher_path():
    return Path(os.environ.get('HARNESS_BIN_DIR', str(Path.home()/'.local/bin'))).absolute()/'harness'

def launcher_bytes():
    return ("#!/usr/bin/env python3\n"
        "# Managed Codex Engineering Harness V3 launcher\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "state=Path(os.environ.get('HARNESS_STATE_DIR',str(Path.home()/'.local/share/codex-engineering-harness')))\n"
        "try: root=Path(json.loads((state/'accepted.json').read_text())['root'])\n"
        "except (OSError,KeyError,ValueError): sys.exit('Harness has no accepted release; run the prepared release bin/harness install')\n"
        "program=root/'bin/harness'\n"
        "os.execv(sys.executable,[sys.executable,str(program),*sys.argv[1:]])\n").encode()

def install():
    check=static_check()
    if check['status']!='PASS':raise HarnessError('Release not ready: '+json.dumps(check))
    path=state_dir()/'accepted.json';current=read_json(path)
    wanted={'version':(ROOT/'VERSION').read_text().strip(),'root':str(ROOT),'release_hash':release_hash()}
    launch=launcher_path();ownership=state_dir()/'launcher.json'
    record=read_json(ownership)
    if launch.exists() or launch.is_symlink():
        if not record or str(launch)!=record.get('path') or image(launch)!=record.get('image'):
            raise HarnessError('Unowned or modified launcher; preserve and reconcile: '+str(launch))
    if current==wanted and record and launch.exists():return {'status':'ALREADY_INSTALLED',**wanted,'launcher':str(launch)}
    if current and current!=wanted:raise HarnessError('Accepted release differs. Use update with an explicit candidate; no automatic overwrite')
    with Transaction('Install shared control plane and launcher') as tx:
        tx.change(path,canonical(wanted)+b'\n',mode=0o600)
        tx.change(launch,launcher_bytes(),mode=0o755)
        tx.change(ownership,canonical({'path':str(launch),'image':image(launch)})+b'\n',mode=0o600)
    return {'status':'INSTALLED','transaction_id':tx.id,**wanted,'launcher':str(launch),'components':'PREPARED_SEPARATELY_BY_BOOTSTRAP'}

def uninstall():
    if registry():raise HarnessError('Disable enabled projects before uninstall')
    ownership=state_dir()/'launcher.json';record=read_json(ownership)
    if record and image(Path(record['path']))!=record['image']:
        raise HarnessError('Modified launcher; preserve user changes and reconcile before uninstall')
    with Transaction('Uninstall accepted registration and owned launcher') as tx:
        if record:tx.change(Path(record['path']),delete=True)
        tx.change(ownership,delete=True)
        tx.change(state_dir()/'accepted.json',delete=True)
    return {'status':'UNINSTALLED','transaction_id':tx.id,'preserved':'Source, dependencies, data, reports, journals and backups'}

def update(source,promote=False,acceptance_plan=None,acceptance=None):
    from .acceptance import update_candidate
    return update_candidate(source,promote,acceptance_plan,acceptance)

def package(destination):
    destination=Path(destination).expanduser().absolute()
    files=[]
    trees=read_json(ROOT/'manifests/source-trees.json',{})
    for p in sorted(ROOT.rglob('*')):
        relative=p.relative_to(ROOT)
        if any(private_path(Path(*relative.parts[:i])) for i in range(1,len(relative.parts)+1)):continue
        if relative.parts[0] in {'state','data','.codex'}:continue
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
        summary_path=stage/'reports/acceptance-summary.json'
        if not summary_path.exists():
            write_json(summary_path,{'status':'UNVERIFIED','reason':'No sanitized installation acceptance summary was supplied to packaging'})
            entries['reports/acceptance-summary.json']={'kind':'file','sha256':sha(summary_path.read_bytes())}
        summary=read_json(summary_path,{})
        write_json(stage/'package-manifest.json',{'acceptance_summary':{'path':'reports/acceptance-summary.json','status':summary.get('status','UNVERIFIED')},'version':(ROOT/'VERSION').read_text().strip(),'created':now(),'files':entries,
            'hash_semantics':'file.sha256 hashes file bytes; symlink.sha256_target hashes literal UTF-8 target bytes; SHA256SUMS includes regular files only',
            'offline_install':'UNVERIFIED_REQUIRES_DEPENDENCY_DOWNLOADS'})
        atomic(stage/'SHA256SUMS',''.join(f"{entry['sha256']}  {name}\n" for name,entry in entries.items() if entry['kind']=='file').encode(),0o644)
        atomic(stage/'ACCEPTANCE.md',('This archive contains locked source and control utilities. Runtime installations, machine binding, secrets, task state and business data are excluded. Reinstallation and machine-specific acceptance are required.\nInstallation acceptance summary: reports/acceptance-summary.json (status: '+str(summary.get('status','UNVERIFIED'))+'). Any included reports/known-limitations.md records remaining verification or provider requirements. These installation results do not certify this archive on a new machine.\n').encode(),0o644)
        with tarfile.open(destination,'w:gz') as tar:tar.add(stage,arcname=stage.name)
    atomic(Path(str(destination)+'.sha256'),(sha(destination.read_bytes())+'  '+destination.name+'\n').encode(),0o644)
    return {'status':'PACKAGED','path':str(destination),'sha256':sha(destination.read_bytes()),'files':len(files),'offline_install':'UNVERIFIED'}

def main():
    parser=argparse.ArgumentParser(description='Project-scoped Codex Engineering Harness V3; preserves unknown user configuration.')
    subs=parser.add_subparsers(dest='command',required=True)
    subs.add_parser('install',help='Register prepared locked control plane; idempotent')
    for command in ['enable','disable','start','status','doctor']:
        p=subs.add_parser(command);p.add_argument('repo',nargs='?' if command in {'status','doctor'} else None)
        if command=='enable':p.add_argument('--profile',choices=['L0','L1','L2'],default='L1')
        if command=='doctor':p.add_argument('--deep',action='store_true')
        if command=='start':p.add_argument('codex_args',nargs=argparse.REMAINDER)
    subs.add_parser('check',help='Read-only structural/release check')
    p=subs.add_parser('update',help='Build/test isolated candidate, collect process acceptance and transactionally promote');p.add_argument('--to',required=True);p.add_argument('--promote',action='store_true');p.add_argument('--acceptance-plan');p.add_argument('--acceptance')
    p=subs.add_parser('rollback');p.add_argument('transaction_id')
    p=subs.add_parser('package');p.add_argument('--output',default=str(ROOT/'dist/codex-engineering-harness-3.0.0.tar.gz'))
    subs.add_parser('uninstall',help='Remove accepted registration only; enabled projects must be disabled first; preserve source/dependencies/backups')
    p=subs.add_parser('freeze');p.add_argument('repo');p.add_argument('--task',required=True);p.add_argument('--base',required=True);p.add_argument('--spec');p.add_argument('--checks');p.add_argument('--allowlist',help='JSON array of authorized task paths; overrides baseline ownership exclusions')
    p=subs.add_parser('review-run');p.add_argument('repo');p.add_argument('--task',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--kind',choices=['main','risk'],required=True);p.add_argument('reviewer_command',nargs='*')
    p=subs.add_parser('review-status');p.add_argument('repo');p.add_argument('--task',required=True);p.add_argument('--snapshot',required=True)
    p=subs.add_parser('task',help='Contract-validated task event journal and checkpoint recovery');s=p.add_subparsers(dest='task_command',required=True)
    for command in ['create','transition','pause','resume','status']:
        q=s.add_parser(command);q.add_argument('repo');q.add_argument('--id',required=True)
        if command=='create':q.add_argument('--risk',choices=['L0','L1','L2'],default='L1');q.add_argument('--intent',choices=['development','review_only','explanation','maintenance','recovery'],default='development')
        if command=='transition':q.add_argument('--to',required=True);q.add_argument('--reason',required=True);q.add_argument('--evidence');q.add_argument('--risk',choices=['L0','L1','L2'])
        if command=='pause':q.add_argument('--resume',required=True);q.add_argument('--reason',required=True)
    args=parser.parse_args()
    try:
        repo=repo_root(args.repo) if getattr(args,'repo',None) else None
        command=args.command
        if command=='start':
            doc=project_binding(repo);verify_owned(registry().get(str(repo),{}))
            argv=[doc['codex_binary'],*(args.codex_args[1:] if args.codex_args[:1]==['--'] else args.codex_args)]
            os.chdir(repo);os.execv(argv[0],argv)
        if command in {'install','enable','disable','rollback','uninstall','update'}:
            with lock(state_dir()/'operation.lock'):
                if command=='install':result=install()
                elif command=='enable':result=enable(repo,args.profile)
                elif command=='disable':result=disable(repo)
                elif command=='rollback':result=rollback(args.transaction_id)
                elif command=='update':result=update(args.to,args.promote,args.acceptance_plan,args.acceptance)
                else:result=uninstall()
        elif command=='check':result=static_check()
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
        if result.get('status')=='FAIL':sys.exit(1)
    except (HarnessError,OSError,ValueError,subprocess.TimeoutExpired) as error:
        output({'status':'ERROR','error':str(error)})
        sys.exit(2)

if __name__=='__main__':main()
