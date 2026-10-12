"""Atomic OAuth issuance, rotation, and live agent permission resolution."""
import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, delete
from postchief.models import User, AgentKey, AuditEvent
from postchief.agents.policy import SCOPES
from .models import MCPGrant, MCPCode, MCPFamily, MCPAccess, MCPRefresh, MCPPending


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(verifier):
    if not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
        raise OAuthError('invalid_grant')
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


class OAuthError(Exception):
    def __init__(self, error='invalid_grant'):
        self.error = error
        super().__init__(error)


@dataclass(frozen=True)
class AccessIdentity:
    agent_key_id: str
    org_id: str
    client_id: str
    scopes: list[str]
    expires_at: datetime
    resource: str


class OAuthStore:
    def __init__(self, sessions):
        self.sessions = sessions

    def _grant(self, db, grant_id, client_id=None, resource=None):
        grant = db.get(MCPGrant, grant_id)
        key = db.get(AgentKey, grant.agent_key_id) if grant else None
        if (not grant or grant.revoked or not key or not key.active or
                (client_id is not None and grant.client_id != client_id) or
                (resource is not None and grant.resource != resource)):
            raise OAuthError()
        return grant, key

    def authorize(self, owner_id, client_id, redirect_uri, resource, pkce_challenge, scopes):
        with self.sessions() as db:
            code = self.authorize_in_session(db, owner_id, client_id, redirect_uri,
                resource, pkce_challenge, scopes)
            db.commit()
            return code

    def authorize_in_session(self, db, owner_id, client_id, redirect_uri, resource, pkce_challenge, scopes):
        if not scopes or set(scopes) - SCOPES or len(set(scopes)) != len(scopes):
            raise OAuthError('invalid_scope')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', pkce_challenge):
            raise OAuthError('invalid_request')
        owner = db.get(User, owner_id)
        if not owner: raise OAuthError('access_denied')
        key = AgentKey(org_id=owner.org_id, name='MCP: ' + client_id[:190],
            scopes=sorted(scopes), token_hash=digest(secrets.token_urlsafe(48)))
        db.add(key); db.flush()
        grant = MCPGrant(agent_key_id=key.id, client_id=client_id, resource=resource,
            scopes=sorted(scopes)); db.add(grant); db.flush()
        code = 'pc_code_' + secrets.token_urlsafe(48)
        db.add(MCPCode(token_hash=digest(code), grant_id=grant.id,
            challenge=pkce_challenge, redirect_uri=redirect_uri,
            expires_at=now() + timedelta(minutes=5)))
        db.add(AuditEvent(org_id=owner.org_id, actor_id=owner.id, action='mcp.consent',
            details={'key_id': key.id, 'client_id': client_id, 'scopes': sorted(scopes)}))
        return code

    def _tokens(self, db, family, scopes):
        access = 'pc_mcp_' + secrets.token_urlsafe(48)
        refresh = 'pc_refresh_' + secrets.token_urlsafe(48)
        db.add(MCPAccess(token_hash=digest(access), family_id=family.id, scopes=scopes,
            expires_at=now() + timedelta(minutes=15)))
        db.add(MCPRefresh(token_hash=digest(refresh), family_id=family.id, scopes=scopes,
            expires_at=family.expires_at))
        return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer',
            'expires_in': 900, 'scope': ' '.join(scopes)}

    def consume_code(self, code, client_id, redirect_uri, resource, verifier):
        expected = challenge(verifier)
        with self.sessions() as db:
            row = db.scalar(select(MCPCode).where(MCPCode.token_hash == digest(code)))
            if (not row or row.consumed or utc(row.expires_at) <= now() or
                    row.redirect_uri != redirect_uri or not secrets.compare_digest(row.challenge, expected)):
                raise OAuthError()
            grant, key = self._grant(db, row.grant_id, client_id, resource)
            changed = db.execute(update(MCPCode).where(MCPCode.id == row.id,
                MCPCode.consumed.is_(False), MCPCode.expires_at > now()).values(consumed=True)
                .execution_options(synchronize_session=False))
            if changed.rowcount != 1: raise OAuthError()
            family = MCPFamily(grant_id=grant.id, expires_at=now() + timedelta(days=30))
            db.add(family); db.flush()
            scopes = sorted(set(grant.scopes) & set(key.scopes))
            if not scopes: raise OAuthError('invalid_scope')
            tokens = self._tokens(db, family, scopes)
            db.commit()
            return tokens

    def rotate_refresh(self, token, client_id, resource, scopes=None):
        with self.sessions() as db:
            row = db.scalar(select(MCPRefresh).where(MCPRefresh.token_hash == digest(token)))
            if not row: raise OAuthError()
            family = db.scalar(select(MCPFamily).where(MCPFamily.id == row.family_id).with_for_update())
            grant, key = self._grant(db, family.grant_id, client_id, resource)
            if family.revoked or utc(family.expires_at) <= now(): raise OAuthError()
            db.refresh(row)
            if row.consumed:
                family.revoked = True; db.commit()
                raise OAuthError()
            if utc(row.expires_at) <= now(): raise OAuthError()
            desired = row.scopes if scopes is None else scopes
            if set(desired) - set(row.scopes): raise OAuthError('invalid_scope')
            effective = sorted(set(desired) & set(grant.scopes) & set(key.scopes))
            if not effective: raise OAuthError('invalid_scope')
            changed = db.execute(update(MCPRefresh).where(MCPRefresh.id == row.id,
                MCPRefresh.consumed.is_(False)).values(consumed=True)
                .execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                family.revoked = True; db.commit(); raise OAuthError()
            tokens = self._tokens(db, family, effective)
            db.commit()
            return tokens

    def resolve_access(self, token, resource):
        if not token.startswith('pc_mcp_') or len(token) > 200: return None
        with self.sessions() as db:
            row = db.scalar(select(MCPAccess).where(MCPAccess.token_hash == digest(token)))
            if not row or utc(row.expires_at) <= now(): return None
            family = db.get(MCPFamily, row.family_id)
            if not family or family.revoked or utc(family.expires_at) <= now(): return None
            try: grant, key = self._grant(db, family.grant_id, resource=resource)
            except OAuthError: return None
            return AccessIdentity(key.id, key.org_id, grant.client_id,
                sorted(set(row.scopes) & set(grant.scopes) & set(key.scopes)),
                utc(row.expires_at), grant.resource)

    def revoke(self, token, client_id):
        with self.sessions() as db:
            model = MCPAccess if token.startswith('pc_mcp_') else MCPRefresh
            row = db.scalar(select(model).where(model.token_hash == digest(token)))
            if row:
                family = db.get(MCPFamily, row.family_id)
                grant = db.get(MCPGrant, family.grant_id)
                if grant.client_id == client_id:
                    family.revoked = True; db.commit()

    def prune_expired(self, at=None):
        at = at or now()
        with self.sessions() as db:
            # Retain spent refresh records until their deadline to detect reuse.
            for model in [MCPAccess, MCPRefresh, MCPCode, MCPPending]:
                ids = list(db.scalars(select(model.id).where(model.expires_at <= at).limit(500)))
                if ids: db.execute(delete(model).where(model.id.in_(ids)))
            db.commit()
