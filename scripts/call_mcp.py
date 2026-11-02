"""One real MCP tool call, for hosts whose tool catalog has not reloaded yet."""
import argparse,asyncio,json,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]

async def call(args):
    arguments=json.loads(Path(args.arguments_file).read_text(encoding='utf-8-sig')) if args.arguments_file else {}
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'mcp_bridge.py')],cwd=str(ROOT))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=60) as client:
            await client.initialize()
            if args.tool=='list_tools':
                result={'tools':[t.model_dump(mode='json',by_alias=True) for t in (await client.list_tools()).tools]}
            else:
                response=await client.call_tool(args.tool,arguments)
                if response.is_error:
                    raise RuntimeError(str(response.content))
                result=response.structured_content or json.loads(next(c.text for c in response.content if c.type=='text'))
            if args.output:
                Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool')
    parser.add_argument('--arguments-file')
    parser.add_argument('--output')
    asyncio.run(call(parser.parse_args()))
