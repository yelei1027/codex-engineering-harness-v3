"""Opt-in model registration for an Anthropic-compatible screenshot provider.

Reuse the pinned upstream Anthropic session, stream parser and tool runtime.
No vendor source or existing candidate is patched.
"""
import os
import re
from enum import Enum
from urllib.parse import urlsplit


def settings():
    model = os.environ.get('HARNESS_SCREENSHOT_MODEL', '').strip()
    if not model:
        return None
    endpoint = os.environ.get('ANTHROPIC_BASE_URL', '').rstrip('/')
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Configured model requires an explicit HTTPS Anthropic API base URL')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model):
        raise ValueError('Invalid configured model identifier')
    if not os.environ.get('ANTHROPIC_API_KEY'):
        raise ValueError('Configured model requires ANTHROPIC_API_KEY')
    # The Anthropic SDK appends /v1/messages itself.
    if endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    variants = int(os.environ.get('HARNESS_SCREENSHOT_VARIANTS', '1'))
    tokens = int(os.environ.get('HARNESS_SCREENSHOT_MAX_TOKENS', '8192'))
    turns = int(os.environ.get('HARNESS_SCREENSHOT_MAX_TURNS', '8'))
    if not 1 <= variants <= 4 or not 256 <= tokens <= 32768 or not 1 <= turns <= 30:
        raise ValueError('Invalid configured generation limits')
    return {'model': model, 'base_url': endpoint, 'variants': variants,
            'max_tokens': tokens, 'max_turns': turns}


async def stream_configured(session, on_event, configuration):
    """Use upstream parsing/usage accounting with explicit limits and terminal checks."""
    from agent.providers.anthropic import provider as upstream
    count = getattr(session, '_harness_turns', 0)
    if count >= configuration['max_turns']:
        raise RuntimeError('Configured generation turn limit reached')
    session._harness_turns = count + 1
    session._ensure_many_image_dimension_limit()
    params = {'model': configuration['model'], 'max_tokens': configuration['max_tokens'],
              'system': session._system_prompt, 'messages': session._messages,
              'tools': session._tools, 'temperature': 0.0}
    session._prompt_report_logger.record_request(params)
    if session._recorder:
        session._recorder.record_llm_request('anthropic-compatible', params['model'], params)
    state = upstream.AnthropicParseState()
    try:
        async with session._client.messages.stream(**params) as stream:
            async for event in stream:
                await upstream._parse_stream_event(event, state, on_event)
            final = await stream.get_final_message()
        if final.stop_reason not in {'end_turn', 'tool_use', 'stop_sequence'}:
            raise RuntimeError('Provider response did not finish completely')
        usage = upstream._extract_anthropic_usage(final)
        session._prompt_report_logger.record_usage(usage)
        session._total_usage.accumulate(usage)
        tools = upstream._extract_tool_calls(final)
        allowed = {tool['name'] for tool in session._tools}
        if (len(tools) > 64 or any(not tool.id or tool.name not in allowed for tool in tools)
                or final.stop_reason == 'tool_use' and not tools):
            raise RuntimeError('Provider returned an unadvertised tool')
        if len({tool.id for tool in tools}) != len(tools):
            raise RuntimeError('Provider returned duplicate tool IDs')
        if session._recorder:
            session._recorder.record_llm_response(state.assistant_text, tools, usage)
        return upstream.ProviderTurn(assistant_text=state.assistant_text,
                                     tool_calls=tools, assistant_turn=final)
    except Exception:
        # Provider error bodies may echo credentials; cancellation still propagates.
        raise RuntimeError('Anthropic-compatible generation failed; inspect provider compatibility') from None


def create_app():
    configuration = settings()
    if configuration:
        import httpx
        from anthropic import AsyncAnthropic
        import config
        import llm
        from agent import engine
        from agent.providers import factory
        from agent.providers.anthropic import AnthropicProviderSession, serialize_anthropic_tools
        from agent.tools import canonical_tool_definitions
        from preview_screenshot import is_screenshot_preview_available
        from routes import generate_code
        configured_model = Enum('ConfiguredModel', {'CUSTOM': configuration['model']}).CUSTOM
        llm.MODEL_PROVIDER[configured_model] = 'anthropic-compatible'
        original_factory = factory.create_provider_session
        original_selection = generate_code.ModelSelectionStage._get_variant_models
        original_status = generate_code.StatusBroadcastMiddleware.process

        class ConfiguredSession(AnthropicProviderSession):
            async def stream_turn(self, on_event):
                return await stream_configured(self, on_event, configuration)

        def select(self, generation_type, input_mode, num_variants,
                   openai_api_key, anthropic_api_key, gemini_api_key):
            if input_mode == 'video':
                return original_selection(self, generation_type, input_mode, num_variants,
                                          openai_api_key, anthropic_api_key, gemini_api_key)
            return [configured_model] * configuration['variants']

        async def status(self, context, next_func):
            if context.extracted_params.input_mode == 'video':
                return await original_status(self, context, next_func)
            count = configuration['variants']
            await context.send_message('variantCount', str(count), 0)
            await context.send_message('variantModels', None, 0,
                                       {'models': [configuration['model']] * count}, None)
            for index in range(count):
                await context.send_message('status', 'Generating code...', index)
            await next_func()

        def create(model, prompt_messages, should_generate_images, openai_api_key,
                   openai_base_url, anthropic_api_key, gemini_api_key,
                   replicate_api_key=None, should_extract_assets=True, recorder=None):
            if model != configured_model:
                return original_factory(model, prompt_messages, should_generate_images,
                                        openai_api_key, openai_base_url, anthropic_api_key,
                                        gemini_api_key, replicate_api_key, should_extract_assets, recorder)
            tools = canonical_tool_definitions(
                image_generation_enabled=should_generate_images and bool(replicate_api_key or config.REPLICATE_API_KEY),
                image_editing_enabled=bool(replicate_api_key or config.REPLICATE_API_KEY),
                asset_extraction_enabled=should_extract_assets and bool(gemini_api_key),
                screenshot_enabled=is_screenshot_preview_available())
            # Request input cannot redirect the administrator's endpoint or key.
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY,
                                    base_url=configuration['base_url'], max_retries=0,
                                    http_client=httpx.AsyncClient(follow_redirects=False,
                                                                 trust_env=False, timeout=120))
            return ConfiguredSession(client, model, prompt_messages,
                                     serialize_anthropic_tools(tools), recorder)

        generate_code.ModelSelectionStage._get_variant_models = select
        generate_code.StatusBroadcastMiddleware.process = status
        factory.create_provider_session = create
        engine.create_provider_session = create
    from main import app
    return app
