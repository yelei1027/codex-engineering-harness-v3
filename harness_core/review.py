import json
import os
import stat
import shutil
import subprocess
from pathlib import Path
from .common import HarnessError, atomic, canonical, empty_tree, git, git_head, new_id, now, private_path, read_json, safe_id, sha, write_json
from .transaction import safe_parent


def names(data):
    return [os.fsdecode(x) for x in data.split(b'\0') if x]


def selected(repo, base):
    head = git_head(repo)
    tree = empty_tree(repo)
    fixed = tree if base == tree else git(repo, 'merge-base', base, head).decode().strip()
    committed=names(git(repo,'diff','--name-only','--no-renames','-z',fixed,head))
    workspace=names(git(repo,'diff','--name-only','--no-renames','-z',head))
    staged=names(git(repo,'diff','--cached','--name-only','--no-renames','-z',head))
    untracked=names(git(repo,'ls-files','--others','--exclude-standard','-z'))
    tracked=names(git(repo,'ls-files','-z'))
    changed=set(committed+workspace+staged+untracked)
    return head,fixed,changed,untracked,sorted(set(tracked+untracked)|changed)


def file_image(repo, relative):
    p = repo/relative
    for parent in p.parents:
        if parent==repo:break
        if parent.is_symlink():raise HarnessError('Refusing symlink parent in evidence scope: '+str(parent))
    if p.is_symlink():
        return {'path':relative, 'kind':'symlink', 'target':os.readlink(p), 'skip_reason':'SYMLINK_NOT_DEREFERENCED'}
    if not p.exists():
        return {'path':relative, 'kind':'deleted'}
    st = p.lstat()
    if stat.S_ISDIR(st.st_mode):
        return {'path':relative, 'kind':'directory', 'skip_reason':'SUBMODULE_OR_DIRECTORY_NOT_TRAVERSED',
                'gitlink':git(repo,'ls-files','--stage','--',relative).decode(errors='replace').strip()}
    if not stat.S_ISREG(st.st_mode):
        return {'path':relative, 'kind':'special', 'skip_reason':'SPECIAL_FILE_NOT_OPENED'}
    data = p.read_bytes()
    out = {'path':relative, 'kind':'file', 'mode':stat.S_IMODE(st.st_mode), 'sha256':sha(data), 'size':len(data)}
    if b'\0' in data[:8192]:out['skip_reason'] = 'BINARY_REQUIRES_SPECIALIST_REVIEW'
    return out


def pathspec(paths):
    # Positive literal selection is identical for all patches and source images.
    # Never rely on a second partial secret-pattern allow/deny implementation.
    return [':(literal)'+p for p in sorted(paths)]


def patch_bytes(repo,args,paths):
    return git(repo,'diff','--binary','--no-renames',*args,'--',*pathspec(paths)) if paths else b''


def fingerprint(repo, base, paths=None):
    head,fixed,changed,untracked,all_paths=selected(repo,base)
    paths=sorted(p for p in (all_paths if paths is None else paths) if not private_path(p))
    images=[file_image(repo,p) for p in paths]
    value={'head':head,'base':fixed,'files':images,
           'index':sha(patch_bytes(repo,['--cached',head],paths)),
           'tracked':sha(patch_bytes(repo,[head],paths))}
    return sha(canonical(value)),value


def validate_allowlist(values):
    if not isinstance(values,list) or not all(isinstance(x,str) for x in values):raise HarnessError('allowlist must be a JSON array of relative file paths')
    for p in values:
        path=Path(p)
        if path.is_absolute() or '..' in path.parts or not p or p=='.':raise HarnessError('Unsafe allowlist path: '+p)
        if private_path(p):raise HarnessError('Private/derived path cannot be authorized into evidence: '+p)
    return sorted(set(values))


