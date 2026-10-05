"""Owned resource gateway preparation and process entry point."""
import secrets
import socket
import threading
from pathlib import Path

from . import sidecars, processes, software
from .common import HarnessError, lock, read_json, write_json, sha, canonical, state_dir
from .transaction import safe_parent
from .resource_backend import OwnedBackend, RetainedBackend
from .resource_runtime import ServiceRuntime
from .resource_preferences import ResourcePreferences
from .resource_http import ResourceHTTPServer
from .resource_mcp import MCPBridge, discovery_snapshot, sdk_request


def descriptor_path(root):
    path = Path(root)/'.harness-install/resources.json'
    safe_parent(path)
    if path.is_symlink():
        raise HarnessError('Resource descriptor is a symlink; preserve it')
    return path


def component_identity(root):
    value = sidecars.layout(root)
    if not value:
        raise HarnessError('Owned service layout required')
    return sha(canonical({'root':str(root), 'state':str(sidecars.owner_state(root)),
                          'layout':value, 'lock':sha((Path(root)/'harness.lock').read_bytes())}))


def load(root):
    root = Path(root).resolve()
    value = read_json(descriptor_path(root), {})
    if not value:
        raise HarnessError('Resource gateway is not prepared')
    if value.get('schema_version') != 1 or value.get('root') != str(root):
        raise HarnessError('Resource descriptor ownership mismatch')
    if value.get('identity') != component_identity(root):
        raise HarnessError('Resource identity changed; revalidate preparation')
    ports = value.get('ports', {})
    if set(ports) != {'powercontext', 'screenshot-backend', 'screenshot-frontend'}:
        raise HarnessError('Incomplete gateway port inventory')
    if (len(set(ports.values())) != 3 or
            any(type(p) is not int or not 1024 <= p <= 65535 for p in ports.values())):
        raise HarnessError('Invalid gateway ports')
    if set(ports.values()) & set(sidecars.layout(root)['ports'].values()):
        raise HarnessError('Gateway ports overlap backend ports')
    if not isinstance(value.get('control_token'), str) or len(value['control_token']) != 64:
        raise HarnessError('Invalid resource control identity')
    MCPBridge(None, value['discovery'], value['identity'], None)
    return value


def prepare(root):
    """Called in the installation preparation lock, before projects bind."""
    root = Path(root).resolve()
    processes.require_idle(root)
    software.verify_all(root)
    if descriptor_path(root).exists():
        return load(root)
    backend = OwnedBackend(root, 'powercontext')
    # A real discovery probe is required once; stop even if capture fails.
    try:
        backend.start()
        results = sdk_request(root, backend.endpoints()['powercontext']+'/mcp/')
        identity = component_identity(root)
        snapshot = discovery_snapshot(identity, results)
    finally:
        backend.stop()
    holders = []
    ports = {}
    try:
        for name in sidecars.PORT_KEYS:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            holders.append(sock)
            ports[name] = sock.getsockname()[1]
        value = {'schema_version':1, 'root':str(root), 'identity':identity,
                 'ports':ports, 'idle_seconds':120, 'startup_seconds':60,
                 'control_token':secrets.token_hex(32), 'discovery':snapshot}
        write_json(descriptor_path(root), value)
    finally:
        for sock in holders:
            sock.close()
    return value


