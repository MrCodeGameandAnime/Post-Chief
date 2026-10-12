"""Mount the official MCP transport in the existing API process."""
from dataclasses import dataclass
from urllib.parse import urlsplit
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
import anyio
from mcp.server.auth.provider import AccessToken
from .store import OAuthStore


class TokenVerifier:
    def __init__(self, app):
        self.app = app

    async def verify_token(self, token):
        resource = self.app.state.settings.public_url.rstrip('/') + '/mcp'
        identity = await anyio.to_thread.run_sync(
            OAuthStore(self.app.state.sessions).resolve_access, token, resource)
        if identity is None: return None
        return AccessToken(token=token, client_id=identity.client_id, scopes=identity.scopes,
            expires_at=int(identity.expires_at.timestamp()), resource=resource,
            subject=identity.agent_key_id, claims={'iss': self.app.state.settings.public_url.rstrip('/')})


@dataclass
class MCPIntegration:
    server: MCPServer
    asgi_app: Starlette

    def lifespan(self):
        return self.server.session_manager.run()


def create_mcp(app):
    settings = app.state.settings
    origin = settings.public_url.rstrip('/')
    server = MCPServer('Post Chief', instructions=(
        'Use these tools to operate Post Chief. Read current records before edits. '
        'Respect approval_required results; never approve your own actions. '
        'Dispatch acceptance does not mean a publication succeeded.'),
        token_verifier=TokenVerifier(app), auth=AuthSettings(
            issuer_url=origin, resource_server_url=origin + '/mcp',
            required_scopes=[], validate_token_resource=True))
    transport = server.streamable_http_app(json_response=True, stateless_http=True,
        max_request_body_size=1_500_000, transport_security=TransportSecuritySettings(
            allowed_hosts=[urlsplit(origin).netloc], allowed_origins=[origin]))
    from .client import ApiBridge
    from .tools_read import register_read_tools
    from .tools_write import register_write_tools
    bridge = ApiBridge(app)
    register_read_tools(server, bridge)
    register_write_tools(server, bridge)
    return MCPIntegration(server, transport)
