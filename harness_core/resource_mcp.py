"""Version-bound discovery for a restartable MCP backend.

Discovery is captured from the real backend at preparation time. It is not a
handwritten tool catalogue and never authorizes a tool call by itself.
"""
import copy
import hashlib
import json
import threading
import secrets
import time

from .common import HarnessError

DISCOVERY = {'tools/list', 'prompts/list', 'resources/list', 'resources/templates/list'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class MCPBridge:
    def __init__(self, runtime, snapshot, identity, invoke, *, clock=time.monotonic, session_idle=1800):
        if snapshot.get('identity') != identity:
            raise HarnessError('MCP discovery belongs to another prepared component')
        if snapshot.get('digest') != fingerprint(snapshot.get('results')):
            raise HarnessError('MCP discovery integrity mismatch')
        self.results = copy.deepcopy(snapshot['results'])
        if not isinstance(self.results.get('initialize'), dict):
            raise HarnessError('Missing actual MCP initialization evidence')
        self.runtime = runtime
        self.invoke = invoke
        self.clock = clock
        self.session_idle = session_idle
        self.sessions = {}
        self.calls = {}
        self.guard = threading.RLock()

    def expire_sessions(self):
        now=self.clock()
        with self.guard:
            active={session for session,_ in self.calls}
            for token,touched in list(self.sessions.items()):
                if token not in active and now-touched>=self.session_idle:
                    del self.sessions[token]

    def open_session(self):
        self.expire_sessions()
        with self.guard:
            if len(self.sessions)>=128:raise HarnessError('MCP session capacity reached')
            token=secrets.token_hex(24);self.sessions[token]=self.clock();return token

    def has_session(self, token):
        self.expire_sessions()
        with self.guard:
            if token not in self.sessions:return False
            self.sessions[token]=self.clock()
            return True

    def close_session(self, token):
        with self.guard:
            self.sessions.pop(token,None)
            for (session,_),event in self.calls.items():
                if session==token:event.set()

    def dispatch(self, message, session='internal'):

        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            return self.error(None, -32600, 'Invalid request')
        identifier = message.get('id')
        method = message.get('method')
        params = message.get('params', {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return self.error(identifier, -32600, 'Invalid request')
        if 'id' not in message:
            # This bridge does not expose backend sessions. Initialization is per
            # real call; client readiness notification does not wake a container.
            if method == 'notifications/cancelled':
                with self.guard:
                    event=self.calls.get((session,params.get('requestId')))
                    if event:event.set()
                return None
            if method == 'notifications/initialized':
                return None
            return None
        if method == 'ping':
            result = {}
        elif method == 'initialize':
            result = copy.deepcopy(self.results['initialize'])
        elif method in DISCOVERY:
            if params.get('cursor'):
                return self.error(identifier, -32602, 'Discovery snapshot has no continuation cursor')
            if method not in self.results:
                return self.error(identifier, -32601, 'Capability not provided by prepared backend')
            result = copy.deepcopy(self.results[method])
        else:
            # Never retry a side-effecting call after a timeout or disconnect.
            if not isinstance(identifier,(str,int)) or isinstance(identifier,bool):
                return self.error(None,-32600,'Invalid request ID')
            key=(session,identifier)
            event=threading.Event()
            with self.guard:
                if key in self.calls:return self.error(identifier,-32600,'Request ID already active')
                self.calls[key]=event
            try:
                with self.runtime.use():
                    return self.invoke(copy.deepcopy(message),cancel_event=event)
            finally:
                with self.guard:self.calls.pop(key,None)
        return {'jsonrpc':'2.0', 'id':identifier, 'result':result}

    @staticmethod
    def error(identifier, code, message):
        return {'jsonrpc':'2.0', 'id':identifier, 'error':{'code':code, 'message':message}}


def discovery_snapshot(identity, results):
    allowed = {'initialize', *DISCOVERY}
    if not isinstance(results, dict) or set(results)-allowed:
        raise HarnessError('Unexpected discovery records')
    if not isinstance(results.get('initialize'), dict):
        raise HarnessError('MCP initialize result required')
    for method, value in results.items():
        if not isinstance(value, dict) or value.get('nextCursor'):
            raise HarnessError('Discovery must contain fully collected pages')
    copied = copy.deepcopy(results)
    return {'identity':identity, 'results':copied, 'digest':fingerprint(copied)}


def sdk_request(root, url, *, message=None, cancel_event=None):
    """Run the installed SDK in a short owned process; no command from RPC input."""
    import os
    import shutil
    from pathlib import Path
    from .resource_backend import run_owned_command
    node = shutil.which('node')
    if not node:
        raise HarnessError('Prepared MCP runtime requires Node')
    payload = {'url':url, 'operation':'discovery' if message is None else 'call'}
    if message is not None:
        payload['message'] = message
    data = json.dumps(payload).encode()
    if len(data) > 8*1024*1024:
        raise HarnessError('MCP request exceeds resource budget')
    result = run_owned_command([node, str(Path(root)/'scripts/resource-mcp-client.mjs')],
                               os.environ.copy(), 60 if message is None else 610,
                               input_data=data, max_output=8*1024*1024, cancel_event=cancel_event)
    if result.returncode:
        raise HarnessError('MCP resource operation failed; no automatic retry')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise HarnessError('Invalid MCP resource response') from None
