from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from postchief.auth import Actor, current_actor, require_scope
from postchief.db import get_db
from postchief.models import Campaign, Publication, SocialAccount
from postchief.campaigns.schemas import CampaignCreate, CampaignPatch, aware
from postchief.campaigns.external import external_posts
from postchief.campaigns.service import get_campaign, check_refs, create_campaign, audit, serialize_campaign, serialize_publication, sync_assets

router = APIRouter(tags=["Campaigns"])


@router.post("/campaigns", status_code=201)
def create(data: CampaignCreate, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "campaigns:write")
    return create_campaign(db, actor, data)


@router.get("/campaigns")
@router.get("/planner")
def list_campaigns(start: datetime | None = None, end: datetime | None = None, limit: int = Query(default=100,ge=1,le=100), offset: int = Query(default=0,ge=0), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "campaigns:read")
    query = select(Campaign).where(Campaign.org_id == actor.org_id)
    try:
        start, end = aware(start), aware(end)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if start and end and end <= start:
        raise HTTPException(422, "End must follow start")
    if start:
        query = query.where(Campaign.scheduled_at >= start)
    if end:
        query = query.where(Campaign.scheduled_at < end)
    rows = db.scalars(query.order_by(Campaign.created_at.desc()).limit(limit).offset(offset))
    return [serialize_campaign(db,r) for r in rows]


@router.get("/campaigns/{campaign_id}")
def retrieve(campaign_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "campaigns:read")
    return serialize_campaign(db, get_campaign(db, actor.org_id, campaign_id))


@router.patch("/campaigns/{campaign_id}")
def update(campaign_id: str, data: CampaignPatch, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "campaigns:write")
    row = get_campaign(db, actor.org_id, campaign_id, lock=True)
    if row.revision != data.revision:
        raise HTTPException(409, "Campaign changed; reload before editing")
    if row.status != "draft":
        raise HTTPException(409, "Only drafts can be edited; cancel scheduling first")
    pubs = list(db.scalars(select(Publication).where(Publication.campaign_id == row.id)))
    if any(p.status in ("processing","published") or p.attempts > 0 for p in pubs):
        raise HTTPException(409, "Attempted publications retain their original content; create a new draft for revisions")
    merged = {"title":row.title,"body":row.body,"asset_ids":row.asset_ids,"account_ids":[p.account_id for p in pubs],"overrides":row.overrides,"scheduled_at":row.scheduled_at,"github_path":row.github_path}
    # SQLite returns UTC timestamps without a tzinfo; restore it at this boundary.
    from datetime import timezone
    if merged["scheduled_at"] is not None and merged["scheduled_at"].tzinfo is None:
        merged["scheduled_at"] = merged["scheduled_at"].replace(tzinfo=timezone.utc)
    merged.update(data.model_dump(exclude_unset=True, exclude={"revision"}))
    try:
        validated = CampaignCreate.model_validate(merged)
    except ValidationError:
        raise HTTPException(422, "Invalid campaign changes") from None
    accounts = check_refs(db, actor.org_id, validated)
    if external_posts(db, row) and any(account.provider == 'x' for account in accounts):
        raise HTTPException(409, 'An X handoff is already recorded; create a separate campaign for another X post')
    for key in ("title","body","asset_ids","scheduled_at","github_path"):
        setattr(row, key, getattr(validated,key))
    row.overrides = {k:v.model_dump(exclude_none=True) for k,v in validated.overrides.items()}
    removed = [p for p in pubs if p.account_id not in validated.account_ids]
    for p in removed:
        db.delete(p)
    existing = {p.account_id for p in pubs}
    for account_id in validated.account_ids:
        if account_id not in existing:
            db.add(Publication(org_id=actor.org_id, campaign_id=row.id, account_id=account_id))
    for pub in pubs:
        if pub not in removed:
            pub.status, pub.error, pub.provider_state, pub.next_attempt_at = 'pending', None, {}, None
    row.revision += 1
    sync_assets(db,row)
    audit(db, actor, "campaign.update", {"campaign_id":row.id,"revision":row.revision})
    db.commit()
    return serialize_campaign(db,row)


@router.delete("/campaigns/{campaign_id}")
def delete_draft(campaign_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "campaigns:write")
    row = get_campaign(db, actor.org_id, campaign_id, lock=True)
    protected = db.scalar(select(Publication.id).where(Publication.campaign_id == row.id, (Publication.status.in_(["published","processing"])) | (Publication.attempts > 0)).limit(1))
    if row.status != "draft" or protected or external_posts(db, row):
        raise HTTPException(409, "Only unpublished drafts can be deleted")
    db.execute(delete(Publication).where(Publication.campaign_id == row.id))
    db.delete(row)
    audit(db,actor,"campaign.delete",{"campaign_id":campaign_id})
    db.commit()
    return {"deleted":True}


@router.get("/publications/{publication_id}")
def publication(publication_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, "campaigns:read")
    row = db.scalar(select(Publication).where(Publication.id == publication_id, Publication.org_id == actor.org_id))
    if not row:
        raise HTTPException(404,"Publication not found")
    return serialize_publication(db,row)


@router.get("/connections")
def connections(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor,"campaigns:read")
    rows = db.scalars(select(SocialAccount).where(SocialAccount.org_id == actor.org_id).order_by(SocialAccount.provider))
    return [{"id":r.id,"provider":r.provider,"name":r.name,"remote_id":r.remote_id,"active":r.active,"expires_at":r.expires_at} for r in rows]
