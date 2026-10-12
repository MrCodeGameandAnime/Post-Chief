"""Durable OAuth records; plaintext bearer tokens are never stored."""
from sqlalchemy import String, Text, JSON, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from postchief.models import Base, Entity


class MCPClient(Base):
    __tablename__ = 'mcp_clients'
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    redirect_uris: Mapped[list] = mapped_column(JSON)


class MCPGrant(Entity, Base):
    __tablename__ = 'mcp_grants'
    agent_key_id: Mapped[str] = mapped_column(ForeignKey('agent_keys.id'), index=True)
    client_id: Mapped[str] = mapped_column(String(200))
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSON)
    revoked: Mapped[bool] = mapped_column(default=False)


class MCPPending(Entity, Base):
    __tablename__ = 'mcp_pending'
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    consumed: Mapped[bool] = mapped_column(default=False)


class MCPCode(Entity, Base):
    __tablename__ = 'mcp_codes'
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey('mcp_grants.id'), index=True)
    challenge: Mapped[str] = mapped_column(String(43))
    redirect_uri: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    consumed: Mapped[bool] = mapped_column(default=False)


class MCPFamily(Entity, Base):
    __tablename__ = 'mcp_families'
    grant_id: Mapped[str] = mapped_column(ForeignKey('mcp_grants.id'), index=True)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    revoked: Mapped[bool] = mapped_column(default=False)


class MCPAccess(Entity, Base):
    __tablename__ = 'mcp_access'
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[str] = mapped_column(ForeignKey('mcp_families.id'), index=True)
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)


class MCPRefresh(Entity, Base):
    __tablename__ = 'mcp_refresh'
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[str] = mapped_column(ForeignKey('mcp_families.id'), index=True)
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    consumed: Mapped[bool] = mapped_column(default=False)
