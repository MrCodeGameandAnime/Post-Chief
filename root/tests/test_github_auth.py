import base64
import json
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from postchief.github.service import GitHubService, GitHubError
from postchief.vault import Vault


def test_sessions_reject_wrong_password_and_missing_csrf(app):
    with TestClient(app) as c:
        assert c.get("/api/auth/me").status_code == 401
        assert c.post("/api/auth/login", json={"email":"owner@example.test","password":"wrong"}).status_code == 401
        login = c.post("/api/auth/login", json={"email":"owner@example.test","password":"a-long-test-password"})
        assert login.status_code == 200
        assert c.get("/api/auth/me").json()["email"] == "owner@example.test"
        assert c.post("/api/auth/logout").status_code == 403
        assert c.post("/api/auth/logout", headers={"X-CSRF-Token":login.json()["csrf"]}).status_code == 200
        assert c.get("/api/auth/me").status_code == 401


def test_vault_does_not_store_plain_credentials(app):
    vault = Vault(app.state.settings.encryption_key.get_secret_value())
    ciphertext = vault.encrypt({"access_token":"private-provider-token"})
    assert "private-provider-token" not in ciphertext
    assert vault.decrypt(ciphertext) == {"access_token":"private-provider-token"}


@pytest.mark.asyncio
async def test_github_checks_installation_repositories_and_preserves_sha():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    minted = []
    def transport(request):
        if request.url.path == "/app/installations/7/access_tokens":
            minted.append(json.loads(request.content or b"{}"))
            return httpx.Response(201, json={"token":"installation-token","expires_at":"2099-01-01T00:00:00Z"})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={"total_count":1,"repositories":[{"id":42,"full_name":"org/social"}]})
        if request.method == "PUT":
            payload = json.loads(request.content)
            assert payload["sha"] == "current-sha"
            assert base64.b64decode(payload["content"]) == b"New content"
            return httpx.Response(409, json={"message":"sha does not match"})
        return httpx.Response(200, json={"sha":"current-sha","content":base64.b64encode(b"Old content").decode(),"encoding":"base64"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        service = GitHubService("app-id", key, http)
        assert (await service.read_file(7, "org/social", "docs/BRAND.md"))["content"] == "Old content"
        assert minted[-1] == {"permissions":{"metadata":"read","contents":"read"},"repository_ids":[42]}
        with pytest.raises(GitHubError) as denied:
            await service.read_file(7, "org/ungranted", "README.md")
        assert denied.value.status == 403
        with pytest.raises(GitHubError) as conflict:
            await service.write_file(7, "org/social", "docs/BRAND.md", "New content", "current-sha")
        assert conflict.value.status == 409
        assert minted[-1] == {"permissions":{"metadata":"read","contents":"write"},"repository_ids":[42]}
        with pytest.raises(GitHubError):
            await service.read_file(7, "org/social", "../secrets")
