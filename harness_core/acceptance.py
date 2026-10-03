"""Isolated release candidates, actual process receipts and atomic activation."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from .common import HarnessError, canonical, new_id, now, private_path, read_json, sha, state_dir, write_json
from .transaction import Transaction

REQUIRED={'STATIC','RUNTIME','TRIGGER','MCP','REVIEW','RECOVERY','ISOLATION'}


def execute(argv,candidate,env,category,timeout=60):
    if not isinstance(argv,list) or not argv or not all(isinstance(a,str) for a in argv):
        raise HarnessError('Acceptance checks require a nonempty argv array')
    command=[x.replace('${CANDIDATE}',str(candidate)).replace('${PYTHON}',sys.executable) for x in argv]
    started=now();run_id=new_id('acceptance-run')
    try:
        process=subprocess.Popen(command,cwd=candidate,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:stdout,stderr=process.communicate(timeout=min(int(timeout),120))
        except subprocess.TimeoutExpired:
            process.kill();stdout,stderr=process.communicate()
            raise HarnessError('Candidate process timed out: '+category)
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
                if private_path(item) or item.parts[0] in {'reports','.codex','state','data'} or n in {'candidate-report.json','candidate-acceptance.json'}:result.append(n);continue
                if item.parts[0]=='.agents' and str(item) not in {'.agents','.agents/plugins','.agents/plugins/marketplace.json'}:result.append(n);continue
                if item.parts[0]=='vendor' and len(item.parts)>=2:
                    files=trees.get(item.parts[1],{}).get('files',{})
                    path=str(Path(*item.parts[2:])) if len(item.parts)>2 else ''
                    if path and path not in files and not any(f.startswith(path+'/') for f in files):result.append(n)
            return result
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
            if binding:write_json(candidate/'machine-binding.json',binding)
            write_json(candidate/'candidate-report.json',{'candidate_id':identifier,'candidate':str(candidate),'status':'BUILDING','created':now()})
        except BaseException:
            shutil.rmtree(candidate,ignore_errors=True);raise
        report=read_json(candidate/'candidate-report.json')
    # Tests and commands use a separate control state/Codex home; old accepted stays untouched.
    isolated=candidate/'candidate-private';isolated.mkdir(exist_ok=True)
    env=os.environ.copy();env.update(HARNESS_STATE_DIR=str(isolated/'state'),HARNESS_BIN_DIR=str(isolated/'bin'),CODEX_HOME=str(isolated/'codex'),PLANNING_DISABLED='1')
    check=cli.static_check(candidate)
    tests=execute([sys.executable,'-m','unittest','discover','-s','tests'],candidate,env,'STATIC',120)
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
        needed=REQUIRED|({'HOOKS'} if binding.get('hooks') else set())
        if not needed<=categories:raise HarnessError('Acceptance plan missing required categories: '+str(sorted(needed-categories)))
        receipts=[tests]+[execute(c['command'],candidate,env,c['category'],c.get('timeout',60)) for c in checks]
        receipt={'candidate_id':identifier,'release_hash':release,'machine_binding_hash':binding_hash,'plan_sha256':sha(Path(acceptance_plan).read_bytes()),
                 'status':'PASS' if all(c['status']=='PASS' for c in receipts) else 'FAIL','created':now(),'receipts':receipts,
                 'required_categories':sorted(needed),'evidence_scope':'ACTUAL_ISOLATED_COMMAND_EXECUTION'}
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
    def stale_runtime(value):
        if isinstance(value,str):return str(old_root) in value and str(candidate) not in value
        if isinstance(value,dict):return any(stale_runtime(v) for v in value.values())
        if isinstance(value,list):return any(stale_runtime(v) for v in value)
        return False
    if stale_runtime(binding):raise HarnessError('Candidate binding still refers to accepted runtime paths; bootstrap isolated candidate dependencies before promotion')
    if cli.release_hash(candidate)!=release or sha(canonical(read_json(candidate/'machine-binding.json',{})))!=binding_hash:
        raise HarnessError('Candidate changed during acceptance; no accepted writes performed')
    if any(c.get('status')!='PASS' or not c.get('pid') or not c.get('run_id') for c in receipt['receipts']):
        raise HarnessError('Invalid actual process acceptance receipt')
    if not cli.launcher_path().exists():raise HarnessError('Install the managed launcher before promotion')
    launch=read_json(state_dir()/'launcher.json',{})
    from .transaction import image
    if launch.get('path')!=str(cli.launcher_path()) or image(cli.launcher_path())!=launch.get('image'):
        raise HarnessError('Managed launcher ownership changed; accepted release unchanged')
    wanted={'version':report['version'],'root':str(candidate),'release_hash':release}
    try:
        with Transaction('Promote accepted candidate '+identifier) as tx:
            tx.change(state_dir()/'accepted.json',canonical(wanted)+b'\n',mode=0o600)
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
