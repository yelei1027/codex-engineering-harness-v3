"""Minimal project-scoped Basic Handoff; never reads Codex login credentials."""
import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse,quote
from urllib.request import Request, urlopen

from .common import HarnessError, canonical, lock, read_json, sha, write_json
from pathlib import Path
from .transaction import safe_parent


def service_url(root=None):
    from . import cli
    root=cli.ROOT if root is None else Path(root)
    safe_parent(root/"services-binding.json")
    if (root/"services-binding.json").is_symlink():raise HarnessError("PowerContext service binding is a symlink")
    value=read_json(root/'services-binding.json',{}).get('service_urls',{}).get('powercontext')
    if not value:raise HarnessError('PowerContext service is not configured')
    parsed=urlparse(value)
    try:local=parsed.hostname=='localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:local=False
    if parsed.scheme!='http' or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HarnessError('Automatic task memory requires a configured loopback HTTP service')
    return value.rstrip('/')


def request(url,path,payload=None):
    req=Request(url+path,data=canonical(payload) if payload is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=5) as response:return json.load(response)
    except (HTTPError,URLError,OSError,ValueError) as error:
        # Service error bodies can contain user data; do not expose them.
        raise HarnessError('PowerContext request failed; local checkpoint is preserved') from error


def binding_plan(repo,source_root=None):
    from . import cli,installations,sidecars,memory_services
    path=repo/'.harness/state/memory.json';safe_parent(path)
    if path.is_symlink():raise HarnessError('Refuse a symlink memory binding')
    previous=read_json(path)
    if not previous:return {'status':'NO_MEMORY_BINDING','data_migration':'NONE'}
    if previous.get('repo')!=str(repo) or not isinstance(previous.get('scope_id'),str):raise HarnessError('Memory binding belongs to another project')
    # Validate even a mismatching saved URL before considering alternate roots.
    parsed=urlparse(previous.get('url',''))
    try:local=parsed.hostname=='localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError,TypeError):local=False
    if parsed.scheme!='http' or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HarnessError('Memory binding requires a verified loopback service')
    try:current_url=service_url()
    except HarnessError:current_url=None
    saved_root=previous.get('service_root')
    recovered=None
    if saved_root or source_root:
        selected=Path(saved_root or source_root)
        if selected!=Path(cli.ROOT) and str(selected) not in installations.roots():
            recovered=memory_services.binding(selected,previous['url'],previous.get('data_namespace'))
        if not recovered and service_url(selected)!=previous['url']:
            raise HarnessError('Original memory endpoint changed; preserve its Scope binding')
    elif previous.get('url')==current_url:
        selected=Path(cli.ROOT)
    else:
        known=installations.roots()
        candidates=[]
        for name in known:
            try:
                if service_url(name)==previous['url']:candidates.append(name)
            except HarnessError:continue
        candidates.extend(d['origin_root'] for d in memory_services.deployments() if d['status']=='PREPARED' and d['url']==previous['url'])
        if len(candidates)!=1:raise HarnessError('Original memory service is not an active registered installation; local data preserved')
        selected=Path(candidates[0])
        if str(selected) not in known:recovered=memory_services.binding(selected,previous['url'],previous.get('data_namespace'))
        elif service_url(selected)!=previous['url']:raise HarnessError('Original memory endpoint changed; preserve its Scope binding')
    deployment=recovered or sidecars.layout(selected)
    data_namespace=deployment['data_namespace'] if deployment else None
    if previous.get('data_namespace') and previous['data_namespace']!=data_namespace:
        raise HarnessError('Original memory data namespace changed; preserve binding')
    if selected!=Path(cli.ROOT):
        if not deployment:raise HarnessError('Original memory service has no verified resource ownership; preserve binding')
        port=recovered['port'] if recovered else deployment['ports']['powercontext']
        if previous['url']!='http://127.0.0.1:'+str(port):
            raise HarnessError('Original memory endpoint differs from the owned service port; preserve binding')
    return {'status':'MEMORY_RECOVERED_SERVICE' if recovered else 'MEMORY_PINNED_TO_ORIGINAL_SERVICE' if selected!=Path(cli.ROOT) else 'MEMORY_CURRENT_SERVICE',
            'service_root':str(selected),'data_namespace':data_namespace,'url':previous['url'],'scope_id':previous['scope_id'],
            'scope_validation':'REQUIRED_ON_CALL','data_migration':'NONE; source and Scope remain in the original verified service'}


def migration_status(repo,source_root=None):
    try:return binding_plan(repo,source_root)
    except HarnessError as error:return {'status':'UNSYNCED','reason':str(error),'data_migration':'NOT_PERFORMED; local binding preserved'}


