"""Loopback HTTP transport for one preselected owned backend.

No lifecycle paths, commands or upstream addresses are accepted from requests.
MCP and WebSocket adapters are separate; this layer handles finite HTTP traffic.
"""
import http.client
import json
import hmac
import select
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .common import HarnessError

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}


class ResourceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def __init__(self, address, runtime, upstream, *, max_body=8*1024*1024, mcp=None, control_token=None):
        host = urlsplit(upstream)
        if address[0] != '127.0.0.1' or host.scheme != 'http' or host.hostname != '127.0.0.1':
            raise HarnessError('Resource transport must use loopback HTTP')
        if host.username or host.password or host.query or host.fragment or host.path not in ('', '/'):
            raise HarnessError('Invalid owned upstream')
        self.control_token = control_token
        self.mcp = mcp
        self.runtime = runtime
        self.upstream = (host.hostname, host.port or 80)
        self.max_body = max_body
        # Keep transport headroom for cancellation/status when all work slots are busy.
        self.slots = threading.BoundedSemaphore(16)
        self.work_slots = threading.BoundedSemaphore(8)
        self.shutdown_event = threading.Event()
        super().__init__(address, ResourceHandler)
        self.reaper = threading.Thread(target=self._reap, daemon=True)
        self.reaper.start()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def _reap(self):
        while not self.shutdown_event.wait(1):
            try:
                self.runtime.stop(idle_only=True)
            except (HarnessError, OSError):
                # Failure is retained in runtime status, not mistaken for stopped.
                pass

    def server_close(self):
        self.shutdown_event.set()
        super().server_close()
        self.reaper.join(timeout=2)


class WorkCapacity(HarnessError):
    pass


class ResourceHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    rbufsize = 0

    def log_message(self, *args):
        pass  # Request paths/bodies may contain project data.

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def reply(self, status, value, extra_headers=None):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        for key,value in (extra_headers or {}).items():self.send_header(key,value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_GET(self): self.forward()
    def do_HEAD(self): self.forward()
    def do_POST(self): self.forward()
    def do_DELETE(self): self.forward()
    def do_PUT(self): self.forward()
    def do_PATCH(self): self.forward()

    @contextmanager
    def work(self):
        if not self.server.work_slots.acquire(blocking=False):
            raise WorkCapacity('Resource call capacity reached')
        try:
            yield
        finally:
            self.server.work_slots.release()

    def forward(self):
        try:
            self._forward()
        except WorkCapacity:
            self.reply(503, {'error':'RESOURCE_CALL_CAPACITY'})

    def _forward(self):
        target = urlsplit(self.path)
        origin = self.headers.get('Origin')
        allowed_host = '127.0.0.1:'+str(self.server.server_port)
        if (target.scheme or target.netloc or not self.path.startswith('/') or
                self.headers.get('Host') != allowed_host or
                (origin is not None and origin != 'http://'+allowed_host)):
            self.reply(403, {'error': 'LOCAL_ORIGIN_REQUIRED'})
            return
        if target.path.startswith('/_harness/'):
            supplied = self.headers.get('Authorization', '')
            expected = 'Bearer '+(self.server.control_token or '')
            if not self.server.control_token or not hmac.compare_digest(supplied, expected):
                self.reply(403, {'error':'CONTROL_AUTH_REQUIRED'})
                return
            if target.path == '/_harness/status' and self.command == 'GET':
                self.reply(200, self.server.runtime.status())
                return
            if self.command != 'POST' or target.path not in ('/_harness/start', '/_harness/stop', '/_harness/quiesce', '/_harness/resume'):
                self.reply(404, {'error':'UNKNOWN_CONTROL_OPERATION'})
                return
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
                self.reply(400, {'error':'CONTROL_BODY_NOT_ALLOWED'})
                return
            try:
                if target.path.endswith('/start'):
                    with self.work():self.server.runtime.start_manual()
                elif target.path.endswith('/quiesce'):
                    self.server.runtime.quiesce()
                elif target.path.endswith('/resume'):
                    self.server.runtime.resume()
                else:
                    self.server.runtime.stop()
                self.reply(200, self.server.runtime.status())
            except (HarnessError, OSError):
                self.reply(409, {'error':'RESOURCE_BUSY_OR_UNAVAILABLE'})
            return
        if target.path in ('/health/ready', '/health/live') and self.command in ('GET', 'HEAD'):
            self.reply(200, {'status':'ready', 'resource':self.server.runtime.status()})
            return
        if self.headers.get('Upgrade') and target.path == '/generate-code':
            from .resource_websocket import generation_tunnel
            with self.work():generation_tunnel(self)
            return
        if (target.path.startswith('/mcp') and self.server.mcp is None) or self.headers.get('Upgrade'):
            self.reply(501, {'error':'PROTOCOL_ADAPTER_REQUIRED'})
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
            self.reply(400, {'error':'AMBIGUOUS_BODY_LENGTH'})
            return
        try:
            length = int(lengths[0]) if lengths else 0
        except ValueError:
            self.reply(400, {'error':'INVALID_BODY_LENGTH'})
            return
        if length < 0 or length > self.server.max_body:
            self.reply(413, {'error':'REQUEST_TOO_LARGE'})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            return
        if target.path in ('/mcp', '/mcp/'):
            bridge=self.server.mcp
            session=self.headers.get('Mcp-Session-Id')
            if self.command=='DELETE':
                if not bridge.has_session(session):self.reply(404,{'error':'UNKNOWN_MCP_SESSION'});return
                bridge.close_session(session)
                self.reply(200,{})
                return
            if self.command != 'POST':
                self.reply(405, {'error':'USE_POST_TRANSPORT'})
                return
            try:
                message = json.loads(body)
            except (ValueError, UnicodeError):
                self.reply(400, {'error':'INVALID_JSON'})
                return
            if not isinstance(message,dict):
                self.reply(400,{'error':'INVALID_MCP_REQUEST'});return
            initializing=message.get('method')=='initialize'
            if initializing:
                try:session=bridge.open_session()
                except HarnessError:self.reply(503,{'error':'MCP_SESSION_CAPACITY'});return
            elif not bridge.has_session(session):
                self.reply(404,{'error':'UNKNOWN_MCP_SESSION'});return
            completed=threading.Event()
            def disconnected():
                while not completed.wait(.1):
                    try:
                        readable,_,_=select.select([self.connection],[],[],0)
                        if readable and not self.connection.recv(1,socket.MSG_PEEK):
                            bridge.close_session(session);return
                    except OSError:
                        bridge.close_session(session);return
            monitor=threading.Thread(target=disconnected,daemon=True);monitor.start()
            try:
                from .resource_mcp import DISCOVERY
                if 'id' not in message or message.get('method') in {'initialize','ping',*DISCOVERY}:
                    result = bridge.dispatch(message,session=session)
                else:
                    with self.work():result = bridge.dispatch(message,session=session)
                if result is None:
                    self.send_response(202)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                else:
                    self.reply(200,result,{'Mcp-Session-Id':session} if initializing else None)
            except (HarnessError, OSError):
                self.reply(503, {'error':'RESOURCE_UNAVAILABLE'})
            finally:
                completed.set();monitor.join(timeout=1)
            return
        connection_tokens = {v.strip().lower() for v in self.headers.get('Connection','').split(',')}
        excluded = HOP_HEADERS | connection_tokens | {'host','content-length'}
        headers = {k:v for k,v in self.headers.items() if k.lower() not in excluded}
        headers['Content-Length'] = str(length)
        started = False
        connection = http.client.HTTPConnection(*self.server.upstream, timeout=60)
        try:
            with self.work(), self.server.runtime.use():
                connection.request(self.command, self.path, body, headers)
                response = connection.getresponse()
                self.send_response(response.status)
                response_tokens = {v.strip().lower() for v in response.getheader('Connection','').split(',')}
                for key, value in response.getheaders():
                    if key.lower() not in HOP_HEADERS | response_tokens:
                        self.send_header(key, value)
                self.end_headers()
                started = True
                if self.command != 'HEAD':
                    while chunk := response.read(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
        except (HarnessError, OSError, http.client.HTTPException):
            if not started:
                self.reply(503, {'error':'RESOURCE_UNAVAILABLE'})
        finally:
            connection.close()
            self.close_connection = True
