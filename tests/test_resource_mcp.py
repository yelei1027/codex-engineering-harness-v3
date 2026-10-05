import unittest
from harness_core.common import HarnessError
from harness_core.resource_runtime import ServiceRuntime
from harness_core.resource_mcp import MCPBridge, discovery_snapshot


class ResourceMCPCase(unittest.TestCase):
    def test_discovery_no_start_tool_call_leased_and_response_preserved(self):
        events=[]
        runtime=ServiceRuntime(lambda timeout:events.append('start'),lambda:events.append('stop'))
        snapshot=discovery_snapshot('owned-image-sha',{'initialize':{'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}},'tools/list':{'tools':[{'name':'recall','inputSchema':{'type':'object'}}]}})
        def invoke(message,cancel_event=None):
            self.assertEqual(runtime.status()['active_calls'],1)
            return {'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':'actual fixture response'}]}}
        bridge=MCPBridge(runtime,snapshot,'owned-image-sha',invoke)
        for method in ['initialize','tools/list','ping']:
            self.assertIn('result',bridge.dispatch({'jsonrpc':'2.0','id':1,'method':method}))
        bridge.dispatch({'jsonrpc':'2.0','method':'notifications/initialized'})
        self.assertEqual(events,[])
        result=bridge.dispatch({'jsonrpc':'2.0','id':42,'method':'tools/call','params':{'name':'recall'}})
        self.assertEqual(result['id'],42);self.assertEqual(events,['start'])
        self.assertEqual(runtime.status()['active_calls'],0)

    def test_stale_or_tampered_discovery_refused(self):
        snapshot=discovery_snapshot('image1',{'initialize':{}})
        with self.assertRaises(HarnessError):MCPBridge(None,snapshot,'image2',None)
        snapshot['results']['initialize']['extra']='modified'
        with self.assertRaises(HarnessError):MCPBridge(None,snapshot,'image1',None)

    def test_call_failure_releases_without_retry(self):
        calls=[];runtime=ServiceRuntime(lambda timeout:None,lambda:None)
        def invoke(message,cancel_event=None):calls.append(message);raise OSError('disconnected')
        bridge=MCPBridge(runtime,discovery_snapshot('v1',{'initialize':{}}),'v1',invoke)
        with self.assertRaises(OSError):bridge.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call'})
        self.assertEqual(len(calls),1);self.assertEqual(runtime.status()['active_calls'],0)

    def test_cancellation_isolated_by_client_session_and_releases_lease(self):
        import threading
        runtime=ServiceRuntime(lambda timeout:None,lambda:None)
        entered={name:threading.Event() for name in ['first','second']}
        cancelled={name:threading.Event() for name in entered}
        def invoke(message,cancel_event=None):
            name=message['params']['name'];entered[name].set()
            if cancel_event.wait(3):cancelled[name].set()
            return {'jsonrpc':'2.0','id':message['id'],'result':{}}
        bridge=MCPBridge(runtime,discovery_snapshot('v1',{'initialize':{}}),'v1',invoke)
        first=bridge.open_session();second=bridge.open_session()
        threads=[]
        for session,name in [(first,'first'),(second,'second')]:
            t=threading.Thread(target=bridge.dispatch,args=({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name}},session));t.start();threads.append(t)
        try:
            for event in entered.values():self.assertTrue(event.wait(2))
            bridge.dispatch({'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':1}},first)
            self.assertTrue(cancelled['first'].wait(2));self.assertFalse(cancelled['second'].is_set())
        finally:
            bridge.close_session(second)
            for t in threads:t.join(4)
        self.assertTrue(cancelled['second'].is_set())
        self.assertEqual(runtime.status()['active_calls'],0)


    def test_idle_abandoned_sessions_expire_without_expiring_active_calls(self):
        import threading
        now=[0]
        bridge=MCPBridge(None,discovery_snapshot('v1',{'initialize':{}}),'v1',None,clock=lambda:now[0],session_idle=30)
        tokens=[bridge.open_session() for _ in range(128)]
        with self.assertRaises(HarnessError):bridge.open_session()
        bridge.calls[(tokens[0],1)]=threading.Event()
        now[0]=31
        fresh=bridge.open_session()
        self.assertTrue(bridge.has_session(tokens[0]));self.assertFalse(bridge.has_session(tokens[1]))
        self.assertTrue(bridge.has_session(fresh))
