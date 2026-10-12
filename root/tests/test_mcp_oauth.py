import pytest
from urllib.parse import urlsplit, parse_qs, urlencode, quote
from fastapi.testclient import TestClient
from sqlalchemy import select
from postchief.main import create_app
from postchief.models import Base, AgentKey
from postchief_mcp.store import challenge
from postchief_mcp.models import MCPPending

ORIGIN = 'https://postchief.example.test'
RESOURCE = ORIGIN + '/mcp'
CALLBACK = 'https://chatgpt.com/connector/oauth/test'
VERIFIER = 'v' * 43


@pytest.fixture
def mcp_app(app):
    settings = app.state.settings.model_copy(update={'mcp_enabled': True,
        'public_url': ORIGIN, 'frontend_url': ORIGIN, 'mcp_redirect_uris': [CALLBACK]})
    result = create_app(settings)
    Base.metadata.create_all(result.state.engine)
    yield result
    result.state.engine.dispose()


@pytest.fixture
def mcp_client(mcp_app):
    with TestClient(mcp_app, base_url=ORIGIN) as client:
        response = client.post('/api/auth/login', json={
            'email': 'owner@example.test', 'password': 'a-long-test-password'})
        assert response.status_code == 200
        client.headers['X-CSRF-Token'] = response.json()['csrf']
        yield client


def params(**changes):
    return {'client_id': 'post-chief-chatgpt', 'redirect_uri': CALLBACK,
        'response_type': 'code', 'code_challenge_method': 'S256',
        'code_challenge': challenge(VERIFIER), 'state': 'original-state',
        'scope': 'campaigns:read campaigns:write', 'resource': RESOURCE, **changes}


def link(client, **changes):
    response = client.get('/api/mcp/oauth/authorize', params=params(**changes), follow_redirects=False)
    assert response.status_code == 302
    request_id = parse_qs(urlsplit(response.headers['location']).query)['request'][0]
    response = client.post('/api/mcp/oauth/consent', json={'request': request_id,
        'decision': 'allow', 'scopes': ['campaigns:read', 'campaigns:write']})
    assert response.status_code == 200
    values = parse_qs(urlsplit(response.json()['redirect_url']).query)
    assert values['state'] == ['original-state']
    return values['code'][0]


def exchange(client, code, **changes):
    return client.post('/api/mcp/oauth/token', data={'grant_type': 'authorization_code',
        'client_id': 'post-chief-chatgpt', 'redirect_uri': CALLBACK,
        'code': code, 'code_verifier': VERIFIER, 'resource': RESOURCE, **changes})


def test_oauth_discovery_and_roundtrip(mcp_client):
    metadata = mcp_client.get('/.well-known/oauth-authorization-server').json()
    assert metadata['issuer'] == ORIGIN
    assert metadata['token_endpoint_auth_methods_supported'] == ['none']
    assert metadata['code_challenge_methods_supported'] == ['S256']
    assert 'registration_endpoint' not in metadata
    protected = mcp_client.get('/.well-known/oauth-protected-resource/mcp').json()
    assert protected['resource'] == RESOURCE
    assert protected['authorization_servers'] == [ORIGIN]
    code = link(mcp_client)
    response = exchange(mcp_client, code)
    assert response.status_code == 200
    assert response.json()['access_token'].startswith('pc_mcp_')
    assert response.headers['cache-control'] == 'no-store'
    assert exchange(mcp_client, code).status_code == 400


def test_codex_loopback_ports_preserve_exact_code_binding_and_cloud_link(mcp_client):
    registered = 'http://127.0.0.1/callback/mmLDl6de3hKO'
    mcp_client.app.state.settings.mcp_redirect_uris.append(registered)
    for port in (55422, 55423):
        callback = f'http://127.0.0.1:{port}/callback/mmLDl6de3hKO'
        code = link(mcp_client, redirect_uri=callback)
        assert exchange(mcp_client, code,
            redirect_uri=f'http://127.0.0.1:{port + 1}/callback/mmLDl6de3hKO').status_code == 400
        assert exchange(mcp_client, code, redirect_uri=callback).status_code == 200
    assert exchange(mcp_client, link(mcp_client)).status_code == 200
    for callback in ['http://127.0.0.1:55422/callback/other',
            'http://127.0.0.1:55422/callback/mmLDl6de3hKO?extra=value',
            'http://localhost:55422/callback/mmLDl6de3hKO',
            'http://127.0.0.1.evil.test:55422/callback/mmLDl6de3hKO',
            'http://user@127.0.0.1:55422/callback/mmLDl6de3hKO',
            'https://127.0.0.1:55422/callback/mmLDl6de3hKO']:
        assert mcp_client.get('/api/mcp/oauth/authorize', params=params(redirect_uri=callback),
            follow_redirects=False).status_code == 400


