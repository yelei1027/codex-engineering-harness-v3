"""Context checkpoints preserve verified disk state independently of chat summaries."""
import json
import math
import re
from pathlib import Path

from . import review, workflow
from .common import HarnessError, canonical, git, git_head, lock, new_id, now, private_path, read_json, run, safe_id, sha, write_json
from .transaction import safe_parent

FACT_FIELDS={'objective','completed','remaining','blockers','decisions','next_action','spec_refs','tests','review','workers'}
INACTIVE_TASK_NODES={'ZDONE','ZREPORT','ZINFO','ZCANCEL','ZRECOVERED'}
SECRET=re.compile(r'(?i)(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\s*[=:]\s*\S+|\bsk-[a-zA-Z0-9_-]{16,}')


def safe_file(path):
    safe_parent(path)
    if path.is_symlink():raise HarnessError('Refuse a symlink checkpoint or reference: '+str(path))
    return path


def validate_facts(facts):
    if not isinstance(facts,dict) or set(facts)!=FACT_FIELDS:
        raise HarnessError('Checkpoint facts require exactly: '+', '.join(sorted(FACT_FIELDS)))
    for name in ['objective','next_action']:
        if not isinstance(facts[name],str) or not facts[name].strip():raise HarnessError('Missing checkpoint '+name)
    for name in ['completed','remaining','blockers','decisions','spec_refs']:
        if not isinstance(facts[name],list) or not all(isinstance(v,str) for v in facts[name]):raise HarnessError('Invalid checkpoint '+name)
    if not isinstance(facts['review'],dict) or not isinstance(facts['tests'],list) or not isinstance(facts['workers'],list):
        raise HarnessError('Tests/workers must be arrays and review must be an object')
    for test in facts['tests']:
        if not isinstance(test,dict) or not isinstance(test.get('command'),list) or not all(isinstance(s,str) for s in test['command']) or type(test.get('exit_code')) is not int:
            raise HarnessError('Each executed test needs argv and integer exit_code; do not infer results')
    for worker in facts['workers']:
        if not isinstance(worker,dict) or not worker.get('id') or worker.get('status') not in {'running','pending','completed','stopped','failed'}:
            raise HarnessError('Each worker needs a real id and status')
    data=canonical(facts)
    if len(data)>24000 or SECRET.search(data.decode()):
        raise HarnessError('Checkpoint summary must be small and contain no credentials or raw transcripts')
    return facts


def guard(used_percent=None,compactions=0):
    from . import cli
    cfg=cli.defaults()['context']
    if type(compactions) is not int or compactions<0:raise HarnessError('Compaction count must be a nonnegative integer')
    if used_percent is None:
        return {'status':'USAGE_UNKNOWN','action':'CHECKPOINT_AT_MILESTONES_AND_BEFORE_COMPACT','automatic_percent_monitoring':False}
    if not math.isfinite(used_percent) or not 0<=used_percent<=100:raise HarnessError('Context usage must be between 0 and 100')
    if used_percent>=cfg['stop_new_work_percent']:status='STOP_NEW_WORK'
    elif used_percent>=cfg['checkpoint_percent']:status='CHECKPOINT_REQUIRED'
    elif used_percent>=cfg['warning_percent']:status='WARNING_CONTEXT_HIGH'
    else:status='NORMAL'
    return {'status':status,'used_percent':used_percent,'usage_source':'CALLER_SUPPLIED_HOST_READING',
            'handoff_recommended':compactions>=cfg['handoff_after_compactions'],
            'action':'CHECKPOINT_HANDOFF_ONLY' if status=='STOP_NEW_WORK' else 'CHECKPOINT_THEN_COMPACT_THEN_VERIFY' if status!='NORMAL' else 'CONTINUE'}


def refs(repo,values):
    result=[]
    for value in values:
        p=Path(value)
        if value.startswith(('https://','http://')):
            result.append({'reference':value,'status':'EXTERNAL_UNVERIFIED'});continue
        if p.is_absolute() or '..' in p.parts or private_path(p):raise HarnessError('Spec/test evidence must be a non-private repository-relative path')
        p=safe_file(repo/p)
        result.append({'path':value,'status':'PRESENT' if p.is_file() else 'MISSING','sha256':sha(p.read_bytes()) if p.is_file() else None})
    return result


