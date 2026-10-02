"""Offline acceptance: native Instagram adapter, real application state, fixture IO."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from postchief.models import SocialAccount, User, Publication
from postchief.vault import Vault
from postchief.providers.meta import InstagramProvider
from postchief.publishing.engine import due_publications, execute_publication
from postchief.analytics.service import collect
from test_feedback import FeedbackService, workspace


@pytest.mark.asyncio
async def test_instagram_offline_campaign_to_metrics_to_github(app, client):
    app.state.settings.public_url='https://postchief.example.test'
    with app.state.sessions() as db:
        owner=db.scalar(select(User))
        account=SocialAccount(org_id=owner.org_id,provider='instagram',remote_id='ig-account',name='404 Builds',
            credentials=Vault(app.state.settings.encryption_key.get_secret_value()).encrypt({'id':'ig-account','access_token':'fixture-secret'}))
        db.add(account);db.commit();account_id=account.id
    image=BytesIO();Image.new('RGB',(1080,1080),(20,80,50)).save(image,format='JPEG')
    uploaded=client.post('/api/assets',files={'file':('approved.jpg',image.getvalue(),'image/jpeg')})
    assert uploaded.status_code==201
    created=client.post('/api/campaigns',json={'title':'404 Builds offline acceptance','body':'Approved fixture caption',
        'account_ids':[account_id],'asset_ids':[uploaded.json()['id']]})
    assert created.status_code==201
    campaign=created.json();publication_id=campaign['publications'][0]['id']
    scheduled=client.post(f'/api/campaigns/{campaign["id"]}/schedule',json={'scheduled_at':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()})
    assert scheduled.status_code==200 and due_publications(app.state.sessions)==[]
    assert client.post(f'/api/campaigns/{campaign["id"]}/publish').status_code==200
    assert due_publications(app.state.sessions)==[publication_id]
    calls=[]
    def transport(request):
        calls.append((request.method,request.url.path))
        assert request.headers['Authorization']=='Bearer fixture-secret'
        if request.method=='POST' and request.url.path.endswith('/ig-account/media'):
            data=parse_qs(request.content.decode());assert data['caption']==['Approved fixture caption']
            media=urlsplit(data['image_url'][0])
            assert media.scheme=='https' and media.hostname=='postchief.example.test'
            assert client.get(media.path+'?'+media.query).content==image.getvalue()
            return httpx.Response(200,json={'id':'container'})
        if request.url.path.endswith('/container'):
            return httpx.Response(200,json={'status_code':'FINISHED'})
        if request.method=='POST' and request.url.path.endswith('/ig-account/media_publish'):
            assert parse_qs(request.content.decode())['creation_id']==['container']
            return httpx.Response(200,json={'id':'instagram-post'})
        if request.url.path.endswith('/instagram-post'):
            return httpx.Response(200,json={'like_count':0,'comments_count':2,'permalink':'https://www.instagram.com/p/fixture/'})
        raise AssertionError(request.url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        factory=lambda *args:InstagramProvider(http,app.state.settings)
        for phase in ('processing','publish_intent','published'):
            await execute_publication(publication_id,app.state.sessions,app.state.settings,factory)
            with app.state.sessions() as db:
                pub=db.get(Publication,publication_id)
                state=Vault(app.state.settings.encryption_key.get_secret_value()).decrypt(pub.provider_state['sealed'])
                assert state['phase']==phase
                if phase!='published':
                    pub.next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1);db.commit()
        await execute_publication(publication_id,app.state.sessions,app.state.settings,factory)
        await collect(publication_id,app.state.sessions,app.state.settings,factory)
        await collect(publication_id,app.state.sessions,app.state.settings,factory)
    result=client.get(f'/api/campaigns/{campaign["id"]}').json()
    assert result['status']=='published' and result['publications'][0]['provider_id']=='instagram-post'
    history=client.get(f'/api/analytics/publications/{publication_id}').json()
    assert len(history)==1 and history[0]['metrics']['normalized']=={'likes':0,'comments':2}
    assert sum(method=='POST' and path.endswith('/media_publish') for method,path in calls)==1
    assert sum(method=='POST' and path.endswith('/media') for method,path in calls)==1
    service=FeedbackService();workspace(app,service)
    preview=client.get(f'/api/feedback/campaigns/{campaign["id"]}/preview').json()
    assert 'fixture-secret' not in str(preview) and '?token=' not in str(preview)
    metrics=next(file['content'] for file in preview['files'] if file['path']=='docs/ANALYTICS.md')
    assert '"likes": 0' in metrics and '"comments": 2' in metrics
    payload={key:preview[key] for key in ('revision','digest','base_commit')}
    assert client.post(f'/api/feedback/campaigns/{campaign["id"]}/sync',json=payload).status_code==200
    assert client.post(f'/api/feedback/campaigns/{campaign["id"]}/sync',json=payload).json()['changed'] is False
    assert service.writes==1 and len(service.files)==3
