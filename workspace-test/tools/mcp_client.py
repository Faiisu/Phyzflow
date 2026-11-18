"""Interactive client: send JSON tool calls to a real stdio MCP subprocess."""
import asyncio
import json
import os
import sys
from mcp import Client, StdioServerParameters

async def main():
    params = StdioServerParameters(command=sys.argv[1], args=sys.argv[2:], env={key: os.environ[key] for key in ('PATH', 'SYSTEMROOT') if key in os.environ})
    async with Client(params, raise_exceptions=False) as client:
        discovery = await client.list_tools()
        print(json.dumps({'tools': discovery.model_dump(mode='json', by_alias=True)}), flush=True)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            request = json.loads(line)
            result = await client.call_tool(request['name'], request.get('arguments', {}))
            print(json.dumps({'request': request, 'response': result.model_dump(mode='json', by_alias=True)}), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
