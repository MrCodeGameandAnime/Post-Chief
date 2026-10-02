import secrets
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,Field,ConfigDict,ValidationError
from sqlalchemy import select,update
from sqlalchemy.orm import Session
from postchief.auth import Actor,current_actor,require_owner,require_scope,digest
from postchief.db import get_db
from postchief.models import AgentKey,AgentRun,Approval,AuditEvent,Organization,Campaign,Publication
from postchief.campaigns.service import audit,iso,get_campaign
from postchief.campaigns.schemas import CampaignCreate,CampaignPatch
from postchief.campaigns import routes as campaigns
from postchief.publishing import routes as publishing
from postchief.github import routes as github
from postchief.analytics import routes as analytics
from postchief.github.routes import get_service
from postchief.publishing.engine import utc
from postchief.agents.policy import DEFAULTS,SCOPES,mode_for

router=APIRouter(tags=['Agent controls and autonomy'])

class KeyCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=200)
    scopes:list[str]=Field(min_length=1,max_length=20)


@router.post('/agent/keys',status_code=201)
def create_key(data:KeyCreate,actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    if set(data.scopes)-SCOPES or len(set(data.scopes))!=len(data.scopes): raise HTTPException(422,'Choose unique supported scopes; wildcard and owner permissions are unavailable')
    token='pc_agent_'+secrets.token_urlsafe(48)
    row=AgentKey(org_id=actor.org_id,name=data.name,scopes=data.scopes,token_hash=digest(token));db.add(row);db.flush()
    audit(db,actor,'agent.key_created',{'key_id':row.id,'scopes':data.scopes});db.commit()
    return {'id':row.id,'name':row.name,'scopes':row.scopes,'token':token,'active':True}


@router.get('/agent/keys')
def keys(actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    return [{'id':r.id,'name':r.name,'scopes':r.scopes,'active':r.active,'created_at':iso(r.created_at)} for r in
        db.scalars(select(AgentKey).where(AgentKey.org_id==actor.org_id).order_by(AgentKey.created_at.desc()))]


@router.delete('/agent/keys/{key_id}')
def revoke(key_id:str,actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    row=db.scalar(select(AgentKey).where(AgentKey.id==key_id,AgentKey.org_id==actor.org_id))
    if not row: raise HTTPException(404,'Agent key not found')
    row.active=False;audit(db,actor,'agent.key_revoked',{'key_id':row.id});db.commit();return {'id':row.id,'active':False}


@router.get('/settings/autonomy')
def autonomy(actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'campaigns:read')
    return {'policies':{**DEFAULTS,**db.get(Organization,actor.org_id).autonomy},'scopes':sorted(SCOPES)}


class PolicyUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    policies:dict[str,Literal['AUTO','APPROVAL','DISABLED']]


@router.put('/settings/autonomy')
def set_autonomy(data:PolicyUpdate,actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    if set(data.policies)-DEFAULTS.keys(): raise HTTPException(422,'Unknown autonomy permission')
    org=db.scalar(select(Organization).where(Organization.id==actor.org_id).with_for_update())
    org.autonomy={**org.autonomy,**data.policies};audit(db,actor,'autonomy.updated',{'policies':data.policies});db.commit()
    return {'policies':{**DEFAULTS,**org.autonomy}}


@router.get('/audit')
def events(limit:int=Query(default=100,ge=1,le=500),actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'audit:read')
    return [{'id':r.id,'actor_id':r.actor_id,'action':r.action,'details':r.details,'created_at':iso(r.created_at)} for r in
        db.scalars(select(AuditEvent).where(AuditEvent.org_id==actor.org_id).order_by(AuditEvent.created_at.desc()).limit(limit))]


class RunReport(BaseModel):
    model_config=ConfigDict(extra='forbid')
    summary:str=Field(min_length=1,max_length=5000)
    status:Literal['running','completed','failed']='completed'


@router.post('/agent/runs',status_code=201)
def report(data:RunReport,actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'agent:report')
    if actor.kind!='agent':raise HTTPException(403,'Agent key required for a run report')
    row=AgentRun(org_id=actor.org_id,key_id=actor.id,summary=data.summary,status=data.status);db.add(row);db.flush()
    audit(db,actor,'agent.run_reported',{'run_id':row.id,'status':row.status});db.commit();return {'id':row.id,'status':row.status}


@router.get('/agent/runs')
def runs(actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'audit:read')
    return [{'id':r.id,'key_id':r.key_id,'summary':r.summary,'status':r.status,'created_at':iso(r.created_at)} for r in
        db.scalars(select(AgentRun).where(AgentRun.org_id==actor.org_id).order_by(AgentRun.created_at.desc()).limit(100))]


def approval_output(r):
    status='expired' if r.status in ('pending','approved') and utc(r.expires_at)<=datetime.now(timezone.utc) else r.status
    return {'id':r.id,'actor_id':r.actor_id,'action':r.action,'payload':r.payload,'status':status,'expires_at':iso(r.expires_at)}


@router.get('/approvals')
def approvals(actor:Actor=Depends(current_actor),db:Session=Depends(get_db)):
    require_scope(actor,'audit:read')
    query=select(Approval).where(Approval.org_id==actor.org_id)
    if actor.kind=='agent':query=query.where(Approval.actor_id==actor.id)
    return [approval_output(row) for row in db.scalars(query.order_by(Approval.created_at.desc()).limit(100))]


@router.post('/approvals/{approval_id}/{decision}')
def decide(approval_id:str,decision:Literal['approve','reject'],actor:Actor=Depends(require_owner),db:Session=Depends(get_db)):
    row=db.scalar(select(Approval).where(Approval.id==approval_id,Approval.org_id==actor.org_id).with_for_update())
    if not row:raise HTTPException(404,'Approval not found')
    if row.status!='pending' or utc(row.expires_at)<=datetime.now(timezone.utc):raise HTTPException(409,'Approval is no longer pending or has expired')
    row.status='approved' if decision=='approve' else 'rejected'
    audit(db,actor,'approval.'+row.status,{'approval_id':row.id});db.commit();return approval_output(row)


ACTIONS={'campaign.create':'campaigns:write','campaign.update':'campaigns:write','campaign.schedule':'campaigns:schedule',
    'campaign.publish':'campaigns:publish','campaign.cancel':'campaigns:write','publication.retry':'campaigns:publish',
    'github.write':'github:write','analytics.refresh':'analytics:collect'}
class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['campaign.create','campaign.update','campaign.schedule','campaign.publish','campaign.cancel','publication.retry','github.write','analytics.refresh']
    target_id:str|None=Field(default=None,max_length=36)
    data:dict=Field(default_factory=dict)
    approval_id:str|None=Field(default=None,max_length=36)


@router.post('/agent/actions')
async def execute(data:Action,request:Request,actor:Actor=Depends(current_actor),db:Session=Depends(get_db),service=Depends(get_service)):
    if actor.kind!='agent':raise HTTPException(403,'Agent key required for managed actions')
    scoped=replace(actor,managed_action=True);scope=ACTIONS[data.action];require_scope(scoped,scope)
    schema={'campaign.create':CampaignCreate,'campaign.update':CampaignPatch,'campaign.schedule':publishing.Schedule,'github.write':github.ContentWrite}.get(data.action)
    try:
        parsed=schema.model_validate(data.data) if schema else None
    except ValidationError:raise HTTPException(422,'Invalid action payload') from None
    if not schema and data.data:raise HTTPException(422,'This action does not accept payload fields')
    context={}
    if data.action.startswith('campaign.') and data.action!='campaign.create':
        if not data.target_id:raise HTTPException(422,'Campaign target_id required')
        campaign=get_campaign(db,actor.org_id,data.target_id,lock=True);context={'revision':campaign.revision}
    elif data.action in ('publication.retry','analytics.refresh'):
        if not data.target_id:raise HTTPException(422,'Publication target_id required')
        pub=db.scalar(select(Publication).where(Publication.id==data.target_id,Publication.org_id==actor.org_id))
        if not pub:raise HTTPException(404,'Publication not found')
        campaign=get_campaign(db,actor.org_id,pub.campaign_id,lock=True)
        pub=db.scalar(select(Publication).where(Publication.id==pub.id).with_for_update().execution_options(populate_existing=True))
        context={'revision':campaign.revision,'publication_status':pub.status,'attempts':pub.attempts}
    elif data.target_id:raise HTTPException(422,'This action does not accept target_id')
    payload={'target_id':data.target_id,'data':parsed.model_dump(mode='json',exclude_none=True) if parsed else {},'context':context}
    if data.approval_id or mode_for(db,actor.org_id,scope)=='APPROVAL':
        if not data.approval_id:
            row=Approval(org_id=actor.org_id,actor_id=actor.id,action=data.action,payload=payload,expires_at=datetime.now(timezone.utc)+timedelta(minutes=15));db.add(row);db.flush()
            audit(db,actor,'approval.requested',{'approval_id':row.id,'action':data.action});db.commit()
            raise HTTPException(428,{'approval_id':row.id,'message':'Owner approval required','action':data.action,'payload':payload})
        row=db.scalar(select(Approval).where(Approval.id==data.approval_id,Approval.org_id==actor.org_id,Approval.actor_id==actor.id).with_for_update())
        if not row or row.status!='approved' or utc(row.expires_at)<=datetime.now(timezone.utc) or row.action!=data.action or row.payload!=payload:
            raise HTTPException(409,'Approval is expired, consumed, unapproved or does not match the exact action and current campaign')
        consumed=db.execute(update(Approval).where(Approval.id==row.id,Approval.status=='approved').values(status='consumed'))
        if consumed.rowcount!=1:raise HTTPException(409,'Approval was already consumed')
        # Refresh after taking SQLite's write lock too: another approved action may
        # have changed this campaign while this request was waiting to consume.
        if context:
            db.refresh(campaign)
            if campaign.revision!=context['revision']:raise HTTPException(409,'Campaign changed while consuming approval')
            if 'publication_status' in context:
                db.refresh(pub)
                if pub.status!=context['publication_status'] or pub.attempts!=context['attempts']:
                    raise HTTPException(409,'Publication changed while consuming approval')
        audit(db,actor,'approval.consumed',{'approval_id':row.id})
    audit(db,actor,'agent.action_started',{'action':data.action,'target_id':data.target_id})
    if data.action=='campaign.create':return campaigns.create(parsed,scoped,db)
    if data.action=='campaign.update':return campaigns.update(data.target_id,parsed,scoped,db)
    if data.action=='campaign.schedule':return publishing.schedule(data.target_id,parsed,request,scoped,db)
    if data.action=='campaign.publish':return publishing.publish(data.target_id,request,scoped,db)
    if data.action=='campaign.cancel':return publishing.cancel(data.target_id,scoped,db)
    if data.action=='publication.retry':return publishing.retry(data.target_id,scoped,db)
    if data.action=='analytics.refresh':return analytics.refresh(data.target_id,scoped,db)
    return await github.write_content(parsed,scoped,db,service)
