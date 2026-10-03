"""Read-only registration inventory; identities distinguish URL and stdio transports."""
import json
import os
import shutil
import tomllib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .common import HarnessError


def endpoint_identity(server):
    if server.get('url'):
        u=urlsplit(server['url'])
        return ('url',urlunsplit((u.scheme.lower(),u.netloc.lower(),u.path.rstrip('/'),u.query,'')))
    if server.get('command'):
        executable=shutil.which(server['command']) or server['command']
        return ('stdio',str(Path(executable).resolve()) if '/' in executable else executable,tuple(server.get('args',[])))
    return None


def inspect_registrations(repo=None):
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    scopes=[('global',home/'config.toml',home/'hooks.json')]
    if repo:scopes.append(('project',repo/'.codex/config.toml',repo/'.codex/hooks.json'))
    endpoints=[];pwf_plugin=[];pwf_standalone=[];context_hooks=[];hook_sources=[];errors=[]
    for scope,config,hooks in scopes:
        cfg=tomllib.loads(config.read_text()) if config.exists() else {}
        for name,server in cfg.get('mcp_servers',{}).items():
            identity=endpoint_identity(server)
            if identity:endpoints.append((scope,name,identity))
        documents=[(str(config)+' [hooks]',cfg.get('hooks',{}))]
        if hooks.exists():documents.append((str(hooks),json.loads(hooks.read_text())))
        for location,doc in documents:
            # Trust metadata is not a registration, and cannot prove actual execution.
            active={k:v for k,v in doc.items() if k!='state'} if isinstance(doc,dict) else doc
            text=json.dumps(active).lower()
            if active:hook_sources.append(location)
            if 'planning-with-files' in text or 'planning_with_files' in text:pwf_standalone.append(location)
            if 'context-mode' in text or 'context_mode' in text:context_hooks.append(location)
        for name,entry in cfg.get('plugins',{}).items():
            if not isinstance(entry,dict) or not entry.get('enabled',False):continue
            component=name.split('@',1)[0];market=name.split('@',1)[-1]
            if 'planning-with-files' in component:pwf_plugin.append(scope+':'+name)
            matches=list((home/'plugins/cache'/market/component).glob('*/plugin.json'))
            matches+=list((home/'plugins/cache'/market/component).glob('*/.codex-plugin/plugin.json'))
            for manifest in matches:
                doc=json.loads(manifest.read_text());base=manifest.parent
                for field in ['hooks','mcpServers','mcp_servers']:
                    value=doc.get(field)
                    if isinstance(value,str):
                        path=base/value
                        if not path.exists():path=base.parent/value
                        if path.is_file():value=json.loads(path.read_text())
                    if field=='hooks' and value:
                        text=json.dumps(value).lower()
                        if 'context-mode' in component or 'context-mode' in text:context_hooks.append(str(manifest))
                        hook_sources.append(str(manifest))
                    elif field!='hooks' and isinstance(value,dict):
                        for server_name,server in value.get('mcpServers',value).items():
                            if isinstance(server,dict) and endpoint_identity(server):endpoints.append((scope+':plugin:'+name,server_name,endpoint_identity(server)))
    if pwf_plugin and pwf_standalone:errors.append('PWF_PLUGIN_AND_STANDALONE_DUPLICATE')
    if context_hooks:errors.append('CONTEXT_MODE_HOOKS_VIOLATE_MCP_ONLY_PROFILE')
    duplicates=[]
    for i,a in enumerate(endpoints):
        for b in endpoints[i+1:]:
            if a[2]==b[2]:duplicates.append({'first':a[:2],'second':b[:2]})
    return {'HOOKS':{'status':'FAIL' if errors else 'PENDING_TRUST' if hook_sources or pwf_plugin else 'NOT_REGISTERED',
                      'errors':errors,'sources':hook_sources,'pwf_plugin':pwf_plugin,'pwf_standalone':pwf_standalone,
                      'execution':'UNVERIFIED','ordering':'NOT_GUARANTEED'},
            'MCP':{'status':'FAIL' if duplicates else 'UNVERIFIED','duplicates':duplicates,
                   'registrations':[{'scope':s,'name':n,'transport':i[0]} for s,n,i in endpoints]}}
