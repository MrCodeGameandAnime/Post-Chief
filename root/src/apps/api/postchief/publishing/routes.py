from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor,current_actor,require_scope,require_owner
from postchief.db import get_db
from postchief.models import Publication,SocialAccount
from postchief.campaigns.schemas import aware
from postchief.campaigns.service import get_campaign,audit,serialize_campaign,serialize_publication
from postchief.providers.registry import get_provider
from postchief.publishing.media import media_for_campaign
from postchief.publishing.engine import campaign_status
from postchief.publishing.engine import state_for
from postchief.vault import Vault
from typing import Literal
from pydantic import Field
from cryptography.fernet import InvalidToken

router=APIRouter(tags=['Scheduling and publication'])
from postchief.publishing.maintenance import router as maintenance_router
router.include_router(maintenance_router)


class Schedule(BaseModel):
    scheduled_at:datetime
    @field_validator('scheduled_at')
    @classmethod
    def offset_required(cls,value): return aware(value)


def start(campaign_id,at,request,actor,db):
    row=get_campaign(db,actor.org_id,campaign_id,lock=True)
    if row.status not in ('draft','scheduled'): raise HTTPException(409,'Campaign is already executing; retry individual failures')
    pubs=list(db.scalars(select(Publication).where(Publication.campaign_id==row.id)))
    for pub in pubs:
        if pub.status in ('processing','published','failed') or (pub.error and pub.error.get('action_required')=='RECONCILE'):
            raise HTTPException(409,'Campaign has attempted publications; retry or reconcile them individually')
        account=db.get(SocialAccount,pub.account_id)
        if not account or not account.active or account.org_id!=actor.org_id: raise HTTPException(409,'Reconnect all campaign destinations')
        body,media=media_for_campaign(db,row,account,request.app.state.settings)
        get_provider(account.provider,None,request.app.state.settings).validate(body,media)
    for pub in pubs: pub.status='pending'; pub.next_attempt_at=None
    row.scheduled_at=at; row.status='scheduled'; row.revision+=1
    audit(db,actor,'campaign.schedule',{'campaign_id':row.id,'scheduled_at':at.isoformat()})
    db.commit(); return serialize_campaign(db,row)


@router.post('/campaigns/{campaign_id}/schedule')
def schedule(campaign_id:str,data:Schedule,request:Request,actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'campaigns:schedule')
    if data.scheduled_at<=datetime.now(timezone.utc): raise HTTPException(422,'Schedule must be in the future; use publish for immediate delivery')
    return start(campaign_id,data.scheduled_at,request,actor,db)


@router.post('/campaigns/{campaign_id}/publish')
def publish(campaign_id:str,request:Request,actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'campaigns:publish')
    return start(campaign_id,datetime.now(timezone.utc),request,actor,db)


@router.post('/campaigns/{campaign_id}/cancel')
def cancel(campaign_id:str,actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'campaigns:write')
    row=get_campaign(db,actor.org_id,campaign_id,lock=True)
    pubs=list(db.scalars(select(Publication).where(Publication.campaign_id==row.id)))
    if any(p.status=='processing' for p in pubs): raise HTTPException(409,'Publication is in flight; wait for its outcome before cancelling')
    for pub in pubs:
        if pub.status in ('pending','retrying'): pub.status='cancelled'; pub.next_attempt_at=None
    db.flush(); campaign_status(db,row); row.revision+=1
    audit(db,actor,'campaign.cancel',{'campaign_id':row.id}); db.commit()
    return serialize_campaign(db,row)


@router.post('/publications/{publication_id}/retry')
def retry(publication_id:str,actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'campaigns:publish')
    initial=db.scalar(select(Publication).where(Publication.id==publication_id,Publication.org_id==actor.org_id))
    if not initial: raise HTTPException(404,'Publication not found')
    row=get_campaign(db,actor.org_id,initial.campaign_id,lock=True)
    pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update().execution_options(populate_existing=True))
    if pub.status!='failed': raise HTTPException(409,'Only failed publications can be retried')
    if pub.error and pub.error.get('action_required')=='RECONCILE': raise HTTPException(409,'Resolve the uncertain provider result before retrying')
    account=db.get(SocialAccount,pub.account_id)
    if not account.active: raise HTTPException(409,'Reconnect this destination before retrying')
    pub.status='retrying'; pub.next_attempt_at=datetime.now(timezone.utc); pub.error=None
    row.status='publishing'; row.scheduled_at=datetime.now(timezone.utc)
    audit(db,actor,'publication.retry',{'publication_id':pub.id}); db.commit()
    return serialize_publication(db,pub)


class Reconcile(BaseModel):
    resolution:Literal['published','not_published']
    provider_id:str|None=Field(default=None,min_length=1,max_length=500)


@router.post('/publications/{publication_id}/reconcile')
def reconcile(publication_id:str,data:Reconcile,request:Request,actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    initial=db.scalar(select(Publication).where(Publication.id==publication_id,Publication.org_id==actor.org_id))
    if not initial: raise HTTPException(404,'Publication not found')
    campaign=get_campaign(db,actor.org_id,initial.campaign_id,lock=True)
    pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update().execution_options(populate_existing=True))
    if pub.status!='failed' or not pub.error or pub.error.get('action_required')!='RECONCILE':
        raise HTTPException(409,'Only uncertain failed publications need reconciliation')
    vault=Vault(request.app.state.settings.encryption_key.get_secret_value())
    try: state=state_for(pub,vault)
    except (InvalidToken,ValueError,TypeError): state={}
    if data.resolution=='published':
        if not data.provider_id: raise HTTPException(422,'Enter the existing provider publication ID')
        pub.status='published'; pub.provider_id=data.provider_id; pub.published_at=datetime.now(timezone.utc); pub.error=None
        state['provider_id']=data.provider_id; state['phase']='published'
    else:
        if data.provider_id: raise HTTPException(422,'A not-published result cannot contain a publication ID')
        pub.error={'reason':'PROVIDER_ERROR','retryable':False,'action_required':'REVIEW','message':'Owner confirmed no public post exists; an explicit retry is available'}
        state['_retry_count']=0; state['_execution_started']=datetime.now(timezone.utc).isoformat()
    pub.provider_state={'sealed':vault.encrypt(state)}
    db.flush(); campaign_status(db,campaign)
    audit(db,actor,'publication.reconcile',{'publication_id':pub.id,'resolution':data.resolution,'provider_id':data.provider_id})
    db.commit(); return serialize_publication(db,pub)