def freeze(repo, task, base, spec=None, checks=None, allowlist=None):
    safe_id(task)
    head,fixed,changed,untracked,all_paths=selected(repo,base)
    state=read_json(repo/'.harness/task'/task/'state.json',{})
    baseline=set(state.get('baseline_changes',[]))
    if allowlist is not None:
        allowed=set(validate_allowlist(allowlist));reviewable=changed&allowed
        # Explicit ownership overrides preexisting ownership only for listed files.
        paths=sorted(allowed)
        excluded=[{'path':p,'reason':'OUTSIDE_TASK_ALLOWLIST'} for p in all_paths if p not in allowed]
    else:
        reviewable=changed-baseline
        # Clean tracked context is retained, unrelated preexisting or untracked dirt is omitted.
        paths=sorted(set(all_paths)-baseline)
        excluded=[{'path':p,'reason':'PREEXISTING_USER_CHANGE_NOT_AUTHORIZED'} for p in sorted(baseline)]
    excluded += [{'path':p,'reason':'PRIVATE_OR_DERIVED_DATA_EXCLUDED'} for p in paths if private_path(p)]
    paths=[p for p in paths if not private_path(p)]
    reviewable=sorted(p for p in reviewable if not private_path(p))
    # Capture relevant source images and Git metadata before any evidence writes.
    original,version=fingerprint(repo,fixed,paths)
    snapshot=new_id('snapshot');destination=repo/'.harness-artifacts/review'/task/snapshot
    temporary=destination.with_name('.'+snapshot);temporary.mkdir(parents=True)
    try:
        for name,args in [('committed.patch',[fixed,head]),('tracked.patch',[head]),('index.patch',['--cached',head])]:
            atomic(temporary/name,patch_bytes(repo,args,reviewable))
        entries=[]
        for p in paths:
            info=file_image(repo,p);entries.append(info)
            if info['kind']=='file':
                data=(repo/p).read_bytes()
                if sha(data)!=info['sha256']:raise HarnessError('Source changed during freeze: '+p)
                atomic(temporary/'source'/p,data,0o400)
        write_json(temporary/'untracked-manifest.json',{'files':[e for e in entries if e['path'] in untracked],'excluded':excluded})
        for name,provided in [('spec-ref.json',spec),('checks.json',checks)]:
            if provided:
                evidence_path=Path(provided).absolute();safe_parent(evidence_path)
                if evidence_path.is_symlink():raise HarnessError('Symlink cannot be used as spec/check evidence')
                path=evidence_path.resolve()
                classified=path.relative_to(repo) if repo in path.parents else path
                if private_path(classified):raise HarnessError('Private file cannot be used as spec/check evidence')
                data=path.read_bytes();atomic(temporary/('spec-source' if name.startswith('spec') else 'checks-source.json'),data,0o400)
                write_json(temporary/name,{'source':str(path),'sha256':sha(data),'status':'PROVIDED_NOT_INDEPENDENTLY_VERIFIED'})
            else:write_json(temporary/name,{'status':'UNVERIFIED','reason':'No evidence supplied'})
        atomic(temporary/'codegraph-summary.md',b'UNVERIFIED: no CodeGraph query evidence supplied by freeze.\n')
        write_json(temporary/'target.json',{'snapshot_id':snapshot,'task_id':task,'repo':str(repo),'created':now(),
             'base_requested':base,'merge_base':fixed,'head':head,'fingerprint':original,'content_fingerprint':sha(canonical(version['files'])),
             'frozen_paths':paths,'reviewable_paths':reviewable,'files':entries,'excluded':excluded,'preexisting_changes':sorted(baseline),
             'ownership':'EXPLICIT_ALLOWLIST' if allowlist is not None else 'TASK_BASELINE_EXCLUDED' if state else 'NO_TASK_BASELINE_AVAILABLE',
             'risk':state.get('risk','L1'),'scope':'task-owned committed + staged + unstaged + untracked; immutable final source copy'})
        if fingerprint(repo,fixed,paths)[0]!=original:raise HarnessError('Worktree/index/head changed during freeze; retry from a stable version')
        inputs={str(p.relative_to(temporary)):sha(p.read_bytes()) for p in sorted(temporary.rglob('*')) if p.is_file()}
        write_json(temporary/'input-manifest.json',{'files':inputs,'input_hash':sha(canonical(inputs)),'immutable':True})
        (temporary/'review-results').mkdir()
        for p in temporary.rglob('*'):
            if p.is_file():p.chmod(0o400)
        temporary.rename(destination)
    except BaseException:shutil.rmtree(temporary,ignore_errors=True);raise
    return {'snapshot_id':snapshot,'path':str(destination),'input_hash':sha(canonical(inputs)),'review':'UNVERIFIED','ownership':state.get('baseline_changes',[])}


def coverage(report,paths):
    records=report.get('files',[])
    if not isinstance(records,list):return list(paths),['INVALID_FILES_RECORD']
    mapped={r.get('path'):r for r in records if isinstance(r,dict)}
    invalid=[r.get('path') for r in records if isinstance(r,dict) and r.get('path') not in paths]
    if len(mapped)!=len(records):invalid.append('DUPLICATE_OR_INVALID_FILE_RECORD')
    pending=[p for p in paths if p not in mapped or mapped[p].get('status') not in {'reviewed','skipped'} or (mapped[p].get('status')=='skipped' and not mapped[p].get('reason','').strip())]
    return pending,invalid


