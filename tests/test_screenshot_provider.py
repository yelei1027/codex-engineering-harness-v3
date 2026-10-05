"""Owned Anthropic-compatible registration/terminal guards; live provider tested separately."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('harness_provider',ROOT/'templates/services/screenshot-to-code/harness_provider.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)

class Stream:
    def __init__(self,reason='end_turn',cancel=False):
        self.reason=reason;self.cancel=cancel;self.closed=False;self.emitted=False
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True
    def __aiter__(self):return self
    async def __anext__(self):
        if self.cancel:raise asyncio.CancelledError()
        if self.emitted:raise StopAsyncIteration
        self.emitted=True;return NS(text='generated code')
    async def get_final_message(self):return NS(stop_reason=self.reason)

class ScreenshotProviderCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        names=['agent','agent.providers','agent.providers.anthropic','agent.providers.anthropic.provider']
        modules={name:ModuleType(name) for name in names};self.upstream=modules[names[-1]]
        modules[names[-2]].provider=self.upstream
        self.upstream.AnthropicParseState=lambda:NS(assistant_text='')
        async def parse(event,state,sink):state.assistant_text+=event.text;await sink(event)
        self.upstream._parse_stream_event=parse
        self.upstream._extract_anthropic_usage=Mock(return_value=NS(tokens=7))
        self.upstream._extract_tool_calls=Mock(return_value=[])
        self.upstream.ProviderTurn=NS
        scope=patch.dict(sys.modules,modules);scope.start();self.addCleanup(scope.stop)
        self.configuration={'model':'deepseek-v4.1-flash','max_tokens':1024,'max_turns':2}

    def session(self,stream):
        return NS(_ensure_many_image_dimension_limit=Mock(),_system_prompt='system',_messages=[],
                  _tools=[{'name':'create_file'}],_prompt_report_logger=Mock(),_recorder=Mock(),
                  _total_usage=Mock(),_client=NS(messages=NS(stream=Mock(return_value=stream))))

    async def test_native_parser_and_wire_model_limits_are_used(self):
        stream=Stream();session=self.session(stream);sink=AsyncMock()
        result=await provider.stream_configured(session,sink,self.configuration)
        self.assertEqual(result.assistant_text,'generated code')
        sent=session._client.messages.stream.call_args.kwargs
        self.assertEqual(sent['model'],'deepseek-v4.1-flash');self.assertEqual(sent['max_tokens'],1024)
        self.assertTrue(stream.closed);sink.assert_awaited_once()
        session._total_usage.accumulate.assert_called_once()

    async def test_truncated_generation_is_rejected_after_stream_closed(self):
        stream=Stream('max_tokens');session=self.session(stream)
        with self.assertRaises(RuntimeError):await provider.stream_configured(session,AsyncMock(),self.configuration)
        self.assertTrue(stream.closed);session._client.messages.stream.assert_called_once()

    async def test_cancel_closes_context_and_does_not_retry(self):
        stream=Stream(cancel=True);session=self.session(stream)
        with self.assertRaises(asyncio.CancelledError):await provider.stream_configured(session,AsyncMock(),self.configuration)
        self.assertTrue(stream.closed);session._client.messages.stream.assert_called_once()

    async def test_unadvertised_tools_cannot_reach_agent_runtime(self):
        self.upstream._extract_tool_calls.return_value=[NS(id='a',name='unknown_tool',arguments={})]
        with self.assertRaises(RuntimeError):await provider.stream_configured(self.session(Stream('tool_use')),AsyncMock(),self.configuration)

    async def test_turn_budget_prevents_another_network_request(self):
        session=self.session(Stream());session._harness_turns=2
        with self.assertRaises(RuntimeError):await provider.stream_configured(session,AsyncMock(),self.configuration)
        session._client.messages.stream.assert_not_called()

    async def test_endpoint_normalization_and_empty_opt_in(self):
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(provider.settings())
        base={'HARNESS_SCREENSHOT_MODEL':'deepseek-v4.1-flash','ANTHROPIC_API_KEY':'test-only','ANTHROPIC_BASE_URL':'https://relay.example/v1'}
        with patch.dict(os.environ,base,clear=True):self.assertEqual(provider.settings()['base_url'],'https://relay.example')
        for changed in [{'ANTHROPIC_BASE_URL':'http://relay.example/v1'},{'ANTHROPIC_BASE_URL':'https://user:password@relay.example/v1'},{'HARNESS_SCREENSHOT_MAX_TURNS':'0'}]:
            with patch.dict(os.environ,{**base,**changed},clear=True),self.assertRaises(ValueError):provider.settings()

    async def test_default_application_is_unchanged(self):
        main=ModuleType('main');main.app=object()
        with patch.dict(os.environ,{},clear=True),patch.dict(sys.modules,{'main':main}):
            self.assertIs(provider.create_app(),main.app)
