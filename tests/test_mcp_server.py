import pytest
from app.mcp.server import create_mcp_server


@pytest.mark.asyncio
async def test_mcp_server_initialization_and_tools():
    mcp = create_mcp_server()
    assert mcp.name == "local-media-hub"

    # List tools registered on MCPServer
    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]

    assert "generate_image" in tool_names
    assert "generate_character_image" in tool_names
    assert "generate_location_image" in tool_names
    assert "list_character_anchors" in tool_names
    assert "list_location_anchors" in tool_names
    assert "get_system_status" in tool_names


@pytest.mark.asyncio
async def test_mcp_call_list_anchors():
    mcp = create_mcp_server()

    # Call list_character_anchors tool
    char_res = await mcp.call_tool("list_character_anchors", {})
    # Content format in MCP
    text_content = char_res[0].text if isinstance(char_res, list) else str(char_res)
    assert "moises" in text_content

    # Call list_location_anchors tool
    loc_res = await mcp.call_tool("list_location_anchors", {})
    text_loc = loc_res[0].text if isinstance(loc_res, list) else str(loc_res)
    assert "templo_salomao" in text_loc


@pytest.mark.asyncio
async def test_mcp_call_system_status():
    mcp = create_mcp_server()
    status_res = await mcp.call_tool("get_system_status", {})
    text_status = status_res[0].text if isinstance(status_res, list) else str(status_res)
    assert "AMD Radeon RX 7800 XT" in text_status
    assert "16 GB VRAM" in text_status
