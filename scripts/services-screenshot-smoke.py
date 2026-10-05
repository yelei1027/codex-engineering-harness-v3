#!/usr/bin/env python3
"""Run inside the upstream backend environment; render a real local fixture."""
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from preview_screenshot import capture_preview_screenshot, probe_screenshot_preview
from preview_screenshot.registry import _backend

async def main():
    assert await probe_screenshot_preview()
    data = await capture_preview_screenshot('<!doctype html><html lang="en"><meta charset="utf-8"><style>body{font:24px sans-serif;padding:40px;background:#edf3ff}main{padding:32px;background:white;border-radius:12px}</style><main><h1>Harness local screenshot fixture</h1><p>Upstream Playwright capture renders this HTML.</p></main></html>')
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    Path('/tmp/screenshot-preview.png').write_bytes(data)
    providers = {key: bool(os.environ.get(key)) for key in ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY']}
    result = {'status': 'PASS', 'preview': {'status': 'PASS', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'artifact': 'private/service-reports/screenshot-preview.png'},
              'playwright_version': importlib.metadata.version('playwright'),
              'provider_configured': providers,
              'generation': {'status': 'NOT_TESTED' if any(providers.values()) else 'OPTIONAL_PROVIDER_BLOCK',
                             'detail': 'Live model generation requires an authorized provider key; fixture only exercises the upstream local screenshot renderer.'}}
    Path('/tmp/screenshot-smoke.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))

async def managed_main():
    try:
        await main()
    finally:
        # This pinned upstream renderer keeps one Chromium process for long-lived
        # servers. A one-shot fixture must close it before the event loop exits.
        if _backend._browser is not None:
            await _backend._browser.close()
        if _backend._playwright is not None:
            await _backend._playwright.stop()

asyncio.run(managed_main())
