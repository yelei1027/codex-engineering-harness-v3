"""Bounded same-origin tunnel for screenshot generation's one-request socket.

The pinned frontend opens this socket only for a generation and the backend
closes it on completion. A client that never sends data cannot pin the service.
"""
import select
import socket
import time
from .common import HarnessError

FIRST_MESSAGE_TIMEOUT = 10


class FirstApplicationFrame:
    """Track frame boundaries across TCP chunks without buffering frame payloads."""
    def __init__(self):
        self.header=bytearray()
        self.remaining=0

    def feed(self,data):
        offset=0
        while offset<len(data):
            if self.remaining:
                skipped=min(self.remaining,len(data)-offset)
                self.remaining-=skipped;offset+=skipped
                continue
            needed=2 if len(self.header)<2 else 2+(2 if self.header[1]&127==126 else 8 if self.header[1]&127==127 else 0)+4
            take=min(needed-len(self.header),len(data)-offset)
            self.header.extend(data[offset:offset+take]);offset+=take
            if len(self.header)<2:continue
            if not self.header[1]&128:raise HarnessError('Client WebSocket frames must be masked')
            size=self.header[1]&127
            needed=2+(2 if size==126 else 8 if size==127 else 0)+4
            if len(self.header)<needed:continue
            opcode=self.header[0]&15
            length=int.from_bytes(self.header[2:4] if size==126 else self.header[2:10],'big') if size>=126 else size
            if opcode in (1,2):return True
            if opcode not in (8,9,10) or length>125 or not self.header[0]&128:
                raise HarnessError('Invalid initial WebSocket control frame')
            self.remaining=length;self.header.clear()
        return False


def generation_tunnel(handler):
    if (handler.command != 'GET' or handler.headers.get('Upgrade','').lower() != 'websocket'
            or handler.headers.get('Sec-WebSocket-Version') != '13'
            or handler.headers.get('Content-Length', '0') != '0'
            or handler.headers.get('Transfer-Encoding')):
        handler.reply(400, {'error':'INVALID_WEBSOCKET_UPGRADE'})
        return
    sent = False
    remote = None
    try:
        with handler.server.runtime.use():
            remote = socket.create_connection(handler.server.upstream, timeout=10)
            headers = [(key, value) for key,value in handler.headers.items()
                       if key.lower() not in {'host','authorization','proxy-authorization'}]
            host,port = handler.server.upstream
            request = ('GET '+handler.path+' HTTP/1.1\r\nHost: '+host+':'+str(port)+'\r\n'+
                       ''.join(key+': '+value+'\r\n' for key,value in headers)+'\r\n')
            remote.sendall(request.encode('latin1'))
            response = bytearray()
            while not response.endswith(b'\r\n\r\n'):
                byte = remote.recv(1)
                if not byte or len(response) >= 65536:
                    raise HarnessError('Invalid backend upgrade')
                response.extend(byte)
            if not bytes(response).split(b'\r\n',1)[0].startswith(b'HTTP/1.1 101 '):
                raise HarnessError('Backend refused WebSocket upgrade')
            handler.connection.sendall(response)
            sent = True
            deadline = time.monotonic()+FIRST_MESSAGE_TIMEOUT
            active = False
            detector = FirstApplicationFrame()
            remote.settimeout(10)
            client = handler.connection
            while not handler.server.shutdown_event.is_set():
                if not active and time.monotonic() >= deadline:
                    break
                readable,_,_ = select.select([client,remote],[],[],.2)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    if source is client:
                        # First application frame identifies an actual generation.
                        # Ping/control frames alone cannot create a lasting lease.
                        if not active and detector.feed(data):
                            active = True
                        remote.sendall(data)
                    else:
                        client.sendall(data)
    except (OSError, HarnessError):
        if not sent:
            handler.reply(503, {'error':'SCREENSHOT_RESOURCE_UNAVAILABLE'})
    finally:
        if remote:
            remote.close()
        handler.close_connection = True
