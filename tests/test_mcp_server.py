import pytest
from app.mcp.server import create_mcp_server

@pytest.mark.asyncio
async def test_mcp_server_initialization_and_tools():
    server=create_mcp_server()
    assert server.name=="local-media-hub"
    names={tool.name for tool in await server.list_tools()}
    assert {"generate_image","get_job","get_capabilities","cancel_job","list_anchors"} <= names
    assert "generate_character_image" not in names  # unavailable conditioning is not advertised

def test_legacy_entrypoint_is_same_shared_bridge():
    from mcp_bridge import create_mcp_server as bridge_factory
    assert create_mcp_server is bridge_factory
