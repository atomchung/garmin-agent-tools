"""Initialize a real local Personal MCP; --live reads explicit dates, counts only."""
import argparse
import asyncio
import datetime as dt
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def decode(result):
    if result.isError:
        raise RuntimeError('mcp_call_failed')
    value=result.structuredContent or json.loads(result.content[0].text)
    if value.get('status')=='failed':
        print(json.dumps(value,ensure_ascii=False),flush=True)
        raise RuntimeError('provider_or_local_failure_no_retry')
    return value


async def invoke(dates):
    params=StdioServerParameters(command=sys.executable,args=['-m','garmin_agent_tools.cli','mcp','--backend','personal'],env=dict(os.environ))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            initialized=await session.initialize()
            tools=await session.list_tools()
            assert {t.name for t in tools.tools}=={'read_snapshot','get_connection_status'}
            assert all(t.annotations.readOnlyHint for t in tools.tools)
            print(json.dumps({'stage':'mcp_initialize','protocol':initialized.protocolVersion,'tools':[t.name for t in tools.tools],'all_read_only':True}),flush=True)
            status=decode(await session.call_tool('get_connection_status',{}))
            print(json.dumps({'stage':'local_status','result':status}),flush=True)
            if dates and (not status['key_configured'] or status['attempt_stopped']):
                raise RuntimeError('private_setup_required_or_stopped_no_provider_calls')
            for date in dates:
                value=decode(await session.call_tool('read_snapshot',{'date':date}))
                print(json.dumps({'stage':'snapshot','result':value},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    args=parser.parse_args()
    dates=[]
    if args.live:
        if not args.start_date or not args.end_date:
            parser.error('--live requires explicit --start-date and --end-date')
        start=dt.date.fromisoformat(args.start_date);end=dt.date.fromisoformat(args.end_date)
        if not 0 <= (end-start).days <= 13:
            parser.error('Choose between one and fourteen explicit dates')
        dates=[(start+dt.timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    try:
        asyncio.run(invoke(dates))
    except Exception:
        print('{"status":"failed","automatic_retry":false}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
