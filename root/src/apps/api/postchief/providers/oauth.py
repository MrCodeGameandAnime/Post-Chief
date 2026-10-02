import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session
from postchief.auth import Actor, digest, require_owner
from postchief.db import get_db
from postchief.models import OAuthState
from postchief.providers.routes import save_connection
from provider_contracts import ProviderError, ErrorReason

router=APIRouter(tags=['OAuth connections'])


class OAuthService:
    def __init__(self, client, settings): self.http,self.settings=client,settings

    def redirect_uri(self, provider): return self.settings.public_url.rstrip('/')+f'/api/connections/oauth/{provider}/callback'

    def authorization_url(self,provider,state):
        s=self.settings
        if provider=='meta':
            app,secret=s.meta_client_id,s.meta_client_secret.get_secret_value()
            endpoint=f'https://www.facebook.com/{s.meta_api_version}/dialog/oauth'
            scope='business_management,pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish,instagram_manage_insights'
        elif provider=='threads':
            app,secret=s.threads_client_id,s.threads_client_secret.get_secret_value()
            endpoint='https://threads.net/oauth/authorize'
            scope='threads_basic,threads_content_publish,threads_manage_insights'
        elif provider=='linkedin':
            app,secret=s.linkedin_client_id,s.linkedin_client_secret.get_secret_value()
            endpoint='https://www.linkedin.com/oauth/v2/authorization'
            scope=s.linkedin_scopes
        else: raise HTTPException(404,'OAuth provider not found')
        if not app or not secret: raise HTTPException(503,'Configure this provider app before connecting')
        return endpoint+'?'+urlencode({'client_id':app,'redirect_uri':self.redirect_uri(provider),'scope':scope,'response_type':'code','state':state})

    async def request(self,method,url,**kwargs):
        try:
            response=await self.http.request(method,url,**kwargs)
            value=response.json()
        except (httpx.HTTPError,ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR,'Authorization exchange failed; start connection again')
        if response.is_error or value.get('error'):
            raise ProviderError(ErrorReason.AUTH_REVOKED,'Authorization failed; check app configuration and consent')
        return value

    async def exchange(self,provider,code):
        s=self.settings
        if provider=='linkedin':
            value=await self.request('POST','https://www.linkedin.com/oauth/v2/accessToken',data={
                'client_id':s.linkedin_client_id,'client_secret':s.linkedin_client_secret.get_secret_value(),
                'grant_type':'authorization_code','redirect_uri':self.redirect_uri(provider),'code':code})
            profile=await self.request('GET','https://api.linkedin.com/v2/userinfo',headers={'Authorization':'Bearer '+value['access_token']})
            author='urn:li:person:'+profile['sub']
            expires=datetime.now(timezone.utc)+timedelta(seconds=int(value['expires_in']))
            credentials={'author':author,'access_token':value['access_token'],'scopes':value.get('scope',s.linkedin_scopes).split(),
                'expires_at':expires.isoformat()}
            for field in ('refresh_token','refresh_token_expires_in'):
                if value.get(field): credentials[field]=value[field]
            return [{'provider':'linkedin','id':author,'name':profile.get('name','LinkedIn member'),'credentials':credentials,'expires_at':expires}]
        if provider=='threads':
            short=await self.request('POST','https://graph.threads.net/oauth/access_token',data={
                'client_id':s.threads_client_id,'client_secret':s.threads_client_secret.get_secret_value(),
                'grant_type':'authorization_code','redirect_uri':self.redirect_uri(provider),'code':code})
            long=await self.request('GET','https://graph.threads.net/access_token',
                params={'grant_type':'th_exchange_token','client_secret':s.threads_client_secret.get_secret_value()},
                headers={'Authorization':'Bearer '+short['access_token']})
            profile=await self.request('GET','https://graph.threads.net/v1.0/me',params={'fields':'id,username'},headers={'Authorization':'Bearer '+long['access_token']})
            expires=datetime.now(timezone.utc)+timedelta(seconds=int(long['expires_in']))
            return [{'provider':'threads','id':profile['id'],'name':profile['username'],
                'credentials':{'id':profile['id'],'access_token':long['access_token'],'expires_at':expires.isoformat()},'expires_at':expires}]
        base=f'https://graph.facebook.com/{s.meta_api_version}'
        short=await self.request('GET',base+'/oauth/access_token',params={'client_id':s.meta_client_id,
            'client_secret':s.meta_client_secret.get_secret_value(),'redirect_uri':self.redirect_uri(provider),'code':code})
        long=await self.request('GET',base+'/oauth/access_token',params={'client_id':s.meta_client_id,
            'client_secret':s.meta_client_secret.get_secret_value(),'grant_type':'fb_exchange_token','fb_exchange_token':short['access_token']})
        params={'fields':'id,name,access_token,tasks,instagram_business_account{id,username}','limit':100}
        result=[]
        # Follow cursors against a fixed origin; never follow URLs carrying tokens.
        for _ in range(100):
            pages=await self.request('GET',base+'/me/accounts',params=params,headers={'Authorization':'Bearer '+long['access_token']})
            for page in pages.get('data',[]):
                if not page.get('access_token'): continue
                result.append({'provider':'facebook','id':page['id'],'name':page['name'],
                    'credentials':{'id':page['id'],'access_token':page['access_token'],'tasks':page.get('tasks',[])}})
                ig=page.get('instagram_business_account')
                if ig:
                    result.append({'provider':'instagram','id':ig['id'],'name':ig.get('username',page['name']),
                        'credentials':{'id':ig['id'],'page_id':page['id'],'access_token':page['access_token']}})
            paging=pages.get('paging',{})
            after=paging.get('cursors',{}).get('after')
            if not paging.get('next') or not after: break
            params['after']=after
        if not result: raise ProviderError(ErrorReason.PERMISSION_MISSING,'No authorized Facebook Pages or linked professional Instagram accounts found')
        return result