def serve(root, stop_event=None):
    """One process owns all service leases and HTTP listeners for this root."""
    root = Path(root).resolve()
    stop_event = stop_event or threading.Event()
    with lock(root/'.harness-install/resource-manager.lock'):
        value = load(root)
        backends = {name:OwnedBackend(root, name) for name in sidecars.GROUPS}
        preferences = ResourcePreferences(root, value['identity'])
        manual = preferences.read()
        runtimes = {name:ServiceRuntime(backend.start, backend.stop, manual=manual[name],
                     persist_manual=lambda enabled, group=name: preferences.set_manual(group, enabled))
                    for name,backend in backends.items()}
        # Reconcile running leftovers only inside the verified deployment.
        for group,backend in backends.items():
            processes.require_idle(root)
            owned = sidecars.inspect_owned(root, group)
            if manual[group]:
                runtimes[group].start_manual()
            elif any(item['state']=='running' for item in owned):
                backend.stop()
        servers = []
        threads = []
        try:
            for name, port in value['ports'].items():
                group = 'powercontext' if name == 'powercontext' else 'screenshot-to-code'
                backend = backends[group]
                upstream = backend.endpoints()[name]
                mcp = None
                if name == 'powercontext':
                    mcp = MCPBridge(runtimes[group], value['discovery'], value['identity'],
                                    lambda message, cancel_event=None, endpoint=upstream+'/mcp/': sdk_request(root, endpoint, message=message,cancel_event=cancel_event))
                server = ResourceHTTPServer(('127.0.0.1', port), runtimes[group], upstream, mcp=mcp, control_token=value['control_token'])
                servers.append(server)
            for server in servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                threads.append(thread)
            retained={}
            from . import memory_services
            while not stop_event.wait(1):
                records=memory_services.deployments()
                by_origin={item['origin_root']:item for item in records}
                for origin,server in list(retained.items()):
                    current=by_origin.get(origin,{})
                    if current.get('status')=='PREPARED' and current.get('owner_root')==str(root):continue
                    try:server.runtime.quiesce()
                    except HarnessError:continue
                    index=servers.index(server)
                    server.shutdown();server.server_close();threads[index].join(timeout=3)
                    servers.pop(index);threads.pop(index);del retained[origin]
                for doc in records:
                    origin=doc['origin_root']
                    if (doc['status']!='PREPARED' or not doc.get('on_demand')
                            or doc.get('owner_root')!=str(root) or origin in retained):continue
                    memory_services.verify_origin(doc,memory_services.source(origin))
                    backend=RetainedBackend(root,origin)
                    runtime=ServiceRuntime(backend.start,backend.stop,manual=doc.get('manual',False),
                              persist_manual=lambda enabled, source=origin: memory_services.set_manual(source,enabled))
                    if doc.get('manual'):runtime.start_manual()
                    else:
                        existing=memory_services.inspect(doc)
                        if existing and existing.get('State',{}).get('Status')=='running':backend.stop()
                    endpoint=backend.endpoints()['powercontext']
                    identity=sha(canonical({'origin':origin,'image':doc['image_id'],'data':doc['data_namespace']}))
                    snapshot=discovery_snapshot(identity,doc['discovery'])
                    bridge=MCPBridge(runtime,snapshot,identity,
                        lambda message, cancel_event=None, url=endpoint+'/mcp/': sdk_request(root,url,message=message,cancel_event=cancel_event))
                    server=ResourceHTTPServer(('127.0.0.1',doc['port']),runtime,endpoint,mcp=bridge,
                                               control_token=value['control_token'])
                    servers.append(server)
                    thread=threading.Thread(target=server.serve_forever,daemon=True)
                    thread.start();threads.append(thread);retained[origin]=server
        finally:
            for index, server in enumerate(servers):
                if index < len(threads):
                    server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=3)


def retained_targets(root):
    from . import memory_services
    result={}
    for doc in memory_services.deployments():
        if doc['status']=='PREPARED' and doc.get('on_demand') and doc.get('owner_root')==str(Path(root).resolve()):
            memory_services.verify_origin(doc,memory_services.source(doc['origin_root']))
            result['retained-'+sidecars.root_id(doc['origin_root'])]=doc
    return result


def target_groups(root,group='all'):
    if group not in {'all',*sidecars.GROUPS}:raise HarnessError('Unknown resource group')
    groups=list(sidecars.GROUPS) if group=='all' else [group]
    if group!='screenshot-to-code':groups.extend(retained_targets(root))
    return groups


def control(root, action, group):
    """Authenticated local CLI control; the target is selected from owned metadata."""
    import json
    from urllib.request import Request, ProxyHandler, build_opener
    from urllib.error import HTTPError, URLError
    if action not in {'status', 'start', 'stop', 'quiesce', 'resume'}:
        raise HarnessError('Unknown resource control operation')
    value = load(root)
    component = 'powercontext' if group == 'powercontext' else 'screenshot-frontend'
    port=value['ports'][component]
    if group not in sidecars.GROUPS:
        doc=retained_targets(root).get(group)
        if not doc:raise HarnessError('Unknown retained resource target')
        port=doc['port']
    url = 'http://127.0.0.1:'+str(port)+'/_harness/'+action
    request = Request(url, data=None if action == 'status' else b'',
                      headers={'Authorization':'Bearer '+value['control_token']},
                      method='GET' if action == 'status' else 'POST')
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=65) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise HarnessError('Oversized resource status response')
            return json.loads(data)
    except HTTPError as error:
        error.close()
        raise HarnessError('Resource control refused; inspect active calls and ownership') from None
    except (URLError, OSError, ValueError):
        raise HarnessError('Resource gateway unavailable; no direct-start fallback') from None


