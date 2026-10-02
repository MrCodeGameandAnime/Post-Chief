import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from postchief.db import get_db
from postchief.models import User, Organization, AgentKey, AuditEvent, now

router = APIRouter(prefix="/auth", tags=["Authentication"])
password_hash = PasswordHash.recommended()
dummy_password_hash = password_hash.hash(secrets.token_urlsafe(32))


@dataclass
class Actor:
    id: str
    org_id: str
    kind: str
    scopes: list[str]
    csrf: str = ""
    db: Session | None = None
    managed_action: bool = False


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bootstrap_owner(app):
    settings = app.state.settings
    if not settings.bootstrap_email or not settings.bootstrap_password.get_secret_value():
        return
    if len(settings.bootstrap_password.get_secret_value()) < 16:
        raise ValueError("Bootstrap password requires at least 16 characters")
    with app.state.sessions() as db:
        if db.scalar(select(User.id).limit(1)):
            return
        org = Organization(name="404 Builds")
        db.add(org)
        db.flush()
        db.add(User(org_id=org.id, email=settings.bootstrap_email.lower(), password_hash=password_hash.hash(settings.bootstrap_password.get_secret_value())))
        db.commit()


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    bearer = request.headers.get("authorization", "")
    if bearer.startswith("Bearer "):
        key = db.scalar(select(AgentKey).where(AgentKey.token_hash == digest(bearer[7:]), AgentKey.active.is_(True)))
        if not key:
            raise HTTPException(401, "Invalid agent key")
        db.add(AuditEvent(org_id=key.org_id,actor_id=key.id,action='agent.request',details={'method':request.method,'path':request.url.path}))
        db.commit()
        return Actor(key.id, key.org_id, "agent", key.scopes, db=db)
    token = request.cookies.get("pc_session")
    if not token:
        raise HTTPException(401, "Sign in required")
    try:
        claims = jwt.decode(token, request.app.state.settings.signing_key.get_secret_value(), algorithms=["HS256"], audience="post-chief", options={"require":["exp","sub","csrf"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired") from None
    user = db.get(User, claims["sub"])
    if not user:
        raise HTTPException(401, "Invalid session")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != request.app.state.settings.frontend_url:
            raise HTTPException(403, "Invalid request origin")
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), claims["csrf"]):
            raise HTTPException(403, "CSRF token required")
    return Actor(user.id, user.org_id, "owner", ["*"], claims["csrf"])


def require_owner(actor: Actor = Depends(current_actor)):
    if actor.kind != "owner":
        raise HTTPException(403, "Owner permission required")
    return actor


def require_scope(actor: Actor, scope: str):
    if actor.kind != "owner" and scope not in actor.scopes:
        raise HTTPException(403, f"Scope required: {scope}")
    if actor.kind=='agent':
        from postchief.agents.policy import DEFAULTS,mode_for
        if scope in DEFAULTS:
            mode=mode_for(actor.db,actor.org_id,scope)
            if mode=='DISABLED': raise HTTPException(403,'Action disabled by organization autonomy policy')
            if mode=='APPROVAL' and not actor.managed_action:
                raise HTTPException(403,'Owner approval required; submit this action through /api/agent/actions')


class Login(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(data: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    origin = request.headers.get("origin")
    if origin and origin != request.app.state.settings.frontend_url:
        raise HTTPException(403, "Invalid request origin")
    ip = request.client.host if request.client else "unknown"
    fingerprint = digest(request.app.state.settings.signing_key.get_secret_value() + ":" + ip)
    failures = db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "auth.failed", AuditEvent.created_at >= now()-timedelta(minutes=15), AuditEvent.details["ip"].as_string() == fingerprint))
    if failures >= 10:
        raise HTTPException(429, "Too many sign-in attempts; try again in 15 minutes", headers={"Retry-After":"900"})
    user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
    # Keep password hashing work comparable for unknown users.
    hashed = user.password_hash if user else dummy_password_hash
    valid = password_hash.verify(data.password, hashed)
    if not user or not valid:
        org_id = user.org_id if user else db.scalar(select(Organization.id).limit(1))
        if org_id:
            db.add(AuditEvent(org_id=org_id, actor_id="anonymous", action="auth.failed", details={"ip":fingerprint}))
            db.commit()
        raise HTTPException(401, "Invalid email or password")
    db.add(AuditEvent(org_id=user.org_id, actor_id=user.id, action="auth.login", details={}))
    db.commit()
    csrf = secrets.token_urlsafe(32)
    token = jwt.encode({"sub":user.id,"aud":"post-chief","csrf":csrf,"exp":now()+timedelta(hours=8)}, request.app.state.settings.signing_key.get_secret_value(), algorithm="HS256")
    response.set_cookie("pc_session", token, httponly=True, secure=request.app.state.settings.secure_cookies, samesite="lax", max_age=28800, path="/api")
    return {"email":user.email,"csrf":csrf}


@router.get("/me")
def me(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    if actor.kind == "owner":
        user = db.get(User, actor.id)
        return {"email":user.email,"org_id":actor.org_id,"kind":actor.kind,"csrf":actor.csrf}
    return {"org_id":actor.org_id,"kind":actor.kind,"scopes":actor.scopes}


@router.post("/logout")
def logout(response: Response, actor: Actor = Depends(current_actor)):
    response.delete_cookie("pc_session", path="/api")
    return {"status":"signed_out"}
