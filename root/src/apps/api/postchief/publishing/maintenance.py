"""Owner-reviewed additions and text edits without resetting published deliveries."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import hashlib
import re
import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from cryptography.fernet import InvalidToken
from postchief.auth import Actor, require_owner
from postchief.db import get_db
from postchief.models import Publication, SocialAccount, AuditEvent
from postchief.campaigns.schemas import CampaignCreate, YouTubeOptions, TikTokOptions
from postchief.campaigns.external import external_posts
from postchief.campaigns.service import get_campaign, check_refs, audit, serialize_campaign, sync_assets
from postchief.publishing.media import media_for_campaign
from postchief.providers.registry import get_provider
from postchief.vault import Vault
from provider_contracts import ProviderError

router = APIRouter(tags=['Campaign maintenance'])


class Destination(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    account_id: str
    body: str = Field(max_length=20000)
    youtube: YouTubeOptions | None = None
    tiktok: TikTokOptions | None = None


@router.post('/campaigns/{campaign_id}/destinations', status_code=201)
def add_destination(campaign_id: str, data: Destination, request: Request,
                    actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    row = get_campaign(db, actor.org_id, campaign_id, lock=True)
    if row.revision != data.revision:
        raise HTTPException(409, 'Campaign changed; reopen before adding a destination')
    pubs = list(db.scalars(select(Publication).where(Publication.campaign_id == row.id)))
    if row.status not in ('published', 'partial', 'failed', 'draft') or any(p.status in ('pending', 'retrying', 'processing') for p in pubs) or not any(p.attempts for p in pubs):
        raise HTTPException(409, 'Wait for current delivery before adding a destination')
    if any(p.account_id == data.account_id for p in pubs):
        raise HTTPException(409, 'This destination is already part of the campaign')
    if len(pubs) >= 20:
        raise HTTPException(422, 'Campaign destination limit reached')
    account = db.scalar(select(SocialAccount).where(SocialAccount.id == data.account_id,
        SocialAccount.org_id == actor.org_id, SocialAccount.active.is_(True)))
    if not account:
        raise HTTPException(404, 'Connected account not found')
    if data.youtube is not None and account.provider!='youtube':
        raise HTTPException(422,'YouTube options must target YouTube')
    if data.tiktok is not None and account.provider!='tiktok':
        raise HTTPException(422,'TikTok options must target TikTok')
    if account.provider == 'x' and external_posts(db, row):
        raise HTTPException(409, 'An X handoff is already recorded; create a separate campaign for another X post')
    existing_providers = {db.get(SocialAccount, p.account_id).provider for p in pubs}
    original = row.overrides.get(account.provider, {}).get('body', row.body)
    if account.provider in existing_providers and data.body != original:
        raise HTTPException(409, 'Another destination uses this provider copy; use the existing copy')
    overrides = dict(row.overrides)
    if account.provider not in existing_providers:
        overrides[account.provider] = {**overrides.get(account.provider, {}), 'body': data.body}
        if data.youtube is not None: overrides[account.provider]['youtube']=data.youtube.model_dump(exclude_none=True)
        if data.tiktok is not None: overrides[account.provider]['tiktok']=data.tiktok.model_dump()
    elif data.youtube is not None and data.youtube.model_dump(exclude_none=True)!=overrides.get(account.provider,{}).get('youtube'):
        raise HTTPException(409,'Other YouTube destinations share these options; use the existing options')
    validated = CampaignCreate(title=row.title, body=row.body, asset_ids=row.asset_ids,
        account_ids=[account.id], overrides={account.provider: overrides.get(account.provider, {})})
    check_refs(db, actor.org_id, validated)
    row.overrides = overrides
    body, media = media_for_campaign(db, row, account, request.app.state.settings)
    provider=get_provider(account.provider, None, request.app.state.settings)
    provider.validate(body, media)
    if account.provider=='youtube': provider.validate_options(row.overrides.get('youtube',{}).get('youtube'),row.title)
    if account.provider=='tiktok': provider.validate_options(row.overrides.get('tiktok',{}).get('tiktok'))
    # Cancelled is deliberately non-dispatchable until the owner chooses delivery.
    db.add(Publication(org_id=actor.org_id, campaign_id=row.id, account_id=account.id, status='cancelled'))
    row.revision += 1
    sync_assets(db, row)
    audit(db, actor, 'campaign.destination_add', {'campaign_id': row.id, 'account_id': account.id})
    db.commit()
    return serialize_campaign(db, row)


class Delivery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    scheduled_at: datetime | None = None


@router.post('/publications/{publication_id}/deliver')
def deliver_destination(publication_id: str, data: Delivery, request: Request,
                        actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    initial = db.scalar(select(Publication).where(Publication.id == publication_id, Publication.org_id == actor.org_id))
    if not initial:
        raise HTTPException(404, 'Publication not found')
    row = get_campaign(db, actor.org_id, initial.campaign_id, lock=True)
    pub = db.scalar(select(Publication).where(Publication.id == publication_id).with_for_update().execution_options(populate_existing=True))
    if row.revision != data.revision or pub.status != 'cancelled' or pub.attempts or pub.provider_id:
        raise HTTPException(409, 'Only an unattempted destination can start delivery; reopen the campaign')
    now = datetime.now(timezone.utc)
    at = data.scheduled_at or now
    if at.tzinfo is None or (data.scheduled_at and at <= now):
        raise HTTPException(422, 'Choose a future time with a timezone, or publish now')
    at = at.astimezone(timezone.utc)
    account = db.get(SocialAccount, pub.account_id)
    if not account.active:
        raise HTTPException(409, 'Reconnect this destination')
    body, media = media_for_campaign(db, row, account, request.app.state.settings)
    provider=get_provider(account.provider, None, request.app.state.settings)
    provider.validate(body, media)
    if account.provider=='youtube': provider.validate_options(row.overrides.get('youtube',{}).get('youtube'),row.title)
    if account.provider=='tiktok': provider.validate_options(row.overrides.get('tiktok',{}).get('tiktok'))
    pub.status, pub.error, pub.next_attempt_at = 'pending', None, at
    # The individual next_attempt_at controls its date. Retain the campaign's
    # original schedule and all existing provider IDs, attempts and checkpoints.
    if not row.scheduled_at or (row.scheduled_at.replace(tzinfo=timezone.utc) if row.scheduled_at.tzinfo is None else row.scheduled_at) > now:
        row.scheduled_at = now
    row.status = 'partial' if any(p.status == 'published' for p in db.scalars(
        select(Publication).where(Publication.campaign_id == row.id))) else 'publishing'
    row.revision += 1
    audit(db, actor, 'publication.deliver', {'publication_id': pub.id, 'scheduled_at': at.isoformat()})
    db.commit()
    return serialize_campaign(db, row)


class DestinationDraft(Destination):
    asset_ids: list[str] = Field(max_length=20)


@router.patch('/publications/{publication_id}/draft')
def edit_destination_draft(publication_id: str, data: DestinationDraft, request: Request,
                           actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    initial = db.scalar(select(Publication).where(Publication.id == publication_id, Publication.org_id == actor.org_id))
    if not initial:
        raise HTTPException(404, 'Publication not found')
    row = get_campaign(db, actor.org_id, initial.campaign_id, lock=True)
    pub = db.scalar(select(Publication).where(Publication.id == publication_id).with_for_update().execution_options(populate_existing=True))
    if row.revision != data.revision or pub.status != 'cancelled' or pub.attempts or pub.provider_id or data.account_id != pub.account_id:
        raise HTTPException(409, 'Only a paused, unattempted destination can be edited; reopen the campaign')
    account = db.get(SocialAccount, pub.account_id)
    if data.youtube is not None and account.provider!='youtube':
        raise HTTPException(422,'YouTube options must target YouTube')
    if data.tiktok is not None and account.provider!='tiktok':
        raise HTTPException(422,'TikTok options must target TikTok')
    if len(data.asset_ids) != len(set(data.asset_ids)):
        raise HTTPException(422, 'Duplicate media is not allowed')
    others = db.scalars(select(Publication).join(SocialAccount, SocialAccount.id == Publication.account_id).where(
        Publication.campaign_id == row.id, Publication.id != pub.id, SocialAccount.provider == account.provider)).all()
    if others:
        raise HTTPException(409, 'Other destinations share this provider copy; create a separate draft for different copy')
    overrides = dict(row.overrides)
    overrides[account.provider] = {**overrides.get(account.provider,{}), 'body': data.body, 'asset_ids': data.asset_ids}
    if data.youtube is not None: overrides[account.provider]['youtube']=data.youtube.model_dump(exclude_none=True)
    if data.tiktok is not None: overrides[account.provider]['tiktok']=data.tiktok.model_dump()
    validated = CampaignCreate(title=row.title, body=row.body, asset_ids=row.asset_ids,
        account_ids=[account.id], overrides={account.provider: overrides[account.provider]})
    check_refs(db, actor.org_id, validated)
    row.overrides = overrides
    body, media = media_for_campaign(db, row, account, request.app.state.settings)
    provider=get_provider(account.provider, None, request.app.state.settings)
    provider.validate(body, media)
    if account.provider=='youtube': provider.validate_options(row.overrides.get('youtube',{}).get('youtube'),row.title)
    if account.provider=='tiktok': provider.validate_options(row.overrides.get('tiktok',{}).get('tiktok'))
    row.revision += 1
    sync_assets(db, row)
    audit(db, actor, 'publication.draft_update', {'publication_id': pub.id, 'revision': row.revision})
    db.commit()
    return serialize_campaign(db, row)


def text_publication(db, actor, publication_id, *, lock=False):
    query = select(Publication).where(Publication.id == publication_id, Publication.org_id == actor.org_id)
    pub = db.scalar(query.with_for_update() if lock else query)
    if not pub:
        raise HTTPException(404, 'Publication not found')
    account = db.get(SocialAccount, pub.account_id)
    if pub.status != 'published' or not pub.provider_id:
        raise HTTPException(409, 'Only published posts can be edited')
    # Facebook feed post identifiers have page_id_post_id form. Reels and
    # other formats use different update contracts and are excluded here.
    if account.provider != 'facebook' or not re.fullmatch(r'[0-9]+_[0-9]+', pub.provider_id):
        raise HTTPException(422, 'Edit this post directly on its platform')
    if not account.active:
        raise HTTPException(409, 'Reconnect this destination')
    return pub, account


def credentials(account, settings):
    try:
        return Vault(settings.encryption_key.get_secret_value()).decrypt(account.credentials)
    except (InvalidToken, ValueError):
        raise HTTPException(409, 'Reconnect this destination') from None


def checksum(body):
    return hashlib.sha256(body.encode()).hexdigest()


class TextPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: str = Field(max_length=20000)


@router.get('/publications/{publication_id}/text')
async def read_text(publication_id: str, request: Request,
                    actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    pub, account = text_publication(db, actor, publication_id)
    settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=30) as http:
        live = await get_provider('facebook', http, settings).request('GET', pub.provider_id,
            credentials(account, settings), params={'fields': 'id,message'})
    outstanding = db.scalars(select(AuditEvent).where(AuditEvent.org_id == actor.org_id,
        AuditEvent.action == 'publication.text_edit')).all()
    needs_verification = any(e.details.get('publication_id') == pub.id and e.details.get('status') in ('pending', 'uncertain') for e in outstanding)
    return {'body': live.get('message', ''), 'provider_id': pub.provider_id, 'needs_verification': needs_verification}


@router.post('/publications/{publication_id}/text/preview')
async def preview_text(publication_id: str, data: TextPreview, request: Request,
                       actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    pub, account = text_publication(db, actor, publication_id)
    live = await read_text(publication_id, request, actor, db)
    claims = {'sub': pub.id, 'org': actor.org_id, 'actor': actor.id, 'provider_id': pub.provider_id,
        'before': checksum(live['body']), 'after': checksum(data.body), 'jti': str(uuid4()),
        'aud': 'post-chief-text-edit', 'exp': datetime.now(timezone.utc) + timedelta(minutes=10)}
    token = jwt.encode(claims, request.app.state.settings.signing_key.get_secret_value(), algorithm='HS256')
    return {'before': live['body'], 'after': data.body, 'review_token': token}


class TextApply(TextPreview):
    review_token: str = Field(max_length=4000)


@router.post('/publications/{publication_id}/text/verify')
async def verify_text(publication_id: str, data: TextPreview, request: Request,
                      actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    pub, account = text_publication(db, actor, publication_id, lock=True)
    live = await read_text(publication_id, request, actor, db)
    if live['body'] != data.body:
        raise HTTPException(409, 'Facebook text changed; load it again before verifying')
    events = db.scalars(select(AuditEvent).where(AuditEvent.org_id == actor.org_id,
        AuditEvent.action == 'publication.text_edit')).all()
    for event in events:
        if event.details.get('publication_id') != pub.id or event.details.get('status') not in ('pending', 'uncertain'):
            continue
        created = event.created_at.replace(tzinfo=timezone.utc) if event.created_at.tzinfo is None else event.created_at
        if event.details['status'] == 'pending' and datetime.now(timezone.utc) - created < timedelta(minutes=10):
            raise HTTPException(409, 'An edit is still in flight; wait before verifying its outcome')
        event.details = {**event.details, 'status': 'verified', 'verified_body': live['body'],
            'verified_at': datetime.now(timezone.utc).isoformat()}
    db.commit()
    return {'status': 'verified', 'body': live['body']}


@router.post('/publications/{publication_id}/text/apply')
async def apply_text(publication_id: str, data: TextApply, request: Request,
                     actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    settings = request.app.state.settings
    try:
        claims = jwt.decode(data.review_token, settings.signing_key.get_secret_value(), algorithms=['HS256'],
            audience='post-chief-text-edit', options={'require': ['sub', 'org', 'actor', 'before', 'after', 'provider_id', 'jti', 'exp']})
    except jwt.PyJWTError:
        raise HTTPException(409, 'Edit review expired; preview again') from None
    if claims['sub'] != publication_id or claims['org'] != actor.org_id or claims['actor'] != actor.id or claims['after'] != checksum(data.body):
        raise HTTPException(409, 'Text changed since review; preview again')
    pub, account = text_publication(db, actor, publication_id, lock=True)
    if claims['provider_id'] != pub.provider_id or db.get(AuditEvent, claims['jti']):
        raise HTTPException(409, 'This reviewed edit was already submitted or the publication changed')
    outstanding = db.scalars(select(AuditEvent).where(AuditEvent.org_id == actor.org_id,
        AuditEvent.action == 'publication.text_edit')).all()
    if any(e.details.get('publication_id') == pub.id and e.details.get('status') in ('pending', 'uncertain') for e in outstanding):
        raise HTTPException(409, 'An earlier edit needs verification; load current Facebook text before retrying')
    provider_id = pub.provider_id
    auth = credentials(account, settings)
    async with httpx.AsyncClient(timeout=30) as http:
        provider = get_provider('facebook', http, settings)
        live = await provider.request('GET', provider_id, auth, params={'fields': 'id,message'})
        before = live.get('message', '')
        if checksum(before) != claims['before']:
            raise HTTPException(409, 'Facebook text changed since review; preview again')
        event = AuditEvent(id=claims['jti'], org_id=actor.org_id, actor_id=actor.id, action='publication.text_edit',
            details={'publication_id': pub.id, 'provider_id': provider_id, 'before': before, 'after': data.body, 'status': 'pending'})
        db.add(event)
        db.commit()  # Persist a single-use intent before the public update.
        try:
            result = await provider.request('POST', provider_id, auth, public_write=True, data={'message': data.body})
            if result.get('success') is not True:
                raise HTTPException(409, 'Facebook did not confirm the edit; load current text to verify it')
        except (ProviderError, HTTPException) as error:
            event.details = {**event.details, 'status': 'uncertain' if isinstance(error, HTTPException) or getattr(error, 'uncertain', False) else 'failed'}
            db.commit()
            raise
        event.details = {**event.details, 'status': 'succeeded'}
        db.commit()
    return {'status': 'updated', 'body': data.body}
