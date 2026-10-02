from io import BytesIO
from PIL import Image
from sqlalchemy import select
from postchief.models import GitHubConnection, User
from postchief.github.routes import get_service
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from postchief.github.service import GitHubService, GitHubError


def test_github_asset_import_uses_conditional_cache(client, app):
    image = BytesIO()
    Image.new("RGB",(2,3),(0,0,0)).save(image,format="PNG")
    class GitHub:
        async def read_binary(self, installation, repo, path, etag=None):
            assert (installation,repo,path) == (7,"org/social","assets/art.png")
            if etag == '"version-1"':
                return {"data":None,"etag":etag}
            return {"data":image.getvalue(),"etag":'"version-1"'}
    app.dependency_overrides[get_service] = lambda: GitHub()
    with app.state.sessions() as db:
        db.add(GitHubConnection(org_id=db.scalar(select(User.org_id)),installation_id=7,workspace="org/social"))
        db.commit()
    first = client.post("/api/assets/github",json={"repo":"org/social","path":"assets/art.png"})
    assert first.status_code == 201
    assert first.json()["source"] == "github"
    assert first.json()["details"]["height"] == 3
    second = client.post("/api/assets/github",json={"repo":"org/social","path":"assets/art.png"})
    assert second.json()["id"] == first.json()["id"]
    assert client.post("/api/assets/github",json={"repo":"org/unselected","path":"assets/art.png"}).status_code == 403


@pytest.mark.asyncio
async def test_github_binary_stream_supports_etag_and_limits_bytes():
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    def transport(request):
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(201,json={"token":"read-only"})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200,json={"repositories":[{"id":42,"full_name":"org/social"}]})
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304,headers={"etag":'"v1"'})
        return httpx.Response(200,content=b"binary\x00",headers={"etag":'"v1"'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        service = GitHubService("app",key,http)
        assert await service.read_binary(7,"org/social","assets/icon.png") == {"data":b"binary\x00","etag":'"v1"'}
        assert (await service.read_binary(7,"org/social","assets/icon.png",etag='"v1"'))["data"] is None
        with pytest.raises(GitHubError) as too_large:
            await service.read_binary(7,"org/social","assets/icon.png",max_bytes=2)
        assert too_large.value.status == 413
