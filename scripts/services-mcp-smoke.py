#!/usr/bin/env python3
"""Use installed upstream MCP client against the running HTTP server."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness_core.service_reports import prepare_outputs
REPORTS, fixture_path = prepare_outputs(ROOT)

from fastmcp import Client

async def main():
    async with Client(json.loads((ROOT/'services-binding.json').read_text())['mcp_servers']['powercontext']['url']) as client:
        tools = await client.list_tools()
        result = {'status': 'PASS', 'transport': 'streamable_http', 'tools': [t.model_dump(mode='json') for t in tools]}
        (REPORTS/'powercontext-mcp.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': 'PASS', 'tool_count': len(tools), 'tools': [t.name for t in tools]}))
asyncio.run(main())
