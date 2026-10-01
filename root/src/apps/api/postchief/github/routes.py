import hashlib
import hmac
import json
from urllib.parse import quote
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from postchief.auth import Actor, current_actor, require_owner, require_scope
from postchief.db import get_db
from postchief.models import GitHubConnection, WebhookEvent, AuditEvent
from postchief.github.service import GitHubService

router = APIRouter(prefix="/github", tags=["GitHub workspace"])
webhooks = APIRouter(prefix="/webhooks", tags=["Webhooks"])


async def get_service(request: Request):
    settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=30) as client:
        yield GitHubService(settings.github_app_id, settings.github_private_key.get_secret_value(), client)


def connection(db: Session, actor: Actor):
    row = db.scalar(select(GitHubConnection).where(GitHubConnection.org_id == actor.org_id))
    if not row:
        raise HTTPException(404, "Connect a GitHub App installation first")
    return row


def selected_repo(row: GitHubConnection, repo: str, write=False):
    if write and repo != row.workspace:
        raise HTTPException(403, "Only the social workspace is writable")
    if repo not in [row.workspace, *row.source_repos]:
        raise HTTPException(403, "Repository is not selected for this organization")


@router.get("/installation-link")
def installation_link(request: Request, actor: Actor = Depends(require_owner)):
    slug = request.app.state.settings.github_app_slug
    if not slug:
        raise HTTPException(503, "Configure GITHUB_APP_SLUG")
    return {"url":f"https://github.com/apps/{quote(slug, safe='')}/installations/new"}


@router.get("/installations/{installation_id}/repositories")
async def installation_repositories(installation_id: int, actor: Actor = Depends(require_owner), service: GitHubService = Depends(get_service)):
    if installation_id <= 0:
        raise HTTPException(400, "Invalid installation ID")
    return await service.repositories(installation_id)


class WorkspaceSelection(BaseModel):
    installation_id: int = Field(gt=0)
    workspace: str = Field(min_length=3, max_length=300)
    source_repos: list[str] = Field(default_factory=list, max_length=50)


@router.post("/workspace")
async def select_workspace(data: WorkspaceSelection, actor: Actor = Depends(require_owner), db: Session = Depends(get_db), service: GitHubService = Depends(get_service)):
    allowed = {r["full_name"] for r in await service.repositories(data.installation_id)}
    if any(repo not in allowed for repo in [data.workspace, *data.source_repos]):
        raise HTTPException(403, "Select repositories granted to the GitHub App")
    row = db.scalar(select(GitHubConnection).where(GitHubConnection.org_id == actor.org_id))
    if not row:
        row = GitHubConnection(org_id=actor.org_id, installation_id=data.installation_id)
        db.add(row)
    row.installation_id, row.workspace, row.source_repos = data.installation_id, data.workspace, sorted(set(data.source_repos) - {data.workspace})
    db.add(AuditEvent(org_id=actor.org_id, actor_id=actor.id, action="github.workspace.select", details={"workspace":row.workspace,"source_repos":row.source_repos}))
    db.commit()
    return {"workspace":row.workspace,"source_repos":row.source_repos,"installation_id":row.installation_id}


@router.get("/workspace")
def workspace(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "github:read")
    row = connection(db, actor)
    return {"workspace":row.workspace,"source_repos":row.source_repos,"installation_id":row.installation_id}


@router.get("/repositories")
async def repositories(actor: Actor = Depends(current_actor), db: Session = Depends(get_db), service: GitHubService = Depends(get_service)):
    require_scope(actor, "github:read")
    row = connection(db, actor)
    selected = {row.workspace, *row.source_repos}
    return [r for r in await service.repositories(row.installation_id) if r["full_name"] in selected]


@router.get("/content")
async def read_content(repo: str, path: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db), service: GitHubService = Depends(get_service)):
    require_scope(actor, "github:read")
    row = connection(db, actor)
    selected_repo(row, repo)
    return await service.read_file(row.installation_id, repo, path)


class ContentWrite(BaseModel):
    repo: str
    path: str = Field(max_length=1000)
    content: str = Field(max_length=1_000_000)
    sha: str | None = None


@router.put("/content")
async def write_content(data: ContentWrite, actor: Actor = Depends(current_actor), db: Session = Depends(get_db), service: GitHubService = Depends(get_service)):
    require_scope(actor, "github:write")
    row = connection(db, actor)
    selected_repo(row, data.repo, write=True)
    result = await service.write_file(row.installation_id, data.repo, data.path, data.content, data.sha)
    db.add(AuditEvent(org_id=actor.org_id, actor_id=actor.id, action="github.content.write", details={"repo":data.repo,"path":data.path}))
    db.commit()
    return {"sha":result.get("content", {}).get("sha"),"path":data.path}


@router.get("/repositories/{owner}/{repo}/{kind}")
async def activity(owner: str, repo: str, kind: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db), service: GitHubService = Depends(get_service)):
    require_scope(actor, "github:read")
    if kind not in ("releases","activity"):
        raise HTTPException(404, "Unknown repository operation")
    row = connection(db, actor)
    selected_repo(row, f"{owner}/{repo}")
    data = await service.activity(row.installation_id, f"{owner}/{repo}", kind)
    if kind == "releases":
        return [{"id":r["id"],"tag":r["tag_name"],"name":r.get("name"),"body":r.get("body"),"url":r["html_url"],"published_at":r.get("published_at")} for r in data]
    return [{"sha":r["sha"],"message":r["commit"]["message"],"url":r["html_url"]} for r in data]


@webhooks.post("/github")
async def receive_github_webhook(request: Request, db: Session = Depends(get_db)):
    secret = request.app.state.settings.github_webhook_secret
    secret = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    if not secret:
        raise HTTPException(503, "Configure GitHub webhook verification")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_000_000:
            raise HTTPException(413, "Webhook payload is too large")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), expected):
        raise HTTPException(401, "Invalid webhook signature")
    delivery = request.headers.get("x-github-delivery", "")
    if not delivery or len(delivery) > 200:
        raise HTTPException(400, "Webhook delivery ID required")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Invalid webhook JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid webhook payload")
    db.add(WebhookEvent(provider="github", delivery_id=delivery, payload=payload))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"accepted":True,"duplicate":True}
    if request.headers.get("x-github-event") == "installation" and payload.get("action") in ("deleted", "suspend"):
        db.execute(delete(GitHubConnection).where(GitHubConnection.installation_id == payload.get("installation", {}).get("id")))
    db.commit()
    return {"accepted":True,"duplicate":False}
