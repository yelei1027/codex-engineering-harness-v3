from pathlib import Path
from .common import ROOT,HarnessError,atomic,canonical,git,lock,now,read_json,safe_id,sha,write_json
from .review import fingerprint,names

def contract():
    return read_json(ROOT/'contracts/workflow.json')

def task_path(repo, task):
    return repo/'.harness/task'/safe_id(task)

def save(path,state,event):
    # Every event contains complete post-state; the append log is the recovery authority.
    state['revision']=state.get('revision',0)+1
    state['updated']=now()
    event.update(timestamp=state['updated'],revision=state['revision'],state=state.copy())
    old=(path/'events.jsonl').read_bytes() if (path/'events.jsonl').exists() else b''
    atomic(path/'events.jsonl',old+canonical(event)+b'\n')
    write_json(path/'state.json',state)

def load(path):
    if not (path/'events.jsonl').exists():
        raise HarnessError('Unknown task')
    import json
    events=[json.loads(line) for line in (path/'events.jsonl').read_text().splitlines()]
    if not events or any(e['revision']!=i+1 for i,e in enumerate(events)):
        raise HarnessError('Invalid workflow event history')
    return events[-1]['state']

def create(repo,task,risk,intent):
    path=task_path(repo,task)
    with lock(repo/'.harness/state/workflow.lock'):
        if (path/'events.jsonl').exists():
            raise HarnessError('Task ID already exists')
        path.mkdir(parents=True,exist_ok=True)
        head=git(repo,'rev-parse','HEAD').decode().strip()
        state={'task_id':task,'repo':str(repo),'node':contract()['entry_node'],'risk':risk,'intent':intent,
               'baseline_head':head,'baseline_changes':names(git(repo,'diff','--name-only','-z','HEAD'))+names(git(repo,'ls-files','--others','--exclude-standard','-z')),
               'created':now(),'resume_node':None,'fingerprint':fingerprint(repo,head)[0]}
        save(path,state,{'action':'create'})
        return state

def transition(repo,task,to,reason,evidence=None,risk=None):
    path=task_path(repo,task)
    with lock(repo/'.harness/state/workflow.lock'):
        state=load(path)
        cfg=contract(); nodes={n['id']:n for n in cfg['nodes']}
        allowed={e['target'] for e in nodes[state['node']]['edges']}
        if to not in allowed:
            raise HarnessError(f'Invalid contract edge: {state["node"]} -> {to}; allowed {sorted(allowed)}')
        if not reason.strip():
            raise HarnessError('A branch reason is required')
        if risk and int(risk[1])<int(state['risk'][1]):
            raise HarnessError('Risk cannot be silently downgraded')
        proof=read_json(Path(evidence)) if evidence else {}
        if evidence and not proof:
            raise HarnessError('Evidence must be nonempty JSON')
        original=to
        if to=='RESUME':
            to=state.get('resume_node')
            if to not in cfg['dynamic_resume']['allowed_nodes']:
                raise HarnessError('No allowed resume node recorded')
        if to in {'P01','P03','P06'}:
            head=git(repo,'rev-parse','HEAD').decode().strip()
            if not proof.get('authorization_id') or proof.get('head')!=head or proof.get('approved_action')!={'P01':'commit_push','P03':'merge','P06':'deploy'}[to]:
                raise HarnessError('Publication edge requires exact-head action authorization evidence; CLI records but does not authenticate user approval')
        if state['intent']=='review_only' and to in {'P00','P01','P03','P06','T03','T05','X00'}:
            raise HarnessError('review_only task cannot implement or publish')
        if state['node']=='D00':
            expected={'development':{'A01'},'review_only':{'A01'},'explanation':{'ZINFO'},'maintenance':{'U10'},'recovery':{'H02'}}[state['intent']]
            if to not in expected:raise HarnessError('D00 branch does not match recorded task intent')
        selected_edges=[e for e in nodes[state['node']]['edges'] if e['target']==original]
        if len(nodes[state['node']]['edges'])>1:
            state['last_branch']={'from':state['node'],'to':to,'reason':reason,'contract_conditions':[e.get('reason','') or e.get('label','') for e in selected_edges],
                                  'evidence_sha256':sha(Path(evidence).read_bytes()) if evidence else None,'condition_verification':'CALLER_RECORDED; no automatic decision engine'}
        state['node']=to
        if risk:state['risk']=risk
        save(path,state,{'action':'transition','target':original,'reason':reason,'evidence':proof,'evidence_sha256':sha(Path(evidence).read_bytes()) if evidence else None})
        return state

def pause(repo,task,resume,reason):
    if not reason.strip():raise HarnessError('Pause reason is required')
    path=task_path(repo,task)
    with lock(repo/'.harness/state/workflow.lock'):
        state=load(path)
        cfg=contract()
        if resume not in cfg['dynamic_resume']['allowed_nodes']:
            raise HarnessError('Resume node is not in contract allowlist')
        state.update(node='ZPAUSED',resume_node=resume,paused_from=state['node'],fingerprint=fingerprint(repo,state['baseline_head'])[0])
        save(path,state,{'action':'pause','via':'H00','reason':reason,'resource_cleanup':'NONE'})
        write_json(path/'handoff.json',{'task_id':task,'repo':str(repo),'resume_node':resume,'revision':state['revision'],'fingerprint':state['fingerprint'],'remote_sync':'UNSYNCED'})
        return state

def resume(repo,task):
    path=task_path(repo,task)
    with lock(repo/'.harness/state/workflow.lock'):
        state=load(path)
        if state['node'] not in {'ZPAUSED','ZBLOCKED'}:
            raise HarnessError('Task is not paused or blocked')
        current=fingerprint(repo,state['baseline_head'])[0]
        to='V00' if current!=state['fingerprint'] else state.get('resume_node')
        if to not in contract()['dynamic_resume']['allowed_nodes']:
            raise HarnessError('Resume point unavailable; inspect evidence and record a checkpoint')
        state.update(node=to,fingerprint=current)
        save(path,state,{'action':'resume','via':['H02','D30'],'authorization':'MUST_RECHECK_BEFORE_PUBLICATION'})
        return state
