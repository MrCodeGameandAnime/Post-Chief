from dataclasses import asdict
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor, current_actor, require_owner, require_scope
from postchief.db import get_db
from postchief.models import SocialAccount, AuditEvent
from postchief.vault import Vault
from postchief.providers.bluesky import BlueskyProvider
from postchief.providers.registry import PROVIDERS

router = APIRouter(tags=["Provider connections"])


async def get_bluesky_provider():
    async with httpx.AsyncClient(timeout=120) as http:
        yield BlueskyProvider(http)


def save_connection(db: Session, settings, actor: Actor, provider: str, remote_id: str, name: str, credentials: dict, expires_at=None):
    row = db.scalar(select(SocialAccount).where(SocialAccount.org_id == actor.org_id,SocialAccount.provider == provider,SocialAccount.remote_id == remote_id))
    if not row:
        row = SocialAccount(org_id=actor.org_id,provider=provider,remote_id=remote_id,name=name)
        db.add(row)
    row.name, row.active, row.expires_at = name, True, expires_at
    row.credentials = Vault(settings.encryption_key.get_secret_value()).encrypt(credentials)
    db.add(AuditEvent(org_id=actor.org_id,actor_id=actor.id,action="connection.connect",details={"provider":provider,"remote_id":remote_id}))
    db.commit()
    return {"id":row.id,"provider":provider,"remote_id":remote_id,"name":name,"active":True}


class BlueskyConnect(BaseModel):
    identifier: str = Field(min_length=3,max_length=253)
    password: SecretStr = Field(min_length=1,max_length=1024)


@router.post("/connections/bluesky",status_code=201)
async def connect_bluesky(data: BlueskyConnect, request: Request, actor: Actor = Depends(require_owner), db: Session = Depends(get_db), provider: BlueskyProvider = Depends(get_bluesky_provider)):
    credentials = await provider.connect(data.identifier,data.password.get_secret_value())
    return save_connection(db,request.app.state.settings,actor,"bluesky",credentials["did"],credentials["handle"],credentials)


@router.delete("/connections/{account_id}")
def disconnect(account_id: str, actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    row = db.scalar(select(SocialAccount).where(SocialAccount.id == account_id,SocialAccount.org_id == actor.org_id))
    if not row:
        raise HTTPException(404,"Connection not found")
    row.active, row.credentials, row.expires_at = False, "", None
    db.add(AuditEvent(org_id=actor.org_id,actor_id=actor.id,action="connection.disconnect",details={"account_id":account_id,"provider":row.provider}))
    db.commit()
    return {"id":row.id,"active":False}


@router.get("/capabilities")
def capabilities(actor: Actor = Depends(current_actor)):
    require_scope(actor,"campaigns:read")
    return [{"provider":name,"capabilities":asdict(cls.capabilities),"limits":cls.limits,"idempotent":cls.idempotent} for name,cls in PROVIDERS.items()]