def test_oauth_rejects_callback_resource_pkce_and_scopes(mcp_client):
    for changed in [{'redirect_uri': 'https://evil.test/cb'}, {'resource': 'https://evil.test/mcp'},
                    {'code_challenge_method': 'plain'}, {'scope': 'owner'}, {'code_challenge': 'bad'}]:
        response = mcp_client.get('/api/mcp/oauth/authorize', params=params(**changed), follow_redirects=False)
        assert response.status_code in (400, 302)
        assert not response.headers.get('location', '').startswith('https://evil.test')
        with mcp_client.app.state.sessions() as db:
            assert db.scalar(select(MCPPending.id)) is None
    code = link(mcp_client)
    assert exchange(mcp_client, code, resource='https://evil.test/mcp').status_code == 400
    assert exchange(mcp_client, code, code_verifier='x' * 43).status_code == 400
    assert exchange(mcp_client, code).status_code == 200


def test_consent_requires_owner_csrf_and_is_single_use(mcp_client):
    response = mcp_client.get('/api/mcp/oauth/authorize', params=params(), follow_redirects=False)
    request_id = parse_qs(urlsplit(response.headers['location']).query)['request'][0]
    body = {'request': request_id, 'decision': 'allow', 'scopes': ['campaigns:read']}
    assert mcp_client.post('/api/mcp/oauth/consent', json=body,
        headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert mcp_client.post('/api/mcp/oauth/consent', json={**body, 'scopes': ['campaigns:publish']}).status_code == 400
    assert mcp_client.post('/api/mcp/oauth/consent', json=body).status_code == 200
    assert mcp_client.post('/api/mcp/oauth/consent', json=body).status_code == 400
    with mcp_client.app.state.sessions() as db:
        key = db.scalar(select(AgentKey))
        assert key.scopes == ['campaigns:read']


def test_consent_page_and_rejection_without_exposing_secrets(mcp_client):
    response = mcp_client.get('/api/mcp/oauth/authorize', params=params(), follow_redirects=False)
    page = mcp_client.get(response.headers['location'])
    assert page.status_code == 200
    assert 'Content-Security-Policy' in page.headers
    assert 'pc_session' not in page.text
    request_id = parse_qs(urlsplit(response.headers['location']).query)['request'][0]
    response = mcp_client.post('/api/mcp/oauth/consent', json={
        'request': request_id, 'decision': 'deny', 'scopes': []})
    query = parse_qs(urlsplit(response.json()['redirect_url']).query)
    assert query['error'] == ['access_denied']
    assert query['state'] == ['original-state']


def test_public_client_revocation_and_duplicate_parameter_rejection(mcp_client):
    tokens = exchange(mcp_client, link(mcp_client)).json()
    headers = {'Authorization': 'Bearer ' + tokens['access_token']}
    response = mcp_client.post('/api/mcp/oauth/revoke', data={
        'client_id': 'post-chief-chatgpt', 'token': tokens['access_token']})
    assert response.status_code == 200
    assert mcp_client.get('/api/auth/me', headers=headers).status_code == 401
    code = link(mcp_client)
    body = urlencode({'grant_type': 'authorization_code', 'client_id': 'post-chief-chatgpt',
        'redirect_uri': CALLBACK, 'code': code, 'code_verifier': VERIFIER,
        'resource': RESOURCE}) + '&resource=' + quote(RESOURCE, safe='')
    response = mcp_client.post('/api/mcp/oauth/token', content=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    assert response.status_code == 400


def test_oauth_limits_and_no_owner_cookie_mcp_access(mcp_client):
    from test_mcp_transport import rpc
    assert rpc(mcp_client, 'tools/list').status_code == 401
    assert mcp_client.post('/api/mcp/oauth/token', content=b'x' * 8193).status_code == 413
