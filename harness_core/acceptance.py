"""Isolated release candidates, actual process receipts and atomic activation."""
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from .common import HarnessError, canonical, distribution_path, new_id, now, private_path, read_json, sha, state_dir, write_json
from .transaction import Transaction

STATIC_TEST_TIMEOUT=600
EXTERNAL_CHECK_TIMEOUT=120

REQUIRED={'STATIC','RUNTIME','TRIGGER','MCP','HOOKS','REVIEW','RECOVERY','ISOLATION'}


def verify_candidate_runtime(candidate,binding):
    """A copied source must not borrow another installation's prepared tools."""
    from . import software,sidecars
    candidate=Path(candidate).resolve()
    software.require_preparation_idle(candidate)
    owned=software.verify_all(candidate)
    if not owned.get('files'):raise HarnessError('Candidate dependencies are not prepared; bootstrap its owned runtimes before acceptance')
    required=[]
    if (candidate/'manifests/node/package-lock.json').is_file():required.append('runtimes/node')
    if (candidate/'manifests/markitdown-requirements.lock').is_file():required.append('runtimes/markitdown')
    for area in required:
        if not any(Path(area) in Path(name).parents for name in owned['files']):
            raise HarnessError('Candidate required runtime is not prepared: '+area)
    layout=sidecars.layout(candidate)
    expected_groups={g for g in sidecars.GROUPS if (candidate/'templates/services'/g/'compose.yaml').is_file()}
    if expected_groups and (not layout or not expected_groups<=set(layout['recipes'])):
        raise HarnessError('Candidate required sidecars are not prepared')
    urls=set()
    if layout:
        from . import resource_manager, resource_systemd
        gateway = resource_manager.descriptor_path(candidate).exists()
        base_urls=sidecars.require_live_binding(candidate, allow_dormant=True) if gateway else sidecars.require_live_binding(candidate)
        ports=layout['ports']
        if gateway:
            descriptor=resource_manager.load(candidate)
            if resource_systemd.status(candidate)['status']!='RUNNING':
                raise HarnessError('Candidate resource gateway is not running')
            for group in sidecars.GROUPS:
                current=resource_manager.control(candidate,'status',group)
                if current.get('state') not in {'DORMANT','RUNNING'} or current.get('active_calls')!=0:
                    raise HarnessError('Candidate resource gateway is busy or unhealthy')
            ports=descriptor['ports']
            urls.update('http://127.0.0.1:'+str(port) for port in ports.values())
        else:
            urls.update(base_urls)
        if 'powercontext' in layout['recipes']:
            urls.add('http://127.0.0.1:'+str(ports['powercontext'])+'/mcp/')
        from urllib.parse import urlparse
        for value in urls:
            try:
                parsed=urlparse(value)
                valid=(parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost'}
                       and parsed.port in ports.values() and not parsed.username and not parsed.password
                       and not parsed.query and not parsed.fragment)
            except (ValueError,TypeError):valid=False
            if not valid:
                raise HarnessError('Candidate binding references an unowned service URL')
    source_files=read_json(candidate/'.harness-install/receipt.json',{}).get('files',{})
    executable_files=set(owned['files'])|set(source_files)
    host_codex=binding.get('codex_binary');host_node=shutil.which('node')
    def local(value,exists=False):
        path=Path(value)
        return (path.is_absolute() and path.is_relative_to(candidate) and path.resolve().is_relative_to(candidate)
                and (not exists or path.is_file() and
                     (str(path.relative_to(candidate)) in executable_files or str(path.resolve().relative_to(candidate)) in executable_files)))
    def argument(value):
        part=value.split('=',1)[-1]
        if '://' in part:
            if part not in urls:raise HarnessError('Candidate binding references an unowned service URL')
        elif Path(part).is_absolute() and not local(part):
            raise HarnessError('Candidate binding references another runtime path')
        elif '..' in Path(part).parts:raise HarnessError('Candidate binding contains an escaping runtime argument')
    def command(name,argv):
        if not isinstance(argv,list) or not argv or not all(isinstance(v,str) for v in argv):raise HarnessError('Invalid candidate component command')
        program=argv[0]
        system_codex=name=='codex' and program==host_codex and Path(program).is_file()
        system_node=(name=='context-mode' and host_node and Path(program).resolve()==Path(host_node).resolve()
                     and len(argv)>1 and local(argv[1],True))
        if not (local(program,True) or system_codex or system_node):
            raise HarnessError('Candidate binding references another runtime path; bootstrap the isolated candidate')
        for arg in argv[1:]:argument(arg)
    for name,argv in binding.get('component_commands',{}).items():command(name,argv)
    for name,server in binding.get('mcp_servers',{}).items():
        if not isinstance(server,dict):raise HarnessError('Invalid candidate MCP binding')
        if 'command' in server:command(name,[server['command'],*server.get('args',[])])
        elif server.get('url') not in urls:raise HarnessError('Candidate MCP binding references an unowned service URL')
        for value in server.get('env',{}).values():
            if isinstance(value,str):argument(value)
    return owned