def reference_conflicts(repo,doc):
    conflicts=[]
    for name in ['spec_refs','test_evidence','pwf']:
        recorded=[r for r in doc[name] if 'path' in r]
        if refs(repo,[r['path'] for r in recorded])!=recorded:
            conflicts.append(name.upper()+'_CHANGED')
    return conflicts


def choose_task(repo,task=None,session=None):
    if task:return safe_id(task)
    if session:
        mapping=read_json(safe_file(repo/'.harness/state/context-sessions.json'),{})
        if session in mapping:
            mapped=safe_id(mapping[session]);mapped_path=workflow.task_path(repo,mapped)
            try:mapped_state=workflow.load(mapped_path)
            except HarnessError:mapped_state=None
            if mapped_state and mapped_state['node'] not in INACTIVE_TASK_NODES:return mapped
    active=[]
    for path in (repo/'.harness/task').glob('*/events.jsonl'):
        state=workflow.load(safe_file(path).parent)
        if state['node'] not in INACTIVE_TASK_NODES:active.append(path.parent.name)
    if not active:raise HarnessError('No active Harness task for this session')
    if len(active)>1:raise HarnessError('Choose --task explicitly; multiple active Harness tasks exist for this session')
    return active[0]


def task_instance(path):
    # The first event is immutable for this task lifetime; legacy tasks also
    # have a concrete creation event. Revision alone can repeat after rebuild.
    return sha(safe_file(path/'events.jsonl').read_bytes().splitlines()[0])


def checkpoint(repo,task,facts=None,resume_node=None,session=None,reason='manual',plan_id=None):
    task=safe_id(task);path=workflow.task_path(repo,task)
    with lock(repo/'.harness/state/workflow.lock'):
        safe_file(path/'events.jsonl');state=workflow.load(path)
        old_path=safe_file(path/'context-checkpoint.json');old=read_json(old_path,{})
        old_manifest=read_json(safe_file(path/'context-checkpoint.sha256.json'),{})
        old_integrity=bool(old) and old_manifest.get('sha256')==sha(canonical(old))
        instance=task_instance(path)
        old_identity=(old_integrity and old.get('repo')==str(repo) and old.get('task_id')==task
                      and old.get('task_instance')==instance)
        previous_plan=old.get('plan_id') if old_identity else None
        selected_plan=safe_id(plan_id) if plan_id is not None else safe_id(previous_plan) if previous_plan is not None else None
        # The shared active-plan pointer and host environment are not task bindings.
        pwf=refs(repo,[f'.planning/{selected_plan or task}/{name}.md' for name in ['task_plan','findings','progress']])
        # Reuse semantic facts only from an integrity-verified checkpoint that still
        # describes this exact source and workflow revision. Tampered/stale data
        # degrades to CHECKPOINT_PARTIAL instead of being re-signed as current.
        fingerprint,source=review.fingerprint(repo,state['baseline_head'])
        branch=run(['git','-C',repo,'symbolic-ref','--short','-q','HEAD'],ok=False).stdout.decode().strip() or 'DETACHED'
        reused_facts=False
        if (facts is None and old_identity and selected_plan==previous_plan
                and old.get('fingerprint')==fingerprint and old.get('workflow_revision')==state['revision']
                and old.get('git',{}).get('branch')==branch and not reference_conflicts(repo,old)):
            facts=old.get('facts')
            reused_facts=facts is not None
        if facts is not None:validate_facts(facts)
        if selected_plan is not None and (plan_id is not None or facts is not None) and any(r['status']!='PRESENT' for r in pwf):
            raise HarnessError('Selected plan requires task_plan.md, findings.md and progress.md under .planning/'+selected_plan)
        node=resume_node or state.get('resume_node') or state['node']
        if node not in workflow.contract()['dynamic_resume']['allowed_nodes']:
            raise HarnessError('Checkpoint needs an allowed resume node')
        head=git_head(repo)
        def paths(*args):return sorted(p for p in review.names(git(repo,*args)) if not private_path(p))
        specs=refs(repo,facts['spec_refs']) if facts else []
        evidence=refs(repo,[t['evidence'] for t in facts['tests'] if t.get('evidence')]) if facts else []
        value={'schema_version':1,'checkpoint_id':new_id('context'),'task_id':task,'repo':str(repo),'created':now(),'reason':reason,
               'node':state['node'],'resume_node':node,'workflow_revision':state['revision'],'task_instance':instance,'fingerprint':fingerprint,
               'git':{'branch':branch,'head':head,'head_kind':'COMMIT' if run(['git','-C',repo,'rev-parse','--verify','HEAD'],ok=False).returncode==0 else 'UNBORN_EMPTY_TREE',
                      'staged':paths('diff','--cached','--name-only','-z',head),'unstaged':paths('diff','--name-only','-z'),
                      'untracked':paths('ls-files','--others','--exclude-standard','-z')},
               'facts':facts,'facts_status':'AGENT_RECORDED_NOT_INDEPENDENTLY_VERIFIED' if facts else 'AGENT_SUMMARY_REQUIRED',
               'spec_refs':specs,'test_evidence':evidence,'plan_id':selected_plan,'pwf':pwf,'remote_sync':'NOT_REQUESTED_FOR_COMPACT'}
        # A race must never produce a checkpoint claiming a different source version.
        if review.fingerprint(repo,state['baseline_head'])[0]!=fingerprint:raise HarnessError('Source changed while checkpointing; retry')
        if reference_conflicts(repo,value):raise HarnessError('Reference changed while checkpointing; retry')
        if reused_facts and reference_conflicts(repo,old):raise HarnessError('Previous evidence changed while checkpointing; retry')
        current_branch=run(['git','-C',repo,'symbolic-ref','--short','-q','HEAD'],ok=False).stdout.decode().strip() or 'DETACHED'
        if current_branch!=branch:raise HarnessError('Branch changed while checkpointing; retry')
        output=safe_file(path/'context-checkpoint.json');write_json(output,value)
        write_json(safe_file(path/'context-checkpoint.sha256.json'),{'sha256':sha(canonical(value))})
        if session:
            mapping_path=safe_file(repo/'.harness/state/context-sessions.json');mapping=read_json(mapping_path,{})
            mapping[session]=task;write_json(mapping_path,mapping)
    return {'status':'CHECKPOINT_READY' if facts else 'CHECKPOINT_PARTIAL','path':str(output),'resume_node':node,'head':head,'remaining':facts['remaining'] if facts else ['Agent summary required']}


