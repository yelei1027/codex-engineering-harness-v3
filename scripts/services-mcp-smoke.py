#!/usr/bin/env python3
"""Use installed upstream MCP client against the running HTTP server."""
import asyncio
import json
from pathlib import Path
from fastmcp import Client

ROOT = Path(__file__).resolve().parent.parent
async def main():
    async with Client('http://127.0.0.1:18080/mcp/') as client:
        tools = await client.list_tools()
        result = {'status': 'PASS', 'transport': 'streamable_http', 'tools': [t.model_dump(mode='json') for t in tools]}
        (ROOT/'reports/powercontext-mcp.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': 'PASS', 'tool_count': len(tools), 'tools': [t.name for t in tools]}))
asyncio.run(main())
