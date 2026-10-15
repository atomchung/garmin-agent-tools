"""Credential-free stdio protocol smoke; no Garmin request is made."""
import asyncio
import os
import sys
import tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    with tempfile.TemporaryDirectory() as directory:
        env=dict(os.environ,GARMIN_AGENT_HOME=directory)
        env.pop('GARMIN_AGENT_SESSION',None)
        params=StdioServerParameters(command=sys.executable,args=['-m','garmin_agent_tools.cli','mcp'],env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                info=await session.initialize()
                tools=await session.list_tools()
                assert {t.name for t in tools.tools}=={'read_snapshot','read_workout','preview_test','apply_test'}
                response=await session.call_tool('read_workout',{'workout_id':1})
                assert 'Not authenticated' in str(response)
                print('MCP initialize/list/call passed without credentials; protocol='+info.protocolVersion)

if __name__=='__main__':asyncio.run(main())
