import pytest
from sqlalchemy import select
from postchief.models import AgentKey, Organization, Campaign
from test_mcp_oauth import mcp_app, mcp_client, link, exchange, RESOURCE
from test_mcp_transport import rpc


def test_oauth_identity_api_scope_and_owner_isolation(mcp_client):
    tokens = exchange(mcp_client, link(mcp_client)).json()
    headers = {'Authorization': 'Bearer ' + tokens['access_token']}
    identity = mcp_client.get('/api/auth/me', headers=headers).json()
    assert identity['kind'] == 'agent'
    assert identity['scopes'] == ['campaigns:read', 'campaigns:write']
    assert mcp_client.get('/api/campaigns', headers=headers).status_code == 200
    assert mcp_client.get('/api/assets', headers=headers).status_code == 403
    assert mcp_client.post('/api/agent/keys', headers=headers,
        json={'name': 'Escalate', 'scopes': ['campaigns:publish']}).status_code == 403
    with mcp_client.app.state.sessions() as db:
        other = Organization(name='Other'); db.add(other); db.flush()
        row = Campaign(org_id=other.id, title='Private', body='Secret'); db.add(row); db.flush()
        campaign_id = row.id; db.commit()
    assert mcp_client.get('/api/campaigns/' + campaign_id, headers=headers).status_code == 404


def test_revoked_identity_rejected_by_rest_and_transport(mcp_client):
    tokens = exchange(mcp_client, link(mcp_client)).json()
    headers = {'Authorization': 'Bearer ' + tokens['access_token']}
    assert rpc(mcp_client, 'initialize', {'protocolVersion': '2025-11-25',
        'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}, headers=headers).status_code == 200
    with mcp_client.app.state.sessions() as db:
        row = db.scalar(select(AgentKey)); row.active = False; db.commit()
    assert mcp_client.get('/api/auth/me', headers=headers).status_code == 401
    assert rpc(mcp_client, 'tools/list', headers=headers).status_code == 401


@pytest.mark.asyncio
async def test_api_bridge_has_no_arbitrary_http_or_owner_forwarding(mcp_client):
    from postchief_mcp.client import ApiBridge
    token = exchange(mcp_client, link(mcp_client)).json()['access_token']
    bridge = ApiBridge(mcp_client.app)
    response = await bridge.request('GET', '/api/campaigns', token)
    assert response['status'] == 'ok'
    for path in ['https://evil.test', '/api/agent/keys', '/api/auth/login', '/mcp',
                 '/api/../api/agent/keys', '/api/campaigns?secret=token']:
        with pytest.raises(ValueError): await bridge.request('GET', path, token)
