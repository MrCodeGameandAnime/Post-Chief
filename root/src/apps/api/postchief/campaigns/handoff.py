import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from postchief.auth import Actor, require_owner
from postchief.db import get_db
from postchief.models import Asset, AuditEvent, Campaign, Publication, SocialAccount, now
from postchief.campaigns.external import external_posts
from postchief.campaigns.schemas import aware
from postchief.campaigns.service import get_campaign, iso, serialize_campaign, sync_assets

router = APIRouter(tags=['Manual handoff'])


class ExternalXPost(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    body: str = Field(max_length=20000)
    asset_ids: list[str] = Field(default_factory=list, max_length=4)
    url: str = Field(max_length=500)
    published_at: datetime
    confirmed_published: Literal[True]

    @field_validator('url')
    @classmethod
    def post_url(cls, value):
        parsed = urlsplit(value.strip())
        if (parsed.scheme != 'https' or parsed.netloc.lower() not in
                {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'} or
                not re.fullmatch(r'/(?:[A-Za-z0-9_]{1,15}|i/web)/status/[0-9]{1,19}/?', parsed.path)):
            raise ValueError('Paste the HTTPS URL of a specific X post')
        return 'https://x.com' + parsed.path.rstrip('/')

    @field_validator('published_at')
    @classmethod
    def published_time(cls, value):
        value = aware(value)
        if value > now():
            raise ValueError('Published time cannot be in the future')
        return value

    @model_validator(mode='after')
    def content(self):
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError('Duplicate assets are not allowed')
        if not self.body.strip() and not self.asset_ids:
            raise ValueError('Provide the copy or media actually posted')
        return self


@router.post('/campaigns/{campaign_id}/external/x')
def record_x(campaign_id: str, data: ExternalXPost,
             actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    campaign = get_campaign(db, actor.org_id, campaign_id, lock=True)
    existing = external_posts(db, campaign)
    details = {'campaign_id': campaign.id, 'provider': 'x', 'url': data.url,
               'provider_id': data.url.rsplit('/', 1)[-1], 'body': data.body,
               'asset_ids': data.asset_ids, 'published_at': iso(data.published_at),
               'verification': 'owner_reported', 'delivery': 'manual',
               'reported_by': actor.id}
    if existing:
        if all(existing[0].get(key) == value for key, value in details.items()):
            return serialize_campaign(db, campaign)
        raise HTTPException(409, 'An X handoff is already recorded for this campaign')
    if campaign.revision != data.revision:
        raise HTTPException(409, 'Campaign changed; reload before recording the handoff')
    direct = db.scalar(select(Publication.id).join(SocialAccount).where(
        Publication.campaign_id == campaign.id, Publication.org_id == actor.org_id,
        SocialAccount.provider == 'x').limit(1))
    if direct:
        raise HTTPException(409, 'This campaign already has an API X destination; review its outcome instead')
    assets = list(db.scalars(select(Asset).where(
        Asset.org_id == actor.org_id, Asset.id.in_(data.asset_ids))))
    if len(assets) != len(data.asset_ids):
        raise HTTPException(404, 'Asset not found')
    if any(asset.mime_type not in {'image/jpeg', 'image/png', 'image/webp'} or
           asset.byte_size > 5_000_000 for asset in assets):
        raise HTTPException(422, 'The MVP X handoff supports up to four JPEG, PNG or WebP images, 5 MB each')
    changed = db.execute(update(Campaign).where(Campaign.id == campaign.id,
        Campaign.org_id == actor.org_id, Campaign.revision == data.revision)
        .values(revision=data.revision + 1).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(409, 'Campaign changed; reload before recording the handoff')
    db.add(AuditEvent(org_id=actor.org_id, actor_id=actor.id,
                     action='campaign.external_post', details=details))
    db.flush()
    sync_assets(db, campaign)
    db.commit()
    db.refresh(campaign)
    return serialize_campaign(db, campaign)