def load_checkpoint(repo,task):
    path=workflow.task_path(repo,safe_id(task));doc=read_json(safe_file(path/'context-checkpoint.json'))
    manifest=read_json(safe_file(path/'context-checkpoint.sha256.json'))
    if not doc or not manifest or manifest.get('sha256')!=sha(canonical(doc)):raise HarnessError('Missing or modified context checkpoint')
    if doc.get('repo')!=str(repo) or doc.get('task_id')!=task:raise HarnessError('Checkpoint belongs to another project/task')
    return doc


def verify(repo,task):
    doc=load_checkpoint(repo,task);state=workflow.load(workflow.task_path(repo,task))
    conflicts=[]
    if doc.get('task_instance')!=task_instance(workflow.task_path(repo,task)):conflicts.append('TASK_INSTANCE_CHANGED_OR_UNKNOWN')
    current=review.fingerprint(repo,state['baseline_head'])[0]
    if current!=doc['fingerprint']:conflicts.append('GIT_OR_WORKTREE_CHANGED')
    branch=run(['git','-C',repo,'symbolic-ref','--short','-q','HEAD'],ok=False).stdout.decode().strip() or 'DETACHED'
    if branch!=doc['git']['branch']:conflicts.append('BRANCH_CHANGED')
    if state['revision']!=doc['workflow_revision']:conflicts.append('WORKFLOW_CHANGED')
    conflicts.extend(reference_conflicts(repo,doc))
    if not doc['facts']:conflicts.append('AGENT_SUMMARY_REQUIRED')
    else:validate_facts(doc['facts'])
    return {'status':'RESUME_CONFLICT' if conflicts else 'RESUME_OK','task_id':task,'resume_node':doc['resume_node'],
            'head':git_head(repo),'conflicts':conflicts,'next_action':doc['facts']['next_action'] if doc['facts'] else 'Record current task facts before implementing',
            'scope':'DISK_VERSION_CHECK; agent must also verify approvals, test/review evidence and worker state'}


def handoff(repo,task):
    from . import memory
    checked=verify(repo,task)
    if checked['status']!='RESUME_OK':raise HarnessError('Resolve context checkpoint conflicts before Handoff')
    doc=load_checkpoint(repo,task)
    try:return memory.handoff(repo,doc)
    except HarnessError as error:return {'status':'UNSYNCED','local_checkpoint':str(workflow.task_path(repo,task)/'context-checkpoint.json'),'reason':str(error)}
