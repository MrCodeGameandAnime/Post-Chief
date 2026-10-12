from sqlalchemy import select
from postchief.models import User, SocialAccount
from postchief_mcp.store import OAuthStore, challenge
from test_mcp_oauth import mcp_app, mcp_client, RESOURCE, CALLBACK, VERIFIER
from test_mcp_transport import rpc


def agent_headers(client, scopes):
    app = client.app
    with app.state.sessions() as db: owner = db.scalar(select(User)); owner_id = owner.id
    store = OAuthStore(app.state.sessions)
    code = store.authorize(owner_id, 'post-chief-chatgpt', CALLBACK, RESOURCE, challenge(VERIFIER), scopes)
    token = store.consume_code(code, 'post-chief-chatgpt', CALLBACK, RESOURCE, VERIFIER)['access_token']
    return {'Authorization': 'Bearer ' + token}


def call(client, headers, name, arguments=None):
    response = rpc(client, 'tools/call', {'name': name, 'arguments': arguments or {}}, headers=headers)
    assert response.status_code == 200
    assert 'structuredContent' in response.json()['result'], response.json()['result']
    return response.json()['result']['structuredContent']


def test_tools_discover_and_read_with_scopes(mcp_client):
    headers = agent_headers(mcp_client, ['campaigns:read'])
    response = rpc(mcp_client, 'tools/list', headers=headers).json()
    tools = {tool['name']: tool for tool in response['result']['tools']}
    assert {'get_identity', 'list_campaigns', 'create_campaign', 'schedule_campaign',
        'publish_campaign', 'import_github_asset', 'report_agent_run'} <= tools.keys()
    assert tools['list_campaigns']['annotations']['readOnlyHint'] is True
    assert tools['publish_campaign']['annotations']['readOnlyHint'] is False
    assert 'ctx' not in tools['list_campaigns']['inputSchema']['properties']
    assert tools['create_campaign']['_meta']['securitySchemes'][0]['type'] == 'oauth2'
    assert call(mcp_client, headers, 'get_identity')['data']['kind'] == 'agent'
    lower_headers = {'Authorization': headers['Authorization'].replace('Bearer ', 'bearer ', 1)}
    assert call(mcp_client, lower_headers, 'get_identity')['data']['kind'] == 'agent'
    assert call(mcp_client, headers, 'list_campaigns')['data'] == []
    assert call(mcp_client, headers, 'list_assets')['http_status'] == 403
    denied = rpc(mcp_client, 'tools/call', {'name': 'list_assets', 'arguments': {}}, headers=headers).json()['result']
    assert denied['isError'] is True
    assert denied['structuredContent']['http_status'] == 403


def test_all_tool_route_mappings(mcp_client, monkeypatch):
    from postchief_mcp.client import ApiBridge
    from postchief.agents.policy import SCOPES
    async def record(self, method, path, token, **kwargs):
        return {'status': 'ok', 'data': {'method': method, 'path': path, 'payload': kwargs.get('json')}}
    monkeypatch.setattr(ApiBridge, 'request', record)
    headers = agent_headers(mcp_client, sorted(SCOPES))
    uid = '12345678-1234-4234-8234-123456789abc'
    cases = [('get_identity', {}, '/api/auth/me'), ('get_autonomy', {}, '/api/settings/autonomy'),
        ('list_connections', {}, '/api/connections'), ('list_campaigns', {}, '/api/campaigns'),
        ('get_campaign', {'campaign_id': uid}, '/api/campaigns/' + uid),
        ('get_publication', {'publication_id': uid}, '/api/publications/' + uid),
        ('list_assets', {}, '/api/assets'), ('get_workspace', {}, '/api/github/workspace'),
        ('list_repositories', {}, '/api/github/repositories'),
        ('read_github_content', {'repo': 'owner/repo', 'path': 'readme.md'}, '/api/github/content'),
        ('get_analytics', {}, '/api/analytics'), ('list_reporting_accounts', {}, '/api/reports/accounts'),
        ('get_account_report', {'account_id': uid}, '/api/reports/accounts/' + uid),
        ('list_approvals', {}, '/api/approvals'), ('list_audit_events', {}, '/api/audit')]
    for name, arguments, expected in cases:
        result = call(mcp_client, headers, name, arguments)['data']
        assert result['method'] == 'GET' and result['path'] == expected
    actions = [('create_campaign', {'campaign': {'title': 'Draft', 'body': 'Text', 'account_ids': [uid]}}, 'campaign.create'),
        ('update_campaign', {'campaign_id': uid, 'changes': {'revision': 1, 'body': 'New'}}, 'campaign.update'),
        ('cancel_campaign', {'campaign_id': uid}, 'campaign.cancel'),
        ('schedule_campaign', {'campaign_id': uid, 'schedule': {'scheduled_at': '2030-01-01T12:00:00Z'}}, 'campaign.schedule'),
        ('publish_campaign', {'campaign_id': uid}, 'campaign.publish'),
        ('retry_publication', {'publication_id': uid}, 'publication.retry'),
        ('write_github_content', {'content': {'repo': 'owner/repo', 'path': 'post.md', 'content': 'New'}}, 'github.write'),
        ('refresh_analytics', {'publication_id': uid}, 'analytics.refresh')]
    for name, arguments, expected in actions:
        result = call(mcp_client, headers, name, arguments)['data']
        assert result['method'] == 'POST' and result['path'] == '/api/agent/actions'
        assert result['payload']['action'] == expected
    for name, arguments, path in [('import_github_asset', {'asset': {'repo': 'owner/repo', 'path': 'image.jpg'}}, '/api/assets/github'),
            ('report_agent_run', {'report': {'summary': 'Reviewed drafts'}}, '/api/agent/runs')]:
        result = call(mcp_client, headers, name, arguments)['data']
        assert result['method'] == 'POST' and result['path'] == path


def test_draft_creation_and_publish_approval_through_mcp(mcp_client):
    with mcp_client.app.state.sessions() as db:
        owner = db.scalar(select(User))
        account = SocialAccount(org_id=owner.org_id, provider='threads', remote_id='test',
            name='Test account', credentials='not-used')
        db.add(account); db.flush(); account_id = account.id; db.commit()
    headers = agent_headers(mcp_client, ['campaigns:read', 'campaigns:write', 'campaigns:publish'])
    draft = call(mcp_client, headers, 'create_campaign', {'campaign': {
        'title': 'MCP verification', 'body': 'Draft only', 'account_ids': [account_id]}})
    assert draft['status'] == 'ok'
    assert draft['data']['status'] == 'draft'
    campaign_id = draft['data']['id']
    assert call(mcp_client, headers, 'get_campaign', {'campaign_id': campaign_id})['data']['id'] == campaign_id
    required = call(mcp_client, headers, 'publish_campaign', {'campaign_id': campaign_id})
    assert required['status'] == 'approval_required'
    assert required['approval']['action'] == 'campaign.publish'
    assert required['approval']['approval_id']
    failed = call(mcp_client, headers, 'publish_campaign', {
        'campaign_id': campaign_id, 'approval_id': required['approval']['approval_id']})
    assert failed['http_status'] == 409
    assert call(mcp_client, headers, 'get_campaign', {'campaign_id': campaign_id})['data']['status'] == 'draft'