def run_review(repo,task,snapshot,kind,command):
    """Run a real child process on frozen source; bind output and receipt to the inputs."""
    path=repo/'.harness-artifacts/review'/safe_id(task)/safe_id(snapshot)
    status=review_status(repo,task,snapshot)
    if status['stale'] or status['input_mismatches']:raise HarnessError('Cannot review stale or tampered evidence')
    if not command:raise HarnessError('Reviewer command required; stdout must be a complete JSON review report')
    run_id=new_id('review-run');env=os.environ.copy();env.update(PLANNING_DISABLED='1',HARNESS_REVIEW_ROOT=str(path),HARNESS_REVIEW_INPUT_HASH=status['input_hash'],HARNESS_REVIEW_RUN_ID=run_id)
    cwd=path/'source';cwd.mkdir(exist_ok=True)
    started=now();process=subprocess.Popen(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:stdout,stderr=process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        process.kill();process.communicate();raise HarnessError('Independent reviewer process timed out')
    if process.returncode:raise HarnessError('Reviewer process failed: '+stderr.decode(errors='replace')[-2000:])
    report=json.loads(stdout)
    if not isinstance(report,dict):raise HarnessError('Reviewer output must be a JSON object')
    report.update(run_id=run_id,independent_context=True,snapshot_id=snapshot,input_hash=status['input_hash'])
    output=path/'review-results'/f'{kind}-review.json';write_json(output,report)
    receipt={'run_id':run_id,'pid':process.pid,'parent_pid':os.getpid(),'kind':kind,'command':command,'started':started,'finished':now(),
             'returncode':0,'snapshot_id':snapshot,'input_hash':status['input_hash'],'report_sha256':sha(output.read_bytes()),
             'scope':'ACTUAL_INDEPENDENT_PROCESS; reviewer model/semantics are not authenticated'}
    write_json(path/'review-results'/f'{kind}-run-receipt.json',receipt)
    return {'status':'RECORDED_PROCESS_EVIDENCE','run_id':run_id,'receipt':receipt,'review':review_status(repo,task,snapshot)}


def review_status(repo, task, snapshot):
    path=repo/'.harness-artifacts/review'/safe_id(task)/safe_id(snapshot)
    manifest=read_json(path/'input-manifest.json')
    if not manifest:raise HarnessError('Snapshot does not exist')
    mismatches=[name for name,h in manifest['files'].items() if not (path/name).is_file() or sha((path/name).read_bytes())!=h]
    if manifest['input_hash']!=sha(canonical(manifest['files'])):mismatches.append('input-manifest.json HASH_MISMATCH')
    target=read_json(path/'target.json');current,value=fingerprint(repo,target['merge_base'],target.get('frozen_paths'))
    revision_changed=current!=target['fingerprint'];content_equal=sha(canonical(value['files']))==target.get('content_fingerprint')
    stale=revision_changed and not content_equal
    results=read_json(path/'review-results/main-review.json',{})
    pending,invalid=coverage(results,target['reviewable_paths'])
    def addressed(report):
        dispositions=read_json(path/'review-results/dispositions.json',{}).get('dispositions',[])
        done={x.get('finding_id') for x in dispositions if x.get('status') in {'resolved','accepted'} and x.get('reason')}
        return [f for f in report.get('findings',[]) if f.get('id') not in done]
    unresolved=addressed(results);risk=read_json(path/'review-results/risk-review.json',{})
    risk_pending,risk_invalid=coverage(risk,target['reviewable_paths'])
    receipt=read_json(path/'review-results/risk-run-receipt.json',{})
    risk_path=path/'review-results/risk-review.json'
    independent=bool(risk and risk.get('independent_context') and risk.get('run_id')==receipt.get('run_id') and receipt.get('run_id')!=results.get('run_id')
        and receipt.get('pid') and receipt.get('returncode')==0 and receipt.get('snapshot_id')==snapshot and receipt.get('input_hash')==manifest['input_hash']
        and receipt.get('report_sha256')==sha(risk_path.read_bytes()) and not risk_pending and not risk_invalid)
    requires=target.get('risk')=='L2'
    if requires:unresolved+=addressed(risk)
    status='TAMPERED' if mismatches else 'STALE' if stale else 'UNVERIFIED' if not results else 'INCOMPLETE' if pending or invalid or unresolved or (requires and not independent) else 'CONTENT_EQUIVALENT_REVALIDATION_REQUIRED' if revision_changed else 'COMPLETE_RECORDED'
    return {'status':status,'snapshot_id':snapshot,'input_hash':manifest['input_hash'],'input_mismatches':mismatches,
            'stale':stale,'revision_evidence':'SHA_OR_INDEX_CHANGED_CONTENT_EQUIVALENT' if revision_changed and content_equal else 'SOURCE_CHANGED' if stale else 'EXACT_FROZEN_VERSION',
            'pending_files':pending,'invalid_files':invalid,'risk_pending_files':risk_pending if requires else [],'unresolved_findings':unresolved,
            'independent_review':'ACTUAL_PROCESS_RECORDED' if independent else 'UNVERIFIED',
            'limitation':'CLI records complete process/file coverage; reviewer identity, model and review reasoning are not authenticated.'}
