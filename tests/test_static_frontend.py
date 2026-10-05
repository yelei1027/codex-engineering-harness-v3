import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node required for real static server')
class StaticFrontendCase(unittest.TestCase):
    def test_serves_build_and_spa_without_exposing_symlink_or_missing_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'dist'; root.mkdir()
            (root/'index.html').write_text('<h1>static build</h1>')
            (root/'asset.js').write_text('console.log("built")')
            outside = Path(folder)/'private.txt'; outside.write_text('private marker')
            (root/'escape.txt').symlink_to(outside)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = {**os.environ, 'STATIC_ROOT':str(root), 'HOST':'127.0.0.1', 'PORT':str(port)}
            process = subprocess.Popen(['node',str(ROOT/'templates/services/screenshot-to-code/frontend-server.mjs')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/', timeout=.2) as response:
                            self.assertIn(b'static build', response.read())
                        break
                    except URLError:
                        if process.poll() is not None: self.fail(process.stderr.read().decode())
                        time.sleep(.02)
                else: self.fail('server did not become ready')
                with urlopen(f'http://127.0.0.1:{port}/project/new') as response:
                    self.assertIn(b'static build',response.read())
                with urlopen(f'http://127.0.0.1:{port}/asset.js') as response:
                    self.assertIn('javascript',response.headers['Content-Type'])
                for name, code in [('escape.txt',403),('missing.js',404)]:
                    with self.assertRaises(HTTPError) as caught: urlopen(f'http://127.0.0.1:{port}/{name}')
                    self.assertEqual(caught.exception.code,code)
                    caught.exception.close()
            finally:
                process.terminate();process.wait(timeout=3);process.stderr.close()

    def test_http_body_query_and_websocket_upgrade_reach_backend(self):
        import json
        import http.client
        backend_code = r'''
const http=require('node:http'),crypto=require('node:crypto');
const s=http.createServer((q,r)=>{let chunks=[];q.on('data',x=>chunks.push(x));q.on('end',()=>{r.setHeader('Content-Type','application/json');r.end(JSON.stringify({url:q.url,method:q.method,body:Buffer.concat(chunks).toString()}));});});
s.on('upgrade',(q,socket,head)=>{const accept=crypto.createHash('sha1').update(q.headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n');if(head.length)socket.write(head);socket.on('data',data=>socket.write(data));});
s.listen(0,'127.0.0.1',()=>console.log(s.address().port));
'''
        backend = subprocess.Popen(['node','-e',backend_code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(backend.stderr.close);self.addCleanup(backend.stdout.close)
        def end(process):
            if process.poll() is None:process.terminate()
            process.wait(timeout=3)
        self.addCleanup(end,backend)
        backend_port = int(backend.stdout.readline())
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'index.html').write_text('ready')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={**os.environ,'STATIC_ROOT':folder,'HOST':'127.0.0.1','PORT':str(port),
                 'PROXY_CODEGEN_BACKEND':f'http://127.0.0.1:{backend_port}'}
            frontend=subprocess.Popen(['node',str(ROOT/'templates/services/screenshot-to-code/frontend-server.mjs')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/',timeout=.2):pass
                        break
                    except URLError:time.sleep(.02)
                else:self.fail('frontend not ready')
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                try:
                    conn.request('POST','/api/example?value=1',body='request-body')
                    response=conn.getresponse();self.assertEqual(response.status,200)
                    self.assertEqual(json.loads(response.read()),{'url':'/api/example?value=1','method':'POST','body':'request-body'})
                finally:conn.close()
                with socket.create_connection(('127.0.0.1',port),timeout=3) as client:
                    client.sendall(b'GET /generate-code HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n')
                    response=b''
                    while b'\r\n\r\n' not in response:response+=client.recv(4096)
                    self.assertTrue(response.startswith(b'HTTP/1.1 101'))
                    self.assertIn(b's3pPLMBiTxaQ9kYGzzhZRbK+xOo=',response)
                    marker=b'\x81\x02hi';client.sendall(marker)
                    self.assertEqual(client.recv(4),marker)
            finally:end(frontend);frontend.stderr.close()
