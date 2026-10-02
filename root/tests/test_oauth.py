from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from sqlalchemy import select
from postchief.models import OAuthState, SocialAccount
from postchief.providers.oauth import get_oauth_service
from postchief.providers.oauth import OAuthService
import httpx
import pytest
from pydantic import SecretStr


def test_meta_authorization_requests_business_page_discovery_permission(app):
    settings=app.state.settings.model_copy(update={'meta_client_id':'test-app','meta_client_secret':SecretStr('test-secret')})
    query=parse_qs(urlparse(OAuthService(None,settings).authorization_url('meta','test-state')).query)
    assert set(query['scope'][0].split(','))=={
        'business_management','pages_show_list','pages_read_engagement','pages_read_user_content','pages_manage_posts',
        'instagram_basic','instagram_content_publish','instagram_manage_insights'}
    assert query['state']==['test-state']


class FakeOAuth:
    calls=0
    def authorization_url(self,provider,state): return 'https://provider.test/authorize?state='+state
    async def exchange(self,provider,code):
        self.calls+=1
        assert code=='code'
        return [{'provider':'threads','id':'remote','name':'Owner','credentials':{'id':'remote','access_token':'private-token'},'expires_at':datetime.now(timezone.utc)+timedelta(days=60)}]


def test_oauth_state_is_single_use_actor_bound_and_tokens_never_return(client,app):
    service=FakeOAuth()
    app.dependency_overrides[get_oauth_service]=lambda:service
    response=client.post('/api/connections/oauth/threads/authorize')
    assert response.status_code==200
    state=parse_qs(urlparse(response.json()['url']).query)['state'][0]
    with app.state.sessions() as db:
        stored=db.scalar(select(OAuthState))
        assert state not in stored.token_hash
    wrong=client.get('/api/connections/oauth/meta/callback',params={'state':state,'code':'code'})
    assert wrong.status_code==400 and service.calls==0
    callback=client.get('/api/connections/oauth/threads/callback',params={'state':state,'code':'code'})
    assert callback.status_code==200 and 'private-token' not in callback.text
    replay=client.get('/api/connections/oauth/threads/callback',params={'state':state,'code':'code'})
    assert replay.status_code==400 and service.calls==1
    with app.state.sessions() as db:
        assert 'private-token' not in db.scalar(select(SocialAccount)).credentials


def test_oauth_expired_state_rejected_before_provider_call(client,app):
    service=FakeOAuth()
    app.dependency_overrides[get_oauth_service]=lambda:service
    url=client.post('/api/connections/oauth/threads/authorize').json()['url']
    state=parse_qs(urlparse(url).query)['state'][0]
    with app.state.sessions() as db:
        db.scalar(select(OAuthState)).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
    assert client.get('/api/connections/oauth/threads/callback',params={'state':state,'code':'code'}).status_code==400
    assert service.calls==0


def test_meta_configuration_error_without_state_is_readable_and_never_exchanges(client,app):
    service=FakeOAuth();app.dependency_overrides[get_oauth_service]=lambda:service
    response=client.get('/api/connections/oauth/meta/callback',params={'error_code':'100','error_message':'Invalid Scopes: private-untrusted-message'})
    assert response.status_code==400
    assert 'permissions' in response.json()['detail']
    assert 'private-untrusted-message' not in response.text
    assert service.calls==0
    with app.state.sessions() as db:assert db.scalar(select(SocialAccount)) is None


def test_oauth_success_still_requires_state_and_error_cannot_exchange_code(client,app):
    service=FakeOAuth();app.dependency_overrides[get_oauth_service]=lambda:service
    assert client.get('/api/connections/oauth/meta/callback',params={'code':'code'}).status_code==400
    url=client.post('/api/connections/oauth/meta/authorize').json()['url']
    state=parse_qs(urlparse(url).query)['state'][0]
    response=client.get('/api/connections/oauth/meta/callback',params={'state':state,'code':'code','error_code':'100'})
    assert response.status_code==400 and service.calls==0
    with app.state.sessions() as db:assert db.scalar(select(OAuthState)).consumed is True


@pytest.mark.asyncio
async def test_meta_exchange_discovers_pages_and_linked_instagram_using_page_tokens(app):
    calls=[]
    def transport(request):
        calls.append(request)
        if request.url.path.endswith('/oauth/access_token'):
            return httpx.Response(200,json={'access_token':'long' if request.url.params.get('grant_type') else 'short'})
        assert request.headers['Authorization']=='Bearer long'
        assert request.url.params['fields'].startswith('id,name,access_token')
        return httpx.Response(200,json={'data':[{'id':'page','name':'Page','access_token':'page-secret','instagram_business_account':{'id':'ig','username':'owner'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        results=await OAuthService(http,app.state.settings).exchange('meta','code')
    assert [x['provider'] for x in results]==['facebook','instagram']
    assert all(x['credentials']['access_token']=='page-secret' for x in results)
    assert len(calls)==3


@pytest.mark.asyncio
async def test_threads_exchange_uses_separate_app_and_long_lived_token(app):
    def transport(request):
        if request.url.path=='/oauth/access_token':
            assert request.method=='POST' and b'grant_type=authorization_code' in request.content
            return httpx.Response(200,json={'access_token':'short'})
        if request.url.path=='/access_token':
            assert request.url.params['access_token']=='short'
            assert 'Authorization' not in request.headers
            assert request.url.params['grant_type']=='th_exchange_token'
            return httpx.Response(200,json={'access_token':'long','expires_in':5184000})
        assert request.url.path=='/v1.0/me' and request.headers['Authorization']=='Bearer long'
        return httpx.Response(200,json={'id':'threads-id','username':'owner'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        result=(await OAuthService(http,app.state.settings).exchange('threads','code'))[0]
    assert result['credentials']['access_token']=='long'
    assert result['expires_at']>datetime.now(timezone.utc)+timedelta(days=59)


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_step,stage', [
    (1, 'Threads authorization code exchange failed'),
    (2, 'Threads long-lived token exchange failed'),
    (3, 'Threads profile lookup failed'),
])
async def test_threads_exchange_reports_fixed_stage_without_provider_secrets(app,failed_step,stage):
    from provider_contracts import ProviderError,ErrorReason
    calls=0

    def transport(request):
        nonlocal calls
        calls+=1
        if calls==failed_step:
            return httpx.Response(400,json={'error':{'code':190,'error_subcode':'private-subcode','message':'private-provider-secret'}})
        return httpx.Response(200,json={'access_token':'private-token','expires_in':5184000})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(ProviderError) as caught:
            await OAuthService(http,app.state.settings).exchange('threads','private-code')
    assert caught.value.reason==ErrorReason.AUTH_REVOKED
    assert stage in caught.value.message
    assert '190' in caught.value.message
    assert 'private' not in caught.value.message
    assert calls==failed_step
