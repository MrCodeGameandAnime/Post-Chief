import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor, require_owner
from postchief.db import get_db
from postchief.models import SocialAccount
from postchief.vault import Vault
from postchief.providers.linkedin import LinkedInProvider
from postchief.providers.routes import save_connection

router=APIRouter(tags=['LinkedIn organizations'])


async def get_linkedin_provider(request:Request):
    async with httpx.AsyncClient(timeout=120) as http:
        yield LinkedInProvider(http,request.app.state.settings)


def member(db,actor,account_id):
    row=db.scalar(select(SocialAccount).where(SocialAccount.id==account_id,SocialAccount.org_id==actor.org_id,
        SocialAccount.provider=='linkedin',SocialAccount.active.is_(True)))
    if not row or not row.remote_id.startswith('urn:li:person:'): raise HTTPException(404,'Connected LinkedIn member not found')
    return row


@router.get('/connections/linkedin/{account_id}/organizations')
async def organizations(account_id:str,request:Request,actor:Actor=Depends(require_owner),db:Session=Depends(get_db),provider=Depends(get_linkedin_provider)):
    row=member(db,actor,account_id)
    credentials=Vault(request.app.state.settings.encryption_key.get_secret_value()).decrypt(row.credentials)
    return await provider.organizations(credentials)


class OrganizationSelection(BaseModel):
    organization:str=Field(pattern=r'^urn:li:organization:[0-9]+$',max_length=100)


@router.post('/connections/linkedin/{account_id}/organizations',status_code=201)
async def connect_organization(account_id:str,data:OrganizationSelection,request:Request,actor:Actor=Depends(require_owner),
                               db:Session=Depends(get_db),provider=Depends(get_linkedin_provider)):
    row=member(db,actor,account_id)
    credentials=Vault(request.app.state.settings.encryption_key.get_secret_value()).decrypt(row.credentials)
    available=await provider.organizations(credentials)
    selected=next((item for item in available if item['id']==data.organization),None)
    if not selected: raise HTTPException(403,'Member has no approved publishing role for this organization')
    credentials['author']=selected['id']
    credentials['member_author']=row.remote_id
    return save_connection(db,request.app.state.settings,actor,'linkedin',selected['id'],selected['name'],credentials,row.expires_at)
