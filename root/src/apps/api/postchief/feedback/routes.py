from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy.orm import Session
from postchief.auth import Actor,current_actor,require_scope
from postchief.db import get_db
from postchief.github.routes import connection,get_service
from postchief.campaigns.service import get_campaign,audit
from postchief.feedback.service import preview

router=APIRouter(prefix='/feedback',tags=['GitHub feedback'])
class FeedbackSync(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    base_commit:str=Field(pattern=r'^[a-f0-9]{40}$')


def read_scopes(actor):
    for scope in ('campaigns:read','analytics:read','github:read'):require_scope(actor,scope)


@router.get('/campaigns/{campaign_id}/preview')
async def preview_route(campaign_id:str,actor:Actor=Depends(current_actor),db:Session=Depends(get_db),service=Depends(get_service)):
    read_scopes(actor)
    campaign=get_campaign(db,actor.org_id,campaign_id)
    result,_,_=await preview(db,campaign,connection(db,actor),service)
    return result


@router.post('/campaigns/{campaign_id}/sync')
async def sync(campaign_id:str,data:FeedbackSync,actor:Actor=Depends(current_actor),db:Session=Depends(get_db),service=Depends(get_service)):
    require_scope(actor,'github:write');read_scopes(actor)
    campaign=get_campaign(db,actor.org_id,campaign_id,lock=True)
    if campaign.revision!=data.revision:raise HTTPException(409,'Campaign changed; review a new feedback preview')
    row=connection(db,actor);result,snapshot,files=await preview(db,campaign,row,service)
    if result['digest']!=data.digest:raise HTTPException(409,'Feedback or workspace text changed; review a new preview')
    if result['unchanged']:
        applied={'sha':snapshot['head'],'changed':False}
    else:
        if snapshot['head']!=data.base_commit:raise HTTPException(409,'Workspace branch changed; review a new preview')
        applied=await service.commit_feedback(row.installation_id,row.workspace,snapshot,files,f'Post Chief feedback {campaign.id} {data.digest[:12]}')
    audit(db,actor,'feedback.synced',{'campaign_id':campaign.id,'workspace':row.workspace,'commit':applied['sha'],'changed':applied['changed']})
    db.commit();return applied