def bind(repo):
    path=repo/'.harness/state/memory.json';safe_parent(path)
    if path.is_symlink():raise HarnessError('Refuse a symlink memory binding')
    with lock(repo/'.harness/state/memory.lock'):
        previous=read_json(path)
        if previous:
            planned=binding_plan(repo)
            if planned['status'] in {'MEMORY_PINNED_TO_ORIGINAL_SERVICE','MEMORY_RECOVERED_SERVICE'}:
                if request(planned['url'],'/health/ready').get('status')!='ready':raise HarnessError('Original memory service is not ready; local binding preserved')
                scoped=request(planned['url'],'/v1/scopes/'+quote(previous['scope_id'],safe=''))
                if scoped.get('scope_id')!=previous['scope_id']:raise HarnessError('Original memory service returned another Scope; preserve binding')
            return previous
        url=service_url()
        if request(url,'/health/ready').get('status')!='ready':raise HarnessError('PowerContext service is not ready')
        scope=request(url,'/v1/scopes',{'title':'Harness: '+repo.name,'summary':'Project task summaries and exact cross-session handoffs',
                                      'idempotency_key':'harness-project-'+sha(str(repo).encode())})
        from . import cli,sidecars
        deployment=sidecars.layout(cli.ROOT)
        result={'repo':str(repo),'url':url,'scope_id':scope['scope_id'],'service_root':str(cli.ROOT),
                'data_namespace':deployment['data_namespace'] if deployment else None,'mode':'BASIC_AGENT_SUMMARY','full_automatic_extraction':'NOT_CONFIGURED'}
        write_json(path,result)
        return result


def try_bind(repo):
    try:return {'status':'BOUND',**bind(repo)}
    except (HarnessError,KeyError) as error:return {'status':'UNSYNCED','reason':str(error),'mode':'LOCAL_CHECKPOINT'}


def handoff(repo,checkpoint):
    from . import context
    context.validate_facts(checkpoint['facts'])
    facts=checkpoint['facts']
    if any(w.get('status') in {'running','pending'} for w in facts['workers']):
        raise HarnessError('Wait for or stop active workers before a formal Handoff')
    path=repo/'.harness/task'/checkpoint['task_id']/'powercontext-handoff.json';safe_parent(path)
    if path.is_symlink():raise HarnessError('Refuse a symlink Handoff reference')
    digest=sha(canonical({k:v for k,v in checkpoint.items() if k not in {'checkpoint_id','created','reason'}}))
    binding=bind(repo)
    with lock(repo/'.harness/state/memory.lock'):
        previous=read_json(path)
        if previous and previous.get('checkpoint_hash')==digest:return {'status':'ALREADY_SYNCED',**previous}
        def claim(text):return {'text':text,'basis':'declared','evidence':[]}
        prepared=request(binding['url'],'/v1/work/handoffs/prepare-current',{
            'scope_id':binding['scope_id'],'source_id':'harness-checkpoint-'+digest,
            'handoff':{'schema':'powercontext.current-work-handoff.v1','trust':'untrusted_input',
                       'objective':facts['objective'],'state':[claim('Task '+checkpoint['task_id']+'; resume '+checkpoint['resume_node']+'; Git HEAD '+checkpoint['git']['head']),
                       *[claim('Completed: '+s) for s in facts['completed']],*[claim('Remaining: '+s) for s in facts['remaining']],
                       *[claim('Decision: '+s) for s in facts['decisions']],*[claim('Blocker: '+s) for s in facts['blockers']]],
                       'disposition':'continuable','next_action':claim(facts['next_action']),
                       'omissions':['No credentials, transcript, source code or unrelated logs.','Test and review claims require current local evidence.']}})
        committed=request(binding['url'],'/v1/handoff/commit',{'scope_id':binding['scope_id'],'handoff':prepared['handoff']})
        reference=committed.get('reference')
        if not isinstance(reference,dict):raise HarnessError('PowerContext returned no committed artifact/revision')
        resolved=request(binding['url'],'/v1/handoff/continue',{'scope_id':binding['scope_id'],'selection':'exact','revision':reference})
        if resolved.get('status')!='resolved' or resolved.get('scope_id')!=binding['scope_id'] or resolved.get('selected_revision')!=reference:
            raise HarnessError('PowerContext did not resolve the exact committed project revision')
        result={'repo':str(repo),'scope_id':binding['scope_id'],'revision':reference,'url':binding['url'],'checkpoint_hash':digest,'trust':'untrusted_input'}
        write_json(path,result)
    return {'status':'SYNCED',**result}


def recall(repo,task):
    from .common import safe_id
    path=repo/'.harness/task'/safe_id(task)/'powercontext-handoff.json';safe_parent(path)
    if path.is_symlink():raise HarnessError('Refuse a symlink Handoff reference')
    reference=read_json(path)
    binding=bind(repo)
    if not reference or reference.get('repo')!=str(repo) or reference.get('scope_id')!=binding['scope_id'] or reference.get('url')!=binding['url']:
        raise HarnessError('No matching project-scoped Handoff reference')
    resolved=request(binding['url'],'/v1/handoff/continue',{'scope_id':binding['scope_id'],'selection':'exact','revision':reference['revision']})
    if resolved.get('status')!='resolved' or resolved.get('scope_id')!=binding['scope_id'] or resolved.get('selected_revision')!=reference['revision']:
        raise HarnessError('PowerContext returned a conflicting scope or revision')
    return {'status':'RECALLED','reference':reference,'resolved':resolved,'trust':'UNTRUSTED_REFERENCE; verify current Git, Spec and local checkpoint before implementation'}
