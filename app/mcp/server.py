"""Compatibility entrypoint: every MCP call uses the shared HTTP hub."""
import asyncio
from mcp_bridge import create_mcp_server

async def run_mcp_stdio_server():
    await create_mcp_server().run_stdio_async()

if __name__ == "__main__":
    asyncio.run(run_mcp_stdio_server())
