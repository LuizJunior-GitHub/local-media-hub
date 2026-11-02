import pytest
import httpx
from mcp_bridge import create_mcp_server

@pytest.mark.asyncio
async def test_mcp_uses_shared_api_and_returns_immediately():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(202,json={"job_id":"shared-job","status":"queued"})
    server = create_mcp_server()
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000",transport=httpx.MockTransport(handler)) as client:
        server.hub_client = client
        tools = await server.list_tools()
        assert {"generate_image","get_job","import_quiz_manifest","export_quiz_project"} <= {t.name for t in tools}
        result = await server.call_tool("generate_image",{"prompt":"ark","request_id":"stable","seed":42})
        assert "shared-job" in str(result)
        assert len(requests) == 1
        assert requests[0].url.path == "/api/v1/generate/flux"
        assert b'"wait_for_completion":false' in requests[0].content

@pytest.mark.asyncio
async def test_invalid_engine_does_not_submit():
    server = create_mcp_server()
    with pytest.raises(Exception):
        await server.call_tool("generate_image",{"prompt":"ark","request_id":"stable","seed":42,"engine":"bogus"})
