import pytest
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_mcp_oauth import mcp_app, mcp_client, RESOURCE
from test_mcp_tools import agent_headers
from types import SimpleNamespace


@pytest.mark.asyncio
async def test_real_sdk_client_reads(mcp_app):
    headers = agent_headers(SimpleNamespace(app=mcp_app), ['campaigns:read'])
    async with mcp_app.router.lifespan_context(mcp_app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=mcp_app),
                headers=headers) as http:
            async with streamable_http_client(RESOURCE, http_client=http) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == 'Post Chief'
                    tools = await session.list_tools()
                    assert any(tool.name == 'list_campaigns' for tool in tools.tools)
                    result = await session.call_tool('list_campaigns', {})
                    assert result.structured_content['status'] == 'ok'
                    assert result.structured_content['data'] == []
