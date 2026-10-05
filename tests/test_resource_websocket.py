import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from harness_core.resource_http import ResourceHTTPServer
from harness_core.resource_runtime import ServiceRuntime
from harness_core.common import HarnessError


class Upgrade(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.end_headers()
        self.connection.settimeout(3)
        try:
            while data:=self.connection.recv(4096):self.connection.sendall(data)
        except OSError:pass
        self.close_connection=True


class ResourceWebSocketCase(unittest.TestCase):
    def test_generation_pins_until_disconnect_and_empty_socket_expires(self):
        calls=[];runtime=ServiceRuntime(lambda timeout:calls.append('start'),lambda:calls.append('stop'))
        backend=ThreadingHTTPServer(('127.0.0.1',0),Upgrade);backend.daemon_threads=True
        gateway=ResourceHTTPServer(('127.0.0.1',0),runtime,f'http://127.0.0.1:{backend.server_port}')
        for server in [backend,gateway]:threading.Thread(target=server.serve_forever,daemon=True).start()
        def connect():
            client=socket.create_connection(('127.0.0.1',gateway.server_port),timeout=3)
            client.sendall(f'GET /generate-code HTTP/1.1\r\nHost: 127.0.0.1:{gateway.server_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n'.encode())
            data=b''
            while b'\r\n\r\n' not in data:data+=client.recv(4096)
            self.assertIn(b'101',data.split(b'\r\n')[0]);return client
        try:
            with connect() as client:
                frame=b'\x81\x82abcd'+bytes([ord('h')^ord('a'),ord('i')^ord('b')])
                client.sendall(frame);self.assertEqual(client.recv(len(frame)),frame)
                self.assertEqual(runtime.status()['active_calls'],1)
                with self.assertRaises(HarnessError):runtime.stop()
            with patch('harness_core.resource_websocket.FIRST_MESSAGE_TIMEOUT',.05):
                with connect() as client:self.assertEqual(client.recv(1),b'')
            self.assertEqual(runtime.status()['active_calls'],0)
            self.assertEqual(calls,['start'])
        finally:
            for server in [gateway,backend]:server.shutdown();server.server_close()


    def test_frame_detection_handles_split_headers_and_coalesced_ping(self):
        from harness_core.resource_websocket import FirstApplicationFrame
        ping=b'\x89\x82abcdXY'
        message=b'\x81\x82abcdHI'
        parser=FirstApplicationFrame()
        self.assertTrue(parser.feed(ping+message))
        parser=FirstApplicationFrame()
        for byte in ping+message[:5]:self.assertFalse(parser.feed(bytes([byte])))
        self.assertTrue(parser.feed(message[5:]))
