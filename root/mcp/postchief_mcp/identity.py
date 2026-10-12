"""Resolve OAuth bearer tokens to existing scoped agent actors."""
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from postchief.auth import Actor
from .store import OAuthStore


def resolve_oauth_actor(token, db, settings):
    if not settings.mcp_enabled:
        raise HTTPException(401, 'MCP access is disabled')
    store = OAuthStore(sessionmaker(db.get_bind(), expire_on_commit=False))
    identity = store.resolve_access(token, settings.public_url.rstrip('/') + '/mcp')
    if identity is None: raise HTTPException(401, 'Agent authorization expired or was revoked')
    return Actor(identity.agent_key_id, identity.org_id, 'agent', identity.scopes, db=db)
