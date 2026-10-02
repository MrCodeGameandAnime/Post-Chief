import pytest
import asyncio
from sqlalchemy import select
from postchief.models import Publication, AnalyticsSnapshot, SocialAccount
from postchief.analytics.service import collect, due_metrics, normalize
from test_publishing import setup_campaign, FakeProvider
from provider_contracts import ProviderError, ErrorReason


def published(app,client):
    campaign=setup_campaign(app,client)
    id=campaign['publications'][0]['id']
    with app.state.sessions() as db:
        pub=db.get(Publication,id); pub.status='published';pub.provider_id='remote';db.commit()
    return id


def test_normalization_preserves_missing_and_provider_meanings():
    value=normalize('bluesky',{'likes':0,'replies':2,'reposts':3,'views':None,'quotes':1})
    assert value['normalized']=={'likes':0,'comments':2,'shares':3}
    assert value['provider_metrics']['quotes']==1
    assert value['semantics']['shares']=='Bluesky reposts'


@pytest.mark.asyncio
async def test_metrics_store_history_and_do_not_recollect_before_deadline(app,client):
    id=published(app,client)
    class Metrics(FakeProvider):
        async def get_post_metrics(self,*args): self.calls+=1; return {'likes':7,'replies':3}
    provider=Metrics()
    assert due_metrics(app.state.sessions)==[id]
    await collect(id,app.state.sessions,app.state.settings,lambda *args:provider)
    await collect(id,app.state.sessions,app.state.settings,lambda *args:provider)
    assert provider.calls==1
    result=client.get('/api/analytics').json()
    assert result[0]['latest']['metrics']['normalized']['likes']==7
    assert client.post(f'/api/analytics/publications/{id}/refresh').status_code==202
    await collect(id,app.state.sessions,app.state.settings,lambda *args:provider)
    assert len(client.get(f'/api/analytics/publications/{id}').json())==2


@pytest.mark.asyncio
async def test_permission_failure_is_visible_and_does_not_invent_zero_snapshot(app,client):
    id=published(app,client)
    class Denied(FakeProvider):
        async def get_post_metrics(self,*args): raise ProviderError(ErrorReason.PERMISSION_MISSING,'Analytics access requires approval')
    await collect(id,app.state.sessions,app.state.settings,lambda *args:Denied())
    result=client.get('/api/analytics').json()[0]
    assert result['latest'] is None and result['error']['reason']=='PERMISSION_MISSING'
    with app.state.sessions() as db: assert db.scalar(select(AnalyticsSnapshot.id)) is None
    assert client.get('/api/analytics/publications/not-owned').status_code==404


@pytest.mark.asyncio
async def test_concurrent_metrics_jobs_hold_one_lease(app,client):
    id=published(app,client);entered=asyncio.Event();release=asyncio.Event()
    class Blocking(FakeProvider):
        async def get_post_metrics(self,*args):
            self.calls+=1;entered.set();await release.wait();return {'likes':1}
    provider=Blocking()
    first=asyncio.create_task(collect(id,app.state.sessions,app.state.settings,lambda *args:provider))
    await asyncio.wait_for(entered.wait(),5)
    try:
        await collect(id,app.state.sessions,app.state.settings,lambda *args:provider)
        assert provider.calls==1
    finally:release.set();await first


@pytest.mark.asyncio
@pytest.mark.parametrize('link,expected',[
    ('https://www.instagram.com/p/Native_post-1/','https://www.instagram.com/p/Native_post-1/'),
    ('https://www.instagram.com/reel/Native1/','https://www.instagram.com/reel/Native1/'),
    ('https://evil.example/p/Native1/',None),
    ('javascript:alert(1)',None),
    ('https://www.instagram.com@evil.example/p/Native1/',None),
    ('https://www.instagram.com/p/Native1/?token=private',None),
])
async def test_instagram_native_permalink_reaches_campaign_record(app,client,link,expected):
    id=published(app,client)
    with app.state.sessions() as db:
        pub=db.get(Publication,id)
        db.get(SocialAccount,pub.account_id).provider='instagram'
        campaign_id=pub.campaign_id;db.commit()
    class Metrics(FakeProvider):
        async def get_post_metrics(self,*args):return {'likes':0,'comments':0,'provider':{'permalink':link}}
    await collect(id,app.state.sessions,app.state.settings,lambda *args:Metrics())
    assert client.get(f'/api/campaigns/{campaign_id}').json()['publications'][0]['url']==expected
    with app.state.sessions() as db:
        assert db.get(Publication,id).provider_url==expected
        # Historical snapshots also support already-published records.
        db.get(Publication,id).provider_url=None;db.commit()
    assert client.get(f'/api/campaigns/{campaign_id}').json()['publications'][0]['url']==expected
