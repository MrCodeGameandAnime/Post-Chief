from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.models import Publication,Campaign,AnalyticsSnapshot,SocialAccount
from postchief.auth import Actor,current_actor,require_scope
from postchief.db import get_db
from postchief.campaigns.service import iso,audit

router=APIRouter(prefix='/analytics',tags=['Analytics'])


def publication(db,actor,id,lock=False):
    query=select(Publication).where(Publication.id==id,Publication.org_id==actor.org_id)
    if lock: query=query.with_for_update()
    pub=db.scalar(query)
    if not pub: raise HTTPException(404,'Publication not found')
    return pub


def snapshot(row):
    return {'id':row.id,'collected_at':iso(row.created_at),'metrics':row.metrics} if row else None


@router.get('')
def latest(limit:int=Query(default=100,ge=1,le=200),offset:int=Query(default=0,ge=0),actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'analytics:read')
    pubs=db.scalars(select(Publication).where(Publication.org_id==actor.org_id,Publication.status=='published')
        .order_by(Publication.created_at.desc()).limit(limit).offset(offset))
    result=[]
    for pub in pubs:
        row=db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.org_id==actor.org_id,AnalyticsSnapshot.publication_id==pub.id)
            .order_by(AnalyticsSnapshot.created_at.desc()).limit(1))
        account=db.get(SocialAccount,pub.account_id);campaign=db.get(Campaign,pub.campaign_id)
        result.append({'publication_id':pub.id,'campaign_id':campaign.id,'title':campaign.title,'provider':account.provider,'account_name':account.name,
            'latest':snapshot(row),'error':pub.analytics_error,'next_collection_at':iso(pub.analytics_next_at)})
    return result


@router.get('/publications/{publication_id}')
def history(publication_id:str,limit:int=Query(default=100,ge=1,le=500),actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'analytics:read');publication(db,actor,publication_id)
    return [snapshot(row) for row in db.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.org_id==actor.org_id,AnalyticsSnapshot.publication_id==publication_id)
        .order_by(AnalyticsSnapshot.created_at.desc()).limit(limit))]


@router.post('/publications/{publication_id}/refresh',status_code=202)
def refresh(publication_id:str,actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'analytics:collect');pub=publication(db,actor,publication_id,lock=True)
    if pub.status!='published': raise HTTPException(409,'Only published posts have analytics')
    pub.analytics_next_at=datetime.now(timezone.utc)
    audit(db,actor,'analytics.refresh_requested',{'publication_id':pub.id});db.commit()
    return {'status':'queued','publication_id':pub.id}
