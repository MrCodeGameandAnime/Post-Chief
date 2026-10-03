from datetime import timezone
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from postchief.auth import Actor
from postchief.models import Campaign, Publication, Asset, SocialAccount, AuditEvent, CampaignAsset, AnalyticsSnapshot
from postchief.publishing.links import instagram_permalink
from postchief.campaigns.schemas import CampaignCreate
from postchief.campaigns.external import external_posts


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def get_campaign(db: Session, org_id: str, campaign_id: str, lock=False):
    query = select(Campaign).where(Campaign.org_id == org_id, Campaign.id == campaign_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if not row:
        raise HTTPException(404, "Campaign not found")
    return row


def check_refs(db: Session, org_id: str, data: CampaignCreate):
    accounts = list(db.scalars(select(SocialAccount).where(SocialAccount.org_id == org_id, SocialAccount.id.in_(data.account_ids), SocialAccount.active.is_(True))))
    if len(accounts) != len(data.account_ids):
        raise HTTPException(404, "Connected account not found")
    if set(data.overrides) - {a.provider for a in accounts}:
        raise HTTPException(422, "Overrides must target a selected provider")
    assets = set(data.asset_ids)
    for override in data.overrides.values():
        assets.update(override.asset_ids or [])
    found = set(db.scalars(select(Asset.id).where(Asset.org_id == org_id, Asset.id.in_(assets))))
    if assets != found:
        raise HTTPException(404, "Asset not found")
    return accounts


def audit(db: Session, actor: Actor, action: str, details: dict):
    db.add(AuditEvent(org_id=actor.org_id, actor_id=actor.id, action=action, details=details))


def sync_assets(db: Session, row: Campaign):
    db.execute(delete(CampaignAsset).where(CampaignAsset.campaign_id == row.id))
    ids = set(row.asset_ids)
    for override in row.overrides.values():
        ids.update(override.get("asset_ids") or [])
    for post in external_posts(db, row):
        ids.update(post.get('asset_ids', []))
    db.add_all(CampaignAsset(org_id=row.org_id,campaign_id=row.id,asset_id=asset_id) for asset_id in ids)


def serialize_publication(db: Session, row: Publication):
    account = db.get(SocialAccount, row.account_id)
    url=row.provider_url
    if not url and account.provider=='instagram':
        snapshot=db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.publication_id==row.id,AnalyticsSnapshot.org_id==row.org_id)
            .order_by(AnalyticsSnapshot.created_at.desc()).limit(1))
        if snapshot:
            url=instagram_permalink(snapshot.metrics.get('provider_metrics',{}).get('provider',{}).get('permalink'))
    result = {"id":row.id,"campaign_id":row.campaign_id,"account_id":row.account_id,"provider":account.provider,"account_name":account.name,"status":row.status,"provider_id":row.provider_id,"url":url,"error":row.error,"attempts":row.attempts,"published_at":iso(row.published_at)}
    event=db.scalar(select(AuditEvent).where(AuditEvent.org_id==row.org_id,
        AuditEvent.action=='publication.metadata',AuditEvent.details['publication_id'].as_string()==row.id)
        .order_by(AuditEvent.created_at.desc()).limit(1))
    metadata=event.details.get('metadata') if event else None
    if metadata: result['provider_metadata']=metadata
    edits = db.scalars(select(AuditEvent).where(AuditEvent.org_id == row.org_id,
        AuditEvent.action == 'publication.text_edit').order_by(AuditEvent.created_at)).all()
    history = [{"at": iso(e.created_at), **e.details} for e in edits if e.details.get('publication_id') == row.id]
    if history:
        result['text_edits'] = history
    return result


def serialize_campaign(db: Session, row: Campaign):
    publications = list(db.scalars(select(Publication).where(Publication.campaign_id == row.id, Publication.org_id == row.org_id).order_by(Publication.created_at)))
    result = {"id":row.id,"title":row.title,"body":row.body,"asset_ids":row.asset_ids,"overrides":row.overrides,"scheduled_at":iso(row.scheduled_at),"created_at":iso(row.created_at),"status":row.status,"revision":row.revision,"github_path":row.github_path,"publications":[serialize_publication(db,p) for p in publications]}
    reported = external_posts(db, row)
    if reported:
        result['external_posts'] = reported
    return result


def create_campaign(db: Session, actor: Actor, data: CampaignCreate):
    check_refs(db, actor.org_id, data)
    row = Campaign(org_id=actor.org_id, title=data.title, body=data.body, asset_ids=data.asset_ids, overrides={k:v.model_dump(exclude_none=True) for k,v in data.overrides.items()}, scheduled_at=data.scheduled_at, github_path=data.github_path)
    db.add(row)
    db.flush()
    db.add_all(Publication(org_id=actor.org_id, campaign_id=row.id, account_id=a) for a in data.account_ids)
    sync_assets(db,row)
    audit(db, actor, "campaign.create", {"campaign_id":row.id})
    db.commit()
    return serialize_campaign(db, row)
