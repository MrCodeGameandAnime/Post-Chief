import hashlib
import hmac
import json
from sqlalchemy import select, func
from postchief.models import GitHubConnection, WebhookEvent
from postchief.github.routes import get_service


class InstalledGitHub:
    async def repositories(self, installation):
        assert installation == 7
        return [{"id":42,"full_name":"org/social"},{"id":43,"full_name":"org/product"}]
    async def read_file(self, installation, repo, path):
        return {"sha":"sha-1","path":path,"content":"Brand rules"}


def test_workspace_selection_and_read_scope(client, app):
    app.dependency_overrides[get_service] = lambda: InstalledGitHub()
    assert client.post("/api/github/workspace", json={"installation_id":7,"workspace":"org/not-installed"}).status_code == 403
    assert client.post("/api/github/workspace", json={"installation_id":7,"workspace":"org/social","source_repos":["org/product"]}).status_code == 200
    assert client.get("/api/github/content", params={"repo":"org/social","path":"docs/BRAND.md"}).json()["content"] == "Brand rules"
    assert client.get("/api/github/content", params={"repo":"org/unselected","path":"README.md"}).status_code == 403
    assert client.put("/api/github/content", json={"repo":"org/product","path":"README.md","content":"overwrite","sha":"old"}).status_code == 403


def test_webhook_signature_and_delivery_deduplication(client, app):
    app.state.settings.github_webhook_secret = "webhook-secret"
    payload = json.dumps({"action":"created","installation":{"id":7}}).encode()
    assert client.post("/api/webhooks/github", content=payload, headers={"X-GitHub-Delivery":"delivery-1"}).status_code == 401
    signature = "sha256=" + hmac.new(b"webhook-secret", payload, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Delivery":"delivery-1","X-Hub-Signature-256":signature,"X-GitHub-Event":"installation"}
    assert client.post("/api/webhooks/github", content=payload, headers=headers).status_code == 200
    assert client.post("/api/webhooks/github", content=payload, headers=headers).json()["duplicate"] is True
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1


def test_login_throttles_repeated_failures(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        for _ in range(10):
            assert c.post("/api/auth/login", json={"email":"owner@example.test","password":"wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"email":"owner@example.test","password":"wrong"}).status_code == 429
