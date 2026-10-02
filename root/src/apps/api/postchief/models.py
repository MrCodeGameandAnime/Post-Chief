from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Scoped(Entity):
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)


class Organization(Entity, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    autonomy: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Scoped, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class GitHubConnection(Scoped, Base):
    __tablename__ = "github_connections"
    __table_args__ = (UniqueConstraint("org_id"),)
    installation_id: Mapped[int]
    workspace: Mapped[str] = mapped_column(String(300), default="")
    source_repos: Mapped[list] = mapped_column(JSON, default=list)


class SocialAccount(Scoped, Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("org_id", "provider", "remote_id"),)
    provider: Mapped[str] = mapped_column(String(30))
    remote_id: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(300))
    credentials: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Asset(Scoped, Base):
    __tablename__ = "assets"
    name: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100))
    storage_path: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="upload")
    checksum: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int]
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Campaign(Scoped, Base):
    __tablename__ = "campaigns"
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    github_path: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=1)


class Publication(Scoped, Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("campaign_id", "account_id"), CheckConstraint("status IN ('pending','processing','published','failed','retrying','cancelled')", name="publication_status"))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("social_accounts.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    provider_id: Mapped[str | None] = mapped_column(Text)
    provider_url: Mapped[str | None] = mapped_column(Text)
    provider_state: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignAsset(Scoped, Base):
    __tablename__ = "campaign_assets"
    __table_args__ = (UniqueConstraint("campaign_id", "asset_id"),)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)


class AnalyticsSnapshot(Scoped, Base):
    __tablename__ = "analytics_snapshots"
    publication_id: Mapped[str] = mapped_column(ForeignKey("publications.id"), index=True)
    metrics: Mapped[dict] = mapped_column(JSON)


class AgentKey(Scoped, Base):
    __tablename__ = "agent_keys"
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(default=True)


class AgentRun(Scoped, Base):
    __tablename__ = "agent_runs"
    key_id: Mapped[str] = mapped_column(ForeignKey("agent_keys.id"))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="running")


class Approval(Scoped, Base):
    __tablename__ = "approvals"
    actor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Scoped, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class OAuthState(Scoped, Base):
    __tablename__ = "oauth_states"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(default=False)


class WebhookEvent(Entity, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "delivery_id"),)
    provider: Mapped[str] = mapped_column(String(30))
    delivery_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)
