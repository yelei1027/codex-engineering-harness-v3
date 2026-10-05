import http.client
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from harness_core.resource_http import ResourceHTTPServer
from harness_core.resource_runtime import ServiceRuntime


class Echo(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        data=self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200);self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)


class ResourceHTTPCase(unittest.TestCase):
    def test_health_stays_dormant_actual_request_wakes_and_idle_stops(self):
        calls=[];now=[0];stopped=threading.Event()
        def stop():calls.append('stop');stopped.set()
        runtime=ServiceRuntime(lambda timeout:calls.append('start'),stop,clock=lambda:now[0])
        backend=ThreadingHTTPServer(('127.0.0.1',0),Echo)
        gateway=ResourceHTTPServer(('127.0.0.1',0),runtime,f'http://127.0.0.1:{backend.server_port}',control_token='test-control')
        threads=[threading.Thread(target=s.serve_forever) for s in (backend,gateway)]
        for t in threads:t.start()
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',gateway.server_port,timeout=3)
            try:
                conn.request(method,path,body,headers or {})
                response=conn.getresponse();return response.status,response.read()
            finally:conn.close()
        try:
            self.assertEqual(request('GET','/health/ready')[0],200)
            self.assertEqual(calls,[])
            self.assertEqual(request('POST','/_harness/start')[0],403)
            self.assertEqual(calls,[])
            self.assertEqual(request('POST','/v1/example',b'payload'),(200,b'payload'))
            self.assertEqual(calls,['start']);self.assertEqual(runtime.status()['active_calls'],0)
            self.assertEqual(request('POST','/v1/example',b'payload',{'Origin':'https://external.invalid'})[0],403)
            now[0]=120;self.assertTrue(stopped.wait(3));self.assertEqual(calls,['start','stop'])
            self.assertEqual(request('POST','/_harness/start',headers={'Authorization':'Bearer test-control'})[0],200)
            self.assertEqual(runtime.status()['mode'],'MANUAL')
            self.assertEqual(request('POST','/_harness/stop',headers={'Authorization':'Bearer test-control'})[0],200)
        finally:
            for server in (gateway,backend):server.shutdown();server.server_close()
            for thread in threads:thread.join(3)

    def test_full_work_capacity_still_accepts_cancel_delete_and_status(self):
        import json
        import time
        from harness_core.resource_mcp import MCPBridge,discovery_snapshot
        runtime=ServiceRuntime(lambda timeout:None,lambda:None)
        entered=[threading.Event() for _ in range(8)]
        cancelled=[threading.Event() for _ in range(8)]
        release=threading.Event()
        def invoke(message,cancel_event=None):
            index=message['id'];entered[index].set()
            while not release.wait(.01):
                if cancel_event.is_set():cancelled[index].set();break
            return {'jsonrpc':'2.0','id':index,'result':{}}
        bridge=MCPBridge(runtime,discovery_snapshot('test',{'initialize':{}}),'test',invoke)
        gateway=ResourceHTTPServer(('127.0.0.1',0),runtime,'http://127.0.0.1:12345',mcp=bridge,control_token='control')
        server_thread=threading.Thread(target=gateway.serve_forever);server_thread.start()
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',gateway.server_port,timeout=5)
            try:
                conn.request(method,path,json.dumps(body) if body is not None else None,headers or {})
                response=conn.getresponse();response.read();return response.status
            finally:conn.close()
        tokens=[bridge.open_session() for _ in range(8)]
        workers=[];results=[]
        try:
            for i in range(8):
                t=threading.Thread(target=lambda index=i:results.append(request('POST','/mcp/',{'jsonrpc':'2.0','id':index,'method':'tools/call'}, {'Mcp-Session-Id':tokens[index]})))
                t.start();workers.append(t)
            for event in entered:self.assertTrue(event.wait(3))
            self.assertEqual(runtime.status()['active_calls'],8)
            self.assertEqual(request('GET','/_harness/status',headers={'Authorization':'Bearer control'}),200)
            self.assertEqual(request('POST','/mcp/',{'jsonrpc':'2.0','id':100,'method':'tools/call'}, {'Mcp-Session-Id':tokens[0]}),503)
            self.assertEqual(request('POST','/mcp/',{'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':0}}, {'Mcp-Session-Id':tokens[0]}),202)
            self.assertTrue(cancelled[0].wait(2))
            self.assertFalse(cancelled[1].is_set())
            self.assertEqual(request('DELETE','/mcp/',headers={'Mcp-Session-Id':tokens[1]}),200)
            self.assertTrue(cancelled[1].wait(2))
        finally:
            release.set()
            for t in workers:t.join(6)
            gateway.shutdown();gateway.server_close();server_thread.join(3)
        self.assertEqual(results,[200]*8)
        self.assertEqual(runtime.status()['active_calls'],0)
