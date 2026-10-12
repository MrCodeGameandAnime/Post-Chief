"""SDK OAuth handlers backed by Post Chief's durable grants and owner consent."""
from contextvars import ContextVar
from datetime import timedelta
import re
import secrets
from urllib.parse import urlencode
from time import monotonic
from pydantic import AnyUrl
from sqlalchemy import select
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.server.auth.provider import AuthorizationCode, RefreshToken, TokenError, AuthorizeError
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.middleware.client_auth import AuthenticationError
from starlette.responses import JSONResponse
from starlette.routing import Route
from fastapi import APIRouter
from postchief.agents.policy import SCOPES
from .models import MCPClient, MCPCode, MCPGrant, MCPRefresh, MCPFamily, MCPPending
from .store import OAuthStore, OAuthError, digest, now, utc, challenge
from .callbacks import callback_allowed

token_form = ContextVar('postchief_mcp_token_form', default=None)


class RegisteredOAuthClient(OAuthClientInformationFull):
    def validate_redirect_uri(self, redirect_uri):
        if redirect_uri is not None and callback_allowed(str(redirect_uri),
                [str(uri) for uri in self.redirect_uris or []]):
            return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


class OAuthProvider:
    def __init__(self, app):
        self.app = app
        self.settings = app.state.settings
        self.store = OAuthStore(app.state.sessions)
        self.origin = self.settings.public_url.rstrip('/')
        self.resource = self.origin + '/mcp'
        self.last_prune = 0.0

    def maintain(self):
        at = monotonic()
        if at - self.last_prune >= 60:
            self.last_prune = at
            self.store.prune_expired()

    async def get_client(self, client_id):
        if client_id != self.settings.mcp_client_id or not self.settings.mcp_redirect_uris:
            return None
        return RegisteredOAuthClient(client_id=client_id,
            client_name='Post Chief agent', redirect_uris=self.settings.mcp_redirect_uris,
            token_endpoint_auth_method='none', grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'], scope=' '.join(sorted(SCOPES)))

    def configure_client(self):
        with self.app.state.sessions() as db:
            db.merge(MCPClient(id=self.settings.mcp_client_id,
                redirect_uris=list(self.settings.mcp_redirect_uris)))
            db.commit()
        self.store.prune_expired()

    async def register_client(self, _):
        raise NotImplementedError('Dynamic registration is disabled')

    async def authorize(self, client, params):
        if params.resource != self.resource: raise AuthorizeError('invalid_target')
        if (not params.redirect_uri_provided_explicitly or
                not callback_allowed(str(params.redirect_uri), self.settings.mcp_redirect_uris) or
                not re.fullmatch(r'[A-Za-z0-9_-]{43}', params.code_challenge) or
                (params.state is not None and len(params.state) > 2000)):
            raise AuthorizeError('invalid_request')
        scopes = params.scopes or sorted(SCOPES)
        if set(scopes) - SCOPES: raise AuthorizeError('invalid_scope')
        self.maintain()
        pending = secrets.token_urlsafe(48)
        with self.app.state.sessions() as db:
            db.add(MCPPending(token_hash=digest(pending), parameters={
                'client_id': client.client_id, 'redirect_uri': str(params.redirect_uri),
                'resource': self.resource, 'challenge': params.code_challenge,
                'scopes': sorted(set(scopes)), 'state': params.state},
                expires_at=now() + timedelta(minutes=10)))
            db.commit()
        return self.origin + '/api/mcp/oauth/consent?' + urlencode({'request': pending})

    async def load_authorization_code(self, client, code):
        if len(code) > 200: return None
        with self.app.state.sessions() as db:
            row = db.scalar(select(MCPCode).where(MCPCode.token_hash == digest(code)))
            grant = db.get(MCPGrant, row.grant_id) if row else None
            if not row or row.consumed or not grant or grant.client_id != client.client_id: return None
            return AuthorizationCode(code=code, scopes=grant.scopes,
                expires_at=utc(row.expires_at).timestamp(), client_id=grant.client_id,
                code_challenge=row.challenge, redirect_uri=AnyUrl(row.redirect_uri),
                redirect_uri_provided_explicitly=True, resource=grant.resource)

    async def exchange_authorization_code(self, client, code):
        form = token_form.get()
        if not form: raise TokenError('invalid_request')
        try:
            return OAuthToken(**self.store.consume_code(code.code, client.client_id,
                str(code.redirect_uri), form['resource'], form['code_verifier']))
        except OAuthError as error: raise TokenError(error.error) from None

    async def load_refresh_token(self, client, token):
        if len(token) > 200: return None
        with self.app.state.sessions() as db:
            row = db.scalar(select(MCPRefresh).where(MCPRefresh.token_hash == digest(token)))
            family = db.get(MCPFamily, row.family_id) if row else None
            grant = db.get(MCPGrant, family.grant_id) if family else None
            if not row or not grant or grant.client_id != client.client_id: return None
            return RefreshToken(token=token, client_id=grant.client_id, scopes=row.scopes,
                expires_at=int(utc(row.expires_at).timestamp()), resource=grant.resource)

    async def exchange_refresh_token(self, client, token, scopes):
        form = token_form.get()
        if not form: raise TokenError('invalid_request')
        try:
            return OAuthToken(**self.store.rotate_refresh(token.token, client.client_id,
                form['resource'], scopes))
        except OAuthError as error: raise TokenError(error.error) from None

    async def load_access_token(self, token):
        # SDK revocation uses this lookup; authorization uses the same store.
        from mcp.server.auth.provider import AccessToken
        identity = self.store.resolve_access(token, self.resource)
        if not identity: return None
        return AccessToken(token=token, client_id=identity.client_id, scopes=identity.scopes,
            expires_at=int(identity.expires_at.timestamp()), resource=self.resource,
            subject=identity.agent_key_id)

    async def revoke_token(self, token):
        self.store.revoke(token.token, token.client_id)


def build_oauth_routes(app):
    provider = OAuthProvider(app)
    app.state.mcp_oauth = provider
    client_auth = ClientAuthenticator(provider)
    token_handler = TokenHandler(provider, client_auth)

    async def token_endpoint(request):
        data = await request.form()
        if any(len(data.getlist(key)) != 1 for key in data):
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        form = dict(data)
        if form.get('resource') != provider.resource:
            return JSONResponse({'error': 'invalid_target'}, status_code=400,
                headers={'Cache-Control': 'no-store'})
        if form.get('grant_type') == 'authorization_code':
            try: challenge(form.get('code_verifier', ''))
            except OAuthError:
                return JSONResponse({'error': 'invalid_grant'}, status_code=400,
                    headers={'Cache-Control': 'no-store'})
        context = token_form.set(form)
        try:
            provider.maintain()
            return await token_handler.handle(request)
        finally: token_form.reset(context)

    async def revoke_endpoint(request):
        # The SDK revocation model requires client_secret even for public clients;
        # use its authenticator while accepting RFC 7009's secret-free public flow.
        try: client = await client_auth.authenticate_request(request)
        except AuthenticationError:
            return JSONResponse({'error': 'invalid_client'}, status_code=401)
        data = await request.form()
        if (any(len(data.getlist(key)) != 1 for key in data) or
                not data.get('token') or len(data['token']) > 200):
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        provider.store.revoke(data['token'], client.client_id)
        return JSONResponse({}, headers={'Cache-Control': 'no-store'})

    router = APIRouter()

    @router.get('/.well-known/oauth-authorization-server')
    def metadata():
        return {'issuer': provider.origin,
            'authorization_endpoint': provider.origin + '/api/mcp/oauth/authorize',
            'token_endpoint': provider.origin + '/api/mcp/oauth/token',
            'revocation_endpoint': provider.origin + '/api/mcp/oauth/revoke',
            'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'token_endpoint_auth_methods_supported': ['none'],
            'revocation_endpoint_auth_methods_supported': ['none'],
            'code_challenge_methods_supported': ['S256'], 'scopes_supported': sorted(SCOPES)}

    @router.get('/.well-known/oauth-protected-resource/mcp')
    @router.get('/.well-known/oauth-protected-resource')
    def resource_metadata():
        return {'resource': provider.resource, 'authorization_servers': [provider.origin],
            'scopes_supported': sorted(SCOPES), 'bearer_methods_supported': ['header'],
            'resource_name': 'Post Chief'}

    app.include_router(router)
    app.router.routes.extend([
        Route('/api/mcp/oauth/authorize', AuthorizationHandler(provider).handle, methods=['GET', 'POST']),
        Route('/api/mcp/oauth/token', token_endpoint, methods=['POST']),
        Route('/api/mcp/oauth/revoke', revoke_endpoint, methods=['POST'])])
    return provider