async def get_oauth_service(request:Request):
    async with httpx.AsyncClient(timeout=30) as client:
        yield OAuthService(client,request.app.state.settings)


@router.post('/connections/oauth/{provider}/authorize')
def authorize(provider:str,actor:Actor=Depends(require_owner),db:Session=Depends(get_db),service=Depends(get_oauth_service)):
    state=secrets.token_urlsafe(32)
    url=service.authorization_url(provider,state)
    db.add(OAuthState(org_id=actor.org_id,actor_id=actor.id,provider=provider,token_hash=digest(state),expires_at=datetime.now(timezone.utc)+timedelta(minutes=10)))
    db.commit()
    return {'url':url}


@router.get('/connections/oauth/{provider}/callback')
async def callback(provider:str,request:Request,state:str|None=None,code:str|None=None,error:str|None=None,error_code:str|None=None,
                   actor:Actor=Depends(require_owner),db:Session=Depends(get_db),service=Depends(get_oauth_service)):
    rejected=bool(error or error_code)
    rejection='Authorization failed; check the provider app permissions and OAuth settings, then start again from Post Chief Connections'
    # Meta can reject app configuration before returning state. Report only a
    # fixed diagnostic: untrusted provider messages never become displayed text.
    # Missing state can never authorize an exchange or mutate a connection.
    if not state:
        raise HTTPException(400,rejection if rejected else 'OAuth state is missing; start connection again from Post Chief Connections')
    consumed=db.execute(update(OAuthState).where(OAuthState.token_hash==digest(state),OAuthState.provider==provider,
        OAuthState.actor_id==actor.id,OAuthState.org_id==actor.org_id,OAuthState.consumed==False,
        OAuthState.expires_at>datetime.now(timezone.utc)).values(consumed=True))
    db.commit()
    if consumed.rowcount!=1: raise HTTPException(400,'OAuth state is invalid, expired or already used')
    if rejected or not code: raise HTTPException(400,rejection)
    accounts=await service.exchange(provider,code)
    connected=[]
    for account in accounts:
        connected.append(save_connection(db,request.app.state.settings,actor,account['provider'],account['id'],account['name'],account['credentials'],account.get('expires_at')))
    return {'connections':connected,'message':'Connected. Return to Post Chief.'}