def status(root):
    root = Path(root).resolve()
    if not descriptor_path(root).exists():
        return {'status':'NOT_PREPARED', 'root':str(root), 'services':[],
                'next_step':'Prepare the on-demand gateway in an isolated installation'}
    value = load(root)
    projects = read_json(sidecars.owner_state(root)/'projects.json', {})
    owners = [name for name in projects if
              read_json(Path(name)/'.harness/state/project.json', {}).get('harness_root') == str(root)]
    services = []
    for group in sidecars.GROUPS:
        try:
            observed = control(root, 'status', group)
        except HarnessError:
            observed = {'state':'GATEWAY_UNAVAILABLE', 'active_calls':None,
                        'mode':'UNKNOWN', 'idle_remaining_seconds':None}
        services.append({'service':group, **observed,
                         'containers':sidecars.inspect_owned(root, group)})
    from . import memory_services
    for key,doc in retained_targets(root).items():
        try:observed=control(root,'status',key)
        except HarnessError:observed={'state':'GATEWAY_UNAVAILABLE','active_calls':None}
        services.append({'service':key,'origin_root':doc['origin_root'],**observed,
                         'data_namespace':doc['data_namespace']})
    from . import resource_systemd
    try:
        manager = resource_systemd.status(root)
    except HarnessError:
        manager = {'status':'UNVERIFIED', 'memory_bytes':None}
    return {'status':'RESOURCE_STATUS', 'root':str(root), 'projects':owners,
            'idle_seconds':value['idle_seconds'], 'services':services, 'manager':manager}


def manage(root, action, group='all'):
    if action not in {'start','stop','status'}:raise HarnessError('Unknown resource action')
    from . import memory_services
    groups = target_groups(root,group)
    legacy = [] if group=='screenshot-to-code' else [doc for doc in memory_services.deployments()
              if doc['status']=='PREPARED' and not doc.get('on_demand')]
    # Validate legacy deployments before any gateway mutation as well.
    legacy_status=memory_services._manage('status',legacy)
    if action=='start':memory_services.start_preflight(legacy)
    if action == 'status':
        result = status(root)
        result['services'] = [item for item in result['services'] if item['service'] in groups]
        result['legacy_memory_services']=legacy_status
        return result
    # Check the complete requested set before starting any stop operation.
    observed = {name:control(root, 'status', name) for name in groups}
    if action == 'stop' and any(v.get('active_calls') or v.get('state') in {'STARTING','STOPPING'} for v in observed.values()):
        raise HarnessError('Resources have active calls; stop refused')
    results = []
    for name in groups:
        try:
            results.append({'service':name, **control(root, action, name)})
        except HarnessError as error:
            return {'status':'PARTIAL_RESOURCE_OPERATION' if results else 'RESOURCE_OPERATION_FAILED',
                    'completed':results, 'failed_service':name, 'reason':str(error)}
    legacy_results=[]
    for doc in legacy:
        try:
            with lock(state_dir()/'data-locks'/doc['data_namespace']/'maintenance.lock'):
                legacy_results.extend(memory_services._manage(action,[doc]))
        except HarnessError as error:
            return {'status':'PARTIAL_RESOURCE_OPERATION','completed':results,
                    'legacy_memory_services':legacy_results,'failed_service':doc['origin_root'],'reason':str(error)}
    return {'status':'RESOURCES_'+action.upper(), 'services':results,
            'legacy_memory_services':legacy_results, 'volumes':'PRESERVED'}


def public_binding(root, source=None):
    """Advertise gateway addresses; backend ports stay in owned service layout."""
    value = load(root)
    if source is None:
        source = read_json(Path(root)/'services-binding.json', {})
    if not source:
        raise HarnessError('Prepared service binding required')
    import copy
    binding = copy.deepcopy(source)
    ports = value['ports']
    urls = {name:'http://127.0.0.1:'+str(port) for name,port in ports.items()}
    binding['service_urls'] = {'powercontext':urls['powercontext'],
                               'screenshot_backend':urls['screenshot-backend'],
                               'screenshot_frontend':urls['screenshot-frontend']}
    binding['mcp_servers']['powercontext']['url'] = urls['powercontext']+'/mcp/'
    command = binding.get('component_commands', {}).get('powercontext', [])
    if '--server-url' in command:
        command[command.index('--server-url')+1] = urls['powercontext']
    binding['resource_gateway'] = {'schema_version':1, 'identity':value['identity']}
    return binding


def quiesce(root):
    reserved = []
    try:
        for group in target_groups(root):
            control(root, 'quiesce', group)
            reserved.append(group)
    except BaseException:
        for group in reserved:
            control(root, 'resume', group)
        raise
    return reserved


def resume(root):
    for group in target_groups(root):
        control(root, 'resume', group)
