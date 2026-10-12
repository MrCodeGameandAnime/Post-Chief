from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from postchief.models import User, AgentKey
from postchief_mcp.models import MCPCode, MCPAccess, MCPRefresh, MCPFamily
from postchief_mcp.store import OAuthStore, OAuthError, challenge

VERIFIER = 'v' * 43
RESOURCE = 'https://postchief.example.test/mcp'
CALLBACK = 'https://chatgpt.com/callback'


def authorize(app, scopes=None):
    store = OAuthStore(app.state.sessions)
    with app.state.sessions() as db:
        owner = db.scalar(select(User))
        owner_id = owner.id
    code = store.authorize(owner_id, 'test-client', CALLBACK, RESOURCE,
        challenge(VERIFIER), scopes or ['campaigns:read'])
    return store, code


def issue(app, scopes=None):
    store, code = authorize(app, scopes)
    return store, store.consume_code(code, 'test-client', CALLBACK, RESOURCE, VERIFIER)


def test_code_bound_single_use_and_hashed(app):
    store, code = authorize(app)
    for values in [('wrong', CALLBACK, RESOURCE, VERIFIER),
                   ('test-client', CALLBACK + '/wrong', RESOURCE, VERIFIER),
                   ('test-client', CALLBACK, RESOURCE + '/wrong', VERIFIER),
                   ('test-client', CALLBACK, RESOURCE, 'x' * 43)]:
        with pytest.raises(OAuthError): store.consume_code(code, *values)
    tokens = store.consume_code(code, 'test-client', CALLBACK, RESOURCE, VERIFIER)
    assert tokens['expires_in'] == 900
    with pytest.raises(OAuthError):
        store.consume_code(code, 'test-client', CALLBACK, RESOURCE, VERIFIER)
    with app.state.sessions() as db:
        for model in [MCPCode, MCPAccess, MCPRefresh]:
            rows = db.scalars(select(model)).all()
            assert rows
            for row in rows:
                assert code not in str(row.__dict__)
                assert tokens['access_token'] not in str(row.__dict__)
                assert tokens['refresh_token'] not in str(row.__dict__)


def test_refresh_rotation_reuse_revokes_entire_family(app):
    store, first = issue(app)
    second = store.rotate_refresh(first['refresh_token'], 'test-client', RESOURCE)
    assert store.resolve_access(second['access_token'], RESOURCE)
    with pytest.raises(OAuthError):
        store.rotate_refresh(first['refresh_token'], 'test-client', RESOURCE)
    assert store.resolve_access(first['access_token'], RESOURCE) is None
    assert store.resolve_access(second['access_token'], RESOURCE) is None


def test_revocation_scope_reduction_and_expiry(app):
    store, tokens = issue(app, ['campaigns:read', 'campaigns:write'])
    resolved = store.resolve_access(tokens['access_token'], RESOURCE)
    with app.state.sessions() as db:
        key = db.get(AgentKey, resolved.agent_key_id)
        key.scopes = ['campaigns:read']; db.commit()
    assert store.resolve_access(tokens['access_token'], RESOURCE).scopes == ['campaigns:read']
    with app.state.sessions() as db:
        db.get(AgentKey, resolved.agent_key_id).active = False; db.commit()
    assert store.resolve_access(tokens['access_token'], RESOURCE) is None
    with pytest.raises(OAuthError):
        store.rotate_refresh(tokens['refresh_token'], 'test-client', RESOURCE)


def test_expired_code_and_access_and_refresh(app):
    store, code = authorize(app)
    with app.state.sessions() as db:
        db.scalar(select(MCPCode)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    with pytest.raises(OAuthError):
        store.consume_code(code, 'test-client', CALLBACK, RESOURCE, VERIFIER)
    store, tokens = issue(app)
    with app.state.sessions() as db:
        db.scalar(select(MCPAccess)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.scalar(select(MCPFamily)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert store.resolve_access(tokens['access_token'], RESOURCE) is None
    with pytest.raises(OAuthError):
        store.rotate_refresh(tokens['refresh_token'], 'test-client', RESOURCE)


def test_refresh_cannot_expand_scopes_and_survives_new_store(app):
    store, tokens = issue(app, ['campaigns:read', 'campaigns:write'])
    with pytest.raises(OAuthError):
        store.rotate_refresh(tokens['refresh_token'], 'test-client', RESOURCE, ['campaigns:publish'])
    reduced = store.rotate_refresh(tokens['refresh_token'], 'test-client', RESOURCE, ['campaigns:read'])
    fresh = OAuthStore(app.state.sessions)
    assert fresh.resolve_access(reduced['access_token'], RESOURCE).scopes == ['campaigns:read']
    with pytest.raises(OAuthError):
        fresh.rotate_refresh(reduced['refresh_token'], 'test-client', RESOURCE, ['campaigns:write'])


def test_postgres_concurrent_redemption_and_rotation(app):
    if app.state.engine.dialect.name != 'postgresql':
        pytest.skip('Concurrency is verified on disposable PostgreSQL')
    store, code = authorize(app)
    def redeem(_):
        try: return store.consume_code(code, 'test-client', CALLBACK, RESOURCE, VERIFIER)
        except OAuthError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: redeemed = list(pool.map(redeem, range(2)))
    assert sum(value is not None for value in redeemed) == 1
    first = next(value for value in redeemed if value is not None)
    def rotate(_):
        try: return store.rotate_refresh(first['refresh_token'], 'test-client', RESOURCE)
        except OAuthError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: rotated = list(pool.map(rotate, range(2)))
    assert sum(value is not None for value in rotated) == 1
    # The second presentation is replay and invalidates the newly minted family too.
    second = next(value for value in rotated if value is not None)
    assert store.resolve_access(second['access_token'], RESOURCE) is None
