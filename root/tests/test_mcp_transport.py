import pytest
from fastapi.testclient import TestClient


def rpc(client, method, params=None, request_id=1, headers=None):
    return client.post('/mcp', json={'jsonrpc': '2.0', 'id': request_id,
        'method': method, 'params': params or {}}, headers={
        'Accept': 'application/json, text/event-stream', **(headers or {})})


def test_disabled_mcp_keeps_existing_routes(client):
    assert client.get('/api/health').status_code == 200
    assert rpc(client, 'tools/list').status_code == 404


def test_mcp_enabled_mount_and_lifecycle(app):
    from postchief_mcp.server import create_mcp
    integration = create_mcp(app)
    with TestClient(integration.asgi_app, base_url=app.state.settings.public_url) as client:
        response = rpc(client, 'initialize', {'protocolVersion': '2025-11-25',
            'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
        assert response.status_code == 401
        assert 'oauth-protected-resource' in response.headers['www-authenticate']


def test_callback_configuration_rejects_unsafe_urls(app):
    from postchief.config import Settings
    values = app.state.settings.model_dump()
    for url in ['http://evil.test/callback', 'https://evil.test/cb#fragment',
                'https://user:password@evil.test/cb', 'https://evil.test/*']:
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{**values, 'mcp_redirect_uris': [url]})


def test_callback_configuration_allows_only_literal_loopback_http(app):
    from postchief.config import Settings
    values = app.state.settings.model_dump()
    callbacks = ['https://chatgpt.com/connector/oauth/test',
        'http://127.0.0.1/callback/mmLDl6de3hKO']
    assert Settings(_env_file=None, **{**values, 'mcp_redirect_uris': callbacks}).mcp_redirect_uris == callbacks
    for url in ['http://localhost/callback/test', 'http://127.0.0.1.evil.test/callback/test',
            'http://10.0.0.1/callback/test', 'http://127.0.0.1:0/callback/test',
            'http://user@127.0.0.1/callback/test', 'http://127.0.0.1:99999/callback/test']:
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{**values, 'mcp_redirect_uris': [url]})


def test_loopback_matching_only_varies_port_for_port_free_registration():
    from postchief_mcp.callbacks import callback_allowed
    base = 'http://127.0.0.1/callback/test'
    assert callback_allowed('http://127.0.0.1:55422/callback/test', [base])
    assert not callback_allowed('http://127.0.0.1:55423/callback/test',
        ['http://127.0.0.1:55422/callback/test'])
    assert not callback_allowed('http://127.0.0.1:0/callback/test', [base])
    assert not callback_allowed('http://127.0.0.1:99999/callback/test', [base])
    assert not callback_allowed('https://chatgpt.com:55422/connector/oauth/test',
        ['https://chatgpt.com/connector/oauth/test'])


def test_enabled_mcp_requires_canonical_origin_and_matching_frontend(app):
    from postchief.config import Settings
    values = app.state.settings.model_dump()
    for origin, frontend in [('http://evil.test', 'http://evil.test'),
            ('https://example.test/path', 'https://example.test/path'),
            ('https://example.test?token=bad', 'https://example.test'),
            ('https://user:pass@example.test', 'https://example.test'),
            ('https://example.test', 'https://other.test')]:
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{**values, 'mcp_enabled': True,
                'public_url': origin, 'frontend_url': frontend})