def runtime_fingerprint(candidate):
    from . import software,sidecars
    return sha(canonical({'software':read_json(software.snapshot_path(candidate),{}),
                          'services':sidecars.layout(candidate),
                          'binding':read_json(candidate/'services-binding.json',{}),
                          'resource_gateway':read_json(candidate/'.harness-install/resources.json',{}),
                          'resource_unit':read_json(candidate/'.harness-install/resource-unit.json',{})}))


def execute(argv,candidate,env,category,timeout=60,*,internal_static=False):
    if not isinstance(argv,list) or not argv or not all(isinstance(a,str) for a in argv):
        raise HarnessError('Acceptance checks require a nonempty argv array')
    expected_static=[sys.executable,'-m','unittest','discover','-s','tests']
    if internal_static and (category!='STATIC' or argv!=expected_static):
        raise HarnessError('Internal STATIC budget requires the mandatory test command')
    budget=STATIC_TEST_TIMEOUT if internal_static else EXTERNAL_CHECK_TIMEOUT
    try:budget=min(int(timeout),budget)
    except (ValueError,TypeError,OverflowError):raise HarnessError('Invalid acceptance timeout') from None
    if budget<=0:raise HarnessError('Acceptance timeout must be positive')
    command=[x.replace('${CANDIDATE}',str(candidate)).replace('${PYTHON}',sys.executable) for x in argv]
    started=now();run_id=new_id('acceptance-run')
    try:
        process=subprocess.Popen(command,cwd=candidate,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:stdout,stderr=process.communicate(timeout=budget)
        except BaseException as error:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.communicate()
            if isinstance(error,subprocess.TimeoutExpired):
                raise HarnessError('Candidate process timed out: '+category) from None
            raise
    except OSError as error:
        raise HarnessError('Candidate process unavailable: '+str(error))
    out=stdout.decode(errors='replace');failure=False
    try:
        result=json.loads(out)
        failure=isinstance(result,dict) and (result.get('status') in {'FAIL','ERROR','UNVERIFIED','BLOCKED','PENDING_TRUST'} or any(v.get('status')=='FAIL' for v in result.get('categories',{}).values()))
    except ValueError:result=None
    return {'run_id':run_id,'pid':process.pid,'category':category,'command':command,'cwd':str(candidate),
            'started':started,'finished':now(),'returncode':process.returncode,
            'status':'PASS' if process.returncode==0 and not failure else 'FAIL',
            'stdout':out[-12000:],'stderr':stderr.decode(errors='replace')[-6000:]}


def update_candidate(source,promote=False,acceptance_plan=None,acceptance=None):
    from . import cli
    source=Path(source).expanduser().resolve()
    if not source.is_dir() or not (source/'VERSION').exists():raise HarnessError('--to must name an explicit local release directory')
    missing,errors=cli.pwf_hook_problems(source)
    if missing or errors:raise HarnessError('Locked PWF hook files unavailable or changed: '+str(missing+errors))
    report=read_json(source/'candidate-report.json')
    if report:
        candidate=source;identifier=report['candidate_id']
        if candidate.parent!=(state_dir()/'candidates').resolve() or candidate.name!=identifier:
            raise HarnessError('Promotion only accepts an isolated candidate registered by this state root')
    else:
        identifier=new_id('candidate');candidate=state_dir()/'candidates'/identifier
        def ignored(directory,items):
            relative=Path(directory).relative_to(source)
            result=[]
            trees=read_json(source/'manifests/source-trees.json',{})
            for n in items:
                item=relative/n
                if not distribution_path(item) or item.parts[0] in {'reports','.codex','state','data'} or n in {'candidate-report.json','candidate-acceptance.json'}:result.append(n);continue
                if item.parts[0]=='.agents' and str(item) not in {'.agents','.agents/plugins','.agents/plugins/marketplace.json'}:result.append(n);continue
                if item.parts[0]=='vendor' and len(item.parts)>=2:
                    files=trees.get(item.parts[1],{}).get('files',{})
                    path=str(Path(*item.parts[2:])) if len(item.parts)>2 else ''
                    if path and path not in files and not any(f.startswith(path+'/') for f in files):result.append(n)
            return result
        if candidate.exists() or candidate.is_symlink():raise HarnessError('Candidate destination already exists; preserve unknown content')
        try:
            shutil.copytree(source,candidate,ignore=ignored,symlinks=True)
            trees=read_json(candidate/'manifests/source-trees.json',{})
            for p in candidate.rglob('*'):
                if not p.is_symlink():continue
                relative=p.relative_to(candidate);target=os.readlink(p);link=Path(target)
                normalized=Path(os.path.normpath(str(relative.parent/link)))
                if len(relative.parts)<3 or relative.parts[0]!='vendor' or link.is_absolute() or len(normalized.parts)<2 or normalized.parts[:2]!=relative.parts[:2]:
                    raise HarnessError('Unsafe candidate symlink: '+str(relative))
                known=trees.get(relative.parts[1],{}).get('files',{}).get(str(Path(*relative.parts[2:])),{})
                if known.get('kind')!='symlink' or known.get('target')!=target:raise HarnessError('Unowned candidate symlink: '+str(relative))
            binding=read_json(source/'machine-binding.json')
            # Keep only host Codex selection; dependency and service bindings
            # must be generated by preparation inside this new candidate.
            if binding:write_json(candidate/'machine-binding.json',{k:binding[k] for k in
                ['codex_binary','codex_version','codex_launch_mode'] if k in binding})
            write_json(candidate/'candidate-report.json',{'candidate_id':identifier,'candidate':str(candidate),'status':'BUILDING','created':now()})
            files={}
            for copied in candidate.rglob('*'):
                if not distribution_path(copied.relative_to(candidate)):continue
                if copied.is_symlink():files[str(copied.relative_to(candidate))]={'link':os.readlink(copied)}
                elif copied.is_file():files[str(copied.relative_to(candidate))]={'sha256':sha(copied.read_bytes())}
            write_json(candidate/'.harness-install/receipt.json',{'schema_version':1,'target':str(candidate),'state':str(state_dir()),
                       'origin':'COPIED_UPDATE_CANDIDATE','files':files})
            from . import installations
            with Transaction('Register isolated candidate source '+identifier) as tx:
                installations.register_candidate(tx,{'root':str(candidate),'version':(candidate/'VERSION').read_text().strip(),'release_hash':cli.release_hash(candidate)})
        except BaseException:
            shutil.rmtree(candidate,ignore_errors=True);raise
        report=read_json(candidate/'candidate-report.json')
    # Tests and commands use a separate control state/Codex home; old accepted stays untouched.
    isolated=candidate/'candidate-private';isolated.mkdir(exist_ok=True)
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',HARNESS_STATE_DIR=str(isolated/'state'),HARNESS_BIN_DIR=str(isolated/'bin'),CODEX_HOME=str(isolated/'codex'),PLANNING_DISABLED='1')
    check=cli.static_check(candidate)
    tests=execute([sys.executable,'-m','unittest','discover','-s','tests'],candidate,env,'STATIC',STATIC_TEST_TIMEOUT,internal_static=True)
    # unittest exits 0 for zero tests: never treat that as acceptance.
    if 'Ran 0 tests' in tests['stderr']:tests['status']='FAIL'
    prior=read_json(state_dir()/'accepted.json',{})
    old_root=Path(prior.get('root',str(cli.ROOT)))
    changes=[]
    candidate_skills={str(p.relative_to(candidate)):p for p in (candidate/'skills').rglob('*') if p.is_file() and not private_path(p.relative_to(candidate))}
    prior_skills={str(p.relative_to(old_root)):p for p in (old_root/'skills').rglob('*') if p.is_file() and not private_path(p.relative_to(old_root))}
    for relative in sorted(set(candidate_skills)|set(prior_skills)):
        old=prior_skills.get(relative);new=candidate_skills.get(relative)
        if not old or not new or old.read_bytes()!=new.read_bytes():
            changes.append({'path':relative,'before':sha(old.read_bytes()) if old else None,'after':sha(new.read_bytes()) if new else None})
    binding=read_json(candidate/'machine-binding.json',{})
    binding_hash=sha(canonical(binding));release=cli.release_hash(candidate)
    report.update(version=(candidate/'VERSION').read_text().strip(),release_hash=release,machine_binding_hash=binding_hash,check=check,tests=tests,
                  skill_behavior_changes=changes,permission_migration={'old_servers':sorted(read_json(old_root/'machine-binding.json',{}).get('mcp_servers',{})),
                  'candidate_servers':sorted(binding.get('mcp_servers',{})),'before_binding_hash':sha(canonical(read_json(old_root/'machine-binding.json',{}))),
                  'candidate_binding_hash':binding_hash,
                  'binding_changes':[{'server':name,'before':read_json(old_root/'machine-binding.json',{}).get('mcp_servers',{}).get(name),'after':binding.get('mcp_servers',{}).get(name)}
                    for name in sorted(set(read_json(old_root/'machine-binding.json',{}).get('mcp_servers',{}))|set(binding.get('mcp_servers',{})))
                    if read_json(old_root/'machine-binding.json',{}).get('mcp_servers',{}).get(name)!=binding.get('mcp_servers',{}).get(name)],
                  'automatic_database_migration':'NONE'},
                  status='CANDIDATE_READY' if check['status']=='PASS' and tests['status']=='PASS' else 'CANDIDATE_FAILED',
                  promotion='NOT_REQUESTED',isolation={'accepted_root_unchanged':prior,'private_state':str(isolated)},
                  limitations=['Runtime dependencies are prepared separately; no third-party auto-upgrade or database migration',
                               'Process evidence proves commands ran; native UI/session and hook trust require their own actual acceptance commands',
                               'Enabled projects remain pinned until separately migrated'])
    write_json(candidate/'candidate-report.json',report)
    if acceptance_plan:
        if report['status']!='CANDIDATE_READY':raise HarnessError('Candidate failed; accepted release unchanged')
        plan=read_json(Path(acceptance_plan))
        if not plan or plan.get('release_hash')!=release or plan.get('machine_binding_hash')!=binding_hash:
            raise HarnessError('Acceptance plan must bind exact candidate release_hash and machine_binding_hash')
        checks=plan.get('checks',[]);categories={c.get('category') for c in checks}
        needed=REQUIRED
        if not needed<=categories:raise HarnessError('Acceptance plan missing required categories: '+str(sorted(needed-categories)))
        verify_candidate_runtime(candidate,binding)
        runtime_hash=runtime_fingerprint(candidate)
        receipts=[tests]+[execute(c['command'],candidate,env,c['category'],c.get('timeout',60)) for c in checks]
        if runtime_fingerprint(candidate)!=runtime_hash:raise HarnessError('Candidate runtime changed during acceptance; repeat verification')
        receipt={'candidate_id':identifier,'release_hash':release,'machine_binding_hash':binding_hash,'plan_sha256':sha(Path(acceptance_plan).read_bytes()),
                 'status':'PASS' if all(c['status']=='PASS' for c in receipts) else 'FAIL','created':now(),'receipts':receipts,
                 'required_categories':sorted(needed),'runtime_fingerprint':runtime_hash,'evidence_scope':'ACTUAL_ISOLATED_COMMAND_EXECUTION'}
        acceptance_path=state_dir()/'candidate-acceptance'/f'{identifier}.json'
        write_json(acceptance_path,receipt)
        report['acceptance']={'path':str(acceptance_path),'sha256':sha(acceptance_path.read_bytes()),'status':receipt['status']}
        write_json(candidate/'candidate-report.json',report)
        acceptance=str(acceptance_path)
    if not promote:return report
    if report['status']!='CANDIDATE_READY':raise HarnessError('Candidate failed; accepted release unchanged')
    expected=state_dir()/'candidate-acceptance'/f'{identifier}.json'
    acceptance_path=Path(acceptance).resolve() if acceptance else expected.resolve()
    if acceptance_path!=expected.resolve():raise HarnessError('Promotion requires registered actual candidate acceptance receipt')
    receipt=read_json(acceptance_path)
    recorded=report.get('acceptance',{})
    if not receipt or receipt.get('status')!='PASS' or sha(acceptance_path.read_bytes())!=recorded.get('sha256'):
        raise HarnessError('Missing, failed or modified actual candidate acceptance; accepted release unchanged')
    if receipt.get('candidate_id')!=identifier or receipt.get('release_hash')!=release or receipt.get('machine_binding_hash')!=binding_hash:
        raise HarnessError('Acceptance is stale for this candidate; accepted release unchanged')
    if not binding:raise HarnessError('Candidate machine binding required for activation')
    if not REQUIRED<=set(receipt.get('required_categories',[])) or not REQUIRED<={r.get('category') for r in receipt.get('receipts',[])}:
        raise HarnessError('Candidate acceptance lacks current required categories, including HOOKS')
    verify_candidate_runtime(candidate,binding)
    def activate():
        if receipt.get('runtime_fingerprint')!=runtime_fingerprint(candidate):
            raise HarnessError('Candidate runtime changed after acceptance; repeat verification before promotion')
        verify_candidate_runtime(candidate,binding)
        if cli.release_hash(candidate)!=release or sha(canonical(read_json(candidate/'machine-binding.json',{})))!=binding_hash:
            raise HarnessError('Candidate changed during acceptance; no accepted writes performed')
        if any(c.get('status')!='PASS' or not c.get('pid') or not c.get('run_id') for c in receipt['receipts']):
            raise HarnessError('Invalid actual process acceptance receipt')
        # Portable source/binding hashes exclude mutable deployment and runtime
        # ownership. Recheck them before changing the accepted registration.
        from . import software, sidecars, processes
        software.verify_all(candidate)
        sidecars.layout(candidate)
        processes.require_idle(candidate)
        if not cli.launcher_path().exists():raise HarnessError('Install the managed launcher before promotion')
        launch=read_json(state_dir()/'launcher.json',{})
        from .transaction import image
        if launch.get('path')!=str(cli.launcher_path()) or image(cli.launcher_path())!=launch.get('image'):
            raise HarnessError('Managed launcher ownership changed; accepted release unchanged')
        cli.entry_registration(candidate,launch)
        wanted={'version':report['version'],'root':str(candidate),'release_hash':release}
        try:
            with Transaction('Promote accepted candidate '+identifier) as tx:
                from . import installations
                installations.register(tx,wanted,prior)
                tx.change(state_dir()/'accepted.json',canonical(wanted)+b'\n',mode=0o600)
                skill_record=cli.register_entry(tx,candidate,launch)
                tx.change(state_dir()/'launcher.json',canonical({**launch,'entry_skill':skill_record})+b'\n',mode=0o600)
                active_env=os.environ.copy();active_env['HARNESS_STATE_DIR']=str(state_dir())
                activated=execute([sys.executable,str(cli.launcher_path()),'doctor','--deep'],candidate,active_env,'ACTIVATION')
                if activated['returncode'] or (json.loads(activated['stdout']).get('status')=='FAIL'):
                    raise HarnessError('Promote activation doctor failed; touched accepted registration restored')
                if cli.release_hash(candidate)!=release or sha(canonical(read_json(candidate/'machine-binding.json',{})))!=binding_hash:
                    raise HarnessError('Candidate changed during activation; accepted registration restored')
                report.update(promotion='ACCEPTED',activation=activated,transaction_id=tx.id,status='PROMOTED')
                tx.change(candidate/'candidate-report.json',canonical(report)+b'\n',mode=0o600)
        except BaseException as error:
            report.update(promotion='ROLLED_BACK',status='ACTIVATION_FAILED',error=str(error))
            write_json(candidate/'candidate-report.json',report)
            raise
        return report

    from .common import lock
    with lock(candidate/'.harness-install/preparation.lock'):
        with lock(candidate/'.harness-install/services.lock'):
            return activate()
