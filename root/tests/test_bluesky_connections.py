import json
import httpx
from sqlalchemy import select
from postchief.models import SocialAccount
from postchief.providers.bluesky import BlueskyProvider
from postchief.providers.routes import get_bluesky_provider
from postchief.vault import Vault


def test_bluesky_connect_encrypts_session_and_reconnect_preserves_account(client, app):
    def transport(request):
        assert request.url.path.endswith("com.atproto.server.createSession")
        assert json.loads(request.content) == {"identifier":"404.bsky.social","password":"app-password"}
        return httpx.Response(200,json={"did":"did:plc:404","handle":"404.bsky.social","accessJwt":"access-token","refreshJwt":"refresh-token","active":True,"didDoc":{"service":[{"id":"#atproto_pds","type":"AtprotoPersonalDataServer","serviceEndpoint":"https://pds.host.bsky.network"}]}})
    async def provider():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            yield BlueskyProvider(http)
    app.dependency_overrides[get_bluesky_provider] = provider
    first = client.post("/api/connections/bluesky",json={"identifier":"404.bsky.social","password":"app-password"})
    assert first.status_code == 201
    assert "access-token" not in first.text
    second = client.post("/api/connections/bluesky",json={"identifier":"404.bsky.social","password":"app-password"})
    assert second.json()["id"] == first.json()["id"]
    with app.state.sessions() as db:
        account = db.scalar(select(SocialAccount))
        assert "access-token" not in account.credentials
        creds = Vault(app.state.settings.encryption_key.get_secret_value()).decrypt(account.credentials)
        assert creds["accessJwt"] == "access-token"
        assert "password" not in creds
    capabilities = client.get("/api/capabilities").json()
    assert capabilities[0]["provider"] == "bluesky"
    assert capabilities[0]["capabilities"]["video"] is True
    assert client.delete(f'/api/connections/{first.json()["id"]}').json()["active"] is False
