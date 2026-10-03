import secrets
import base64
import hashlib
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
from postchief.providers.x import SCOPES, BASE, pkce_verifier, token_auth, token_credentials, numeric_id
from postchief.providers.pinterest import PinterestProvider, SCOPES as PINTEREST_SCOPES, BASE as PINTEREST_BASE, token_credentials as pinterest_tokens, numeric_id as pinterest_id
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.google import TOKEN_URL, token_credentials as google_tokens
from postchief.providers.gbp import GoogleBusinessProvider, SCOPES as GBP_SCOPES
from postchief.providers.youtube import YouTubeProvider, SCOPES as YOUTUBE_SCOPES, BASE as YOUTUBE_BASE, channel_id
from postchief.providers.tiktok import TikTokProvider, SCOPES as TIKTOK_SCOPES, tokens as tiktok_tokens
from postchief.providers.tiktok_business import TikTokBusinessProvider, authorization_url as business_authorization, token_credentials as business_tokens

router=APIRouter(tags=['OAuth connections'])


class OAuthService:
    def __init__(self, client, settings): self.http,self.settings=client,settings

    def redirect_uri(self, provider):
        return self.settings.public_url.rstrip('/')+f'/api/connections/oauth/{provider}/callback'+('/' if provider=='tiktok_business' else '')

    def authorization_url(self,provider,state):
        s=self.settings
        if provider=='tiktok_business':
            return business_authorization(s, self.redirect_uri(provider), state)
        if provider=='tiktok':
            if not s.tiktok_client_key or not s.tiktok_client_secret.get_secret_value():
                raise HTTPException(503,'Configure the TikTok Login Kit client key and secret before connecting')
            return 'https://www.tiktok.com/v2/auth/authorize/?'+urlencode({'client_key':s.tiktok_client_key,
                'redirect_uri':self.redirect_uri(provider),'scope':TIKTOK_SCOPES,'response_type':'code','state':state,'disable_auto_auth':1})
        if provider in ('youtube','gbp'):
            if not s.google_client_id or not s.google_client_secret.get_secret_value():
                raise HTTPException(503,'Configure the Google OAuth web client ID and secret before connecting')
            return 'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':s.google_client_id,
                'redirect_uri':self.redirect_uri(provider),'scope':GBP_SCOPES if provider=='gbp' else YOUTUBE_SCOPES,'response_type':'code','state':state,
                'access_type':'offline','prompt':'consent'})
        if provider=='x':
            if not s.x_client_id or not s.x_client_secret.get_secret_value():
                raise HTTPException(503,'Configure the X OAuth 2.0 client ID and secret before connecting')
            challenge=base64.urlsafe_b64encode(hashlib.sha256(pkce_verifier(s,state).encode()).digest()).rstrip(b'=').decode()
            return 'https://x.com/i/oauth2/authorize?'+urlencode({'client_id':s.x_client_id,
                'redirect_uri':self.redirect_uri(provider),'scope':SCOPES,'response_type':'code','state':state,
                'code_challenge':challenge,'code_challenge_method':'S256'})
        if provider=='pinterest':
            app,secret=s.pinterest_client_id,s.pinterest_client_secret.get_secret_value()
            endpoint='https://www.pinterest.com/oauth/'
            scope=PINTEREST_SCOPES
        elif provider=='meta':
            app,secret=s.meta_client_id,s.meta_client_secret.get_secret_value()
            endpoint=f'https://www.facebook.com/{s.meta_api_version}/dialog/oauth'
            scope='business_management,pages_show_list,pages_read_engagement,pages_read_user_content,pages_manage_posts,instagram_basic,instagram_content_publish,instagram_manage_insights'
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

    async def request(self,method,url,*,failure_stage=None,**kwargs):
        try:
            response=await self.http.request(method,url,**kwargs)
            value=response.json()
        except (httpx.HTTPError,ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR,'Authorization exchange failed; start connection again')
        if response.is_error or value.get('error'):
            message='Authorization failed; check app configuration and consent'
            if failure_stage:
                message=failure_stage+'; start a fresh connection from Post Chief Connections'
                # Only numeric provider codes are safe to surface. Provider text,
                # request URLs and response bodies can contain credentials.
                error=value.get('error')
                if isinstance(error,dict):
                    codes=[f'{name}={error[name]}' for name in ('code','error_subcode')
                           if type(error.get(name)) is int and 0<=error[name]<=2147483647]
                    if codes: message+=' ('+', '.join(codes)+')'
            raise ProviderError(ErrorReason.AUTH_REVOKED,message)
        return value

    async def exchange(self,provider,code,state=None):
        s=self.settings
        if provider=='gbp':
            value=await self.request('POST',TOKEN_URL,data={'client_id':s.google_client_id,
                'client_secret':s.google_client_secret.get_secret_value(),'grant_type':'authorization_code',
                'redirect_uri':self.redirect_uri(provider),'code':code})
            credentials=google_tokens(value)
            if not isinstance(value.get('scope'),str) or GBP_SCOPES not in value['scope'].split():
                raise ProviderError(ErrorReason.PERMISSION_MISSING,'Grant Google business.manage access and reconnect')
            credentials['scopes']=value['scope'].split()
            return await GoogleBusinessProvider(self.http,s).discover(credentials)
        if provider=='tiktok_business':
            adapter=TikTokBusinessProvider(self.http,s)
            value=await adapter.request('POST','tt_user/oauth2/token/',rotation=True,json={
                'client_id':s.tiktok_business_client_id,'client_secret':s.tiktok_business_client_secret.get_secret_value(),
                'grant_type':'authorization_code','auth_code':code,'redirect_uri':self.redirect_uri(provider)})
            credentials=business_tokens(value)
            await adapter.inspect(credentials)
            profile=await adapter.profile(credentials)
            return [{'provider':provider,'id':credentials['id'],'name':profile['display_name'],
                'credentials':credentials,'expires_at':datetime.fromisoformat(credentials['expires_at'])}]
        if provider=='tiktok':
            adapter=TikTokProvider(self.http,s)
            value=await adapter.request('POST','oauth/token/',data={'client_key':s.tiktok_client_key,
                'client_secret':s.tiktok_client_secret.get_secret_value(),'grant_type':'authorization_code',
                'redirect_uri':self.redirect_uri(provider),'code':code})
            credentials=tiktok_tokens(value)
            if set(TIKTOK_SCOPES.split(','))-set(credentials['scopes']):
                raise ProviderError(ErrorReason.PERMISSION_MISSING,'Grant the requested TikTok profile, video list and inbox upload scopes, then reconnect')
            profile=await adapter.profile(credentials)
            credentials.update(profile=profile)
            return [{'provider':'tiktok','id':credentials['id'],'name':profile['display_name'],'credentials':credentials,
                'expires_at':datetime.fromisoformat(credentials['expires_at'])}]
        if provider=='youtube':
            value=await self.request('POST',TOKEN_URL,data={'client_id':s.google_client_id,
                'client_secret':s.google_client_secret.get_secret_value(),'grant_type':'authorization_code',
                'redirect_uri':self.redirect_uri(provider),'code':code})
            credentials=google_tokens(value)
            response=await YouTubeProvider(self.http,s).request('GET',YOUTUBE_BASE+'channels',credentials,
                params={'part':'id,snippet','mine':'true','maxResults':50})
            channels=response.json().get('items',[])
            if len(channels)!=1 or not channel_id(channels[0].get('id')):
                raise ProviderError(ErrorReason.PERMISSION_MISSING,'Choose one existing YouTube channel during Google consent, then reconnect')
            channel=channels[0]; name=channel.get('snippet',{}).get('title')
            if not isinstance(name,str) or not name:
                raise ProviderError(ErrorReason.AUTH_REVOKED,'YouTube channel identity is incomplete; reconnect')
            credentials.update(id=channel['id'],scopes=value.get('scope',YOUTUBE_SCOPES).split())
            return [{'provider':'youtube','id':channel['id'],'name':name,'credentials':credentials,
                'expires_at':datetime.fromisoformat(credentials['expires_at'])}]
        if provider=='pinterest':
            value=await self.request('POST',PINTEREST_BASE+'oauth/token',
                auth=httpx.BasicAuth(s.pinterest_client_id,s.pinterest_client_secret.get_secret_value()),
                data={'grant_type':'authorization_code','code':code,'redirect_uri':self.redirect_uri(provider)})
            credentials=pinterest_tokens(value)
            profile=await PinterestProvider(self.http,s).request('GET','user_account',credentials)
            if not pinterest_id(profile.get('id')) or not isinstance(profile.get('username'),str):
                raise ProviderError(ErrorReason.AUTH_REVOKED,'Pinterest did not return account identity; reconnect')
            credentials.update(id=profile['id'],username=profile['username'])
            return [{'provider':'pinterest','id':profile['id'],'name':profile['username'],
                'credentials':credentials,'expires_at':datetime.fromisoformat(credentials['expires_at'])}]
        if provider=='x':
            if not state: raise HTTPException(400,'Start a fresh X connection from Connections')
            value=await self.request('POST',BASE+'oauth2/token',auth=token_auth(s),data={
                'grant_type':'authorization_code','code':code,'redirect_uri':self.redirect_uri(provider),
                'code_verifier':pkce_verifier(s,state)},failure_stage='X authorization code exchange failed')
            credentials=token_credentials(value)
            profile=await self.request('GET',BASE+'users/me',headers={'Authorization':'Bearer '+credentials['access_token']},
                failure_stage='X profile lookup failed')
            data=profile.get('data',{})
            if not numeric_id(data.get('id')) or not isinstance(data.get('username'),str):
                raise ProviderError(ErrorReason.AUTH_REVOKED,'X did not return an account identity; reconnect')
            credentials.update(id=data['id'],username=data['username'],scopes=value.get('scope',SCOPES).split())
            return [{'provider':'x','id':data['id'],'name':data['username'],'credentials':credentials,
                'expires_at':datetime.fromisoformat(credentials['expires_at'])}]
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
                'grant_type':'authorization_code','redirect_uri':self.redirect_uri(provider),'code':code},
                failure_stage='Threads authorization code exchange failed')
            long=await self.request('GET','https://graph.threads.net/access_token',
                # Match Meta's Threads sample: this grant receives the short
                # token as access_token, rather than bearer authentication.
                params={'grant_type':'th_exchange_token','client_secret':s.threads_client_secret.get_secret_value(),
                        'access_token':short['access_token']},
                failure_stage='Threads long-lived token exchange failed')
            profile=await self.request('GET','https://graph.threads.net/v1.0/me',params={'fields':'id,username'},
                headers={'Authorization':'Bearer '+long['access_token']},failure_stage='Threads profile lookup failed')
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
@router.get('/connections/oauth/{provider}/callback/',include_in_schema=False)
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
    accounts=await service.exchange(provider,code,state) if provider=='x' else await service.exchange(provider,code)
    connected=[]
    for account in accounts:
        connected.append(save_connection(db,request.app.state.settings,actor,account['provider'],account['id'],account['name'],account['credentials'],account.get('expires_at')))
    return {'connections':connected,'message':'Connected. Return to Post Chief.'}
