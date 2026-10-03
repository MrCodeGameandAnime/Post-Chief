from datetime import datetime,timezone
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor,require_owner
from postchief.db import get_db
from postchief.models import SocialAccount,Publication
from postchief.providers.tiktok import TikTokProvider
from postchief.providers.x_credentials import current_x_credentials
from postchief.campaigns.service import get_campaign,audit,serialize_publication
from postchief.publishing.engine import state_for
from postchief.vault import Vault

router=APIRouter(tags=['TikTok inbox handoff'])


@router.get('/connections/tiktok/{account_id}/creator')
async def creator(account_id:str,request:Request,actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    account=db.scalar(select(SocialAccount).where(SocialAccount.id==account_id,SocialAccount.org_id==actor.org_id,
        SocialAccount.provider=='tiktok',SocialAccount.active.is_(True)))
    if not account: raise HTTPException(404,'Connected TikTok creator not found')
    async with httpx.AsyncClient(timeout=30) as http:
        adapter=TikTokProvider(http,request.app.state.settings)
        credentials=await current_x_credentials(request.app.state.sessions,request.app.state.settings,account.id,actor.org_id,adapter,provider_name='tiktok')
        return await adapter.profile(credentials)


@router.post('/publications/{publication_id}/tiktok/status')
def refresh_status(publication_id:str,request:Request,actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    initial=db.scalar(select(Publication).where(Publication.id==publication_id,Publication.org_id==actor.org_id))
    if not initial: raise HTTPException(404,'Publication not found')
    campaign=get_campaign(db,actor.org_id,initial.campaign_id,lock=True)
    pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update())
    account=db.get(SocialAccount,pub.account_id)
    if account.provider!='tiktok' or pub.status!='awaiting_owner': raise HTTPException(409,'Only TikTok handoffs awaiting native completion can refresh this status')
    vault=Vault(request.app.state.settings.encryption_key.get_secret_value())
    state=state_for(pub,vault)
    if not state.get('publish_id'): raise HTTPException(409,'Stored TikTok task is missing; reconcile the handoff')
    now=datetime.now(timezone.utc)
    state['_execution_started']=now.isoformat()
    pub.provider_state={'sealed':vault.encrypt(state)}
    pub.status='retrying'; pub.next_attempt_at=now
    campaign.status='publishing'; campaign.scheduled_at=campaign.scheduled_at or now
    audit(db,actor,'publication.status_refresh',{'publication_id':pub.id,'provider':'tiktok'})
    db.commit(); return serialize_publication(db,pub)
