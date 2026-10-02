from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from postchief.models import SocialAccount,User,Campaign,Publication
from postchief.vault import Vault
from postchief.publishing.engine import execute_publication, due_publications
from provider_contracts import PublishResult,PublicationPending,ProviderError,ErrorReason,Capabilities
import pytest
import asyncio


class FakeProvider:
    idempotent=True
    capabilities=Capabilities()
    calls=0
    def validate(self,body,media): pass
    async def publish(self,credentials,body,media,key,state):
        self.calls+=1
        return PublishResult('remote:'+key,'https://social.test/post')


def setup_campaign(app,client,providers=('bluesky',)):
    with app.state.sessions() as db:
        owner=db.scalar(select(User)); ids=[]
        for provider in providers:
            account=SocialAccount(org_id=owner.org_id,provider=provider,remote_id=provider,name=provider,
                credentials=Vault(app.state.settings.encryption_key.get_secret_value()).encrypt({'token':'private'}))
            db.add(account); db.flush(); ids.append(account.id)
        db.commit()
    response=client.post('/api/campaigns',json={'title':'Release','body':'Shipped','account_ids':ids})
    assert response.status_code==201
    return response.json()


@pytest.mark.asyncio
async def test_scheduler_publishes_due_campaign_once_and_ignores_drafts(app,client):
    campaign=setup_campaign(app,client)
    id=campaign['publications'][0]['id']; provider=FakeProvider()
    assert due_publications(app.state.sessions)==[]
    scheduled=client.post(f'/api/campaigns/{campaign["id"]}/publish')
    assert scheduled.status_code==200
    assert due_publications(app.state.sessions)==[id]
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    assert provider.calls==1
    result=client.get(f'/api/campaigns/{campaign["id"]}').json()
    assert result['status']=='published' and result['publications'][0]['status']=='published'


@pytest.mark.asyncio
async def test_partial_failure_retries_only_failed_destination(app,client):
    campaign=setup_campaign(app,client,('bluesky','threads'))
    assert client.post(f'/api/campaigns/{campaign["id"]}/publish').status_code==200
    class Failed(FakeProvider):
        async def publish(self,*args): raise ProviderError(ErrorReason.PERMISSION_MISSING,'Reconnect permission')
    success=FakeProvider(); failed=Failed()
    for pub in campaign['publications']:
        await execute_publication(pub['id'],app.state.sessions,app.state.settings,lambda name,*args:success if name=='bluesky' else failed)
    result=client.get(f'/api/campaigns/{campaign["id"]}').json()
    bad=next(p for p in result['publications'] if p['status']=='failed')
    assert result['status']=='partial'
    assert client.post(f'/api/publications/{bad["id"]}/retry').status_code==200
    await execute_publication(bad['id'],app.state.sessions,app.state.settings,lambda *args:success)
    assert success.calls==2
    assert client.get(f'/api/campaigns/{campaign["id"]}').json()['status']=='published'


@pytest.mark.asyncio
async def test_pending_checkpoint_is_encrypted_and_resume_does_not_repeat_preparation(app,client):
    campaign=setup_campaign(app,client)
    client.post(f'/api/campaigns/{campaign["id"]}/publish')
    class Pending(FakeProvider):
        async def publish(self,credentials,body,media,key,state):
            self.calls+=1
            if 'upload' not in state: raise PublicationPending({'upload':'private-url'},1)
            assert state['upload']=='private-url'
            return PublishResult('remote')
    provider=Pending(); id=campaign['publications'][0]['id']
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    with app.state.sessions() as db:
        pub=db.get(Publication,id)
        assert pub.status=='retrying' and pub.error is None
        assert 'private-url' not in str(pub.provider_state)
        pub.next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1); db.commit()
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    assert provider.calls==2
    assert client.get(f'/api/publications/{id}').json()['status']=='published'


@pytest.mark.asyncio
async def test_expired_non_idempotent_public_intent_requires_reconciliation(app,client):
    campaign=setup_campaign(app,client)
    client.post(f'/api/campaigns/{campaign["id"]}/publish')
    id=campaign['publications'][0]['id']
    with app.state.sessions() as db:
        pub=db.get(Publication,id); pub.status='processing'; pub.started_at=datetime.now(timezone.utc)-timedelta(minutes=20)
        pub.provider_state={'sealed':Vault(app.state.settings.encryption_key.get_secret_value()).encrypt({'phase':'publish_intent'})}; db.commit()
    provider=FakeProvider(); provider.idempotent=False
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    assert provider.calls==0
    result=client.get(f'/api/publications/{id}').json()
    assert result['error']['action_required']=='RECONCILE'
    assert client.post(f'/api/publications/{id}/retry').status_code==409


def test_schedule_requires_offset_and_cancel_prevents_execution(app,client):
    campaign=setup_campaign(app,client)
    assert client.post(f'/api/campaigns/{campaign["id"]}/schedule',json={'scheduled_at':'2026-12-01T08:00:00'}).status_code==422
    assert client.post(f'/api/campaigns/{campaign["id"]}/schedule',json={'scheduled_at':'2026-12-01T08:00:00-05:00'}).status_code==200
    assert client.post(f'/api/campaigns/{campaign["id"]}/cancel').status_code==200
    assert due_publications(app.state.sessions)==[]


@pytest.mark.asyncio
async def test_concurrent_duplicate_jobs_share_one_active_lease(app,client):
    campaign=setup_campaign(app,client); client.post(f'/api/campaigns/{campaign["id"]}/publish')
    entered=asyncio.Event(); release=asyncio.Event()
    class Blocking(FakeProvider):
        async def publish(self,*args):
            self.calls+=1; entered.set(); await release.wait(); return PublishResult('remote')
    provider=Blocking(); id=campaign['publications'][0]['id']
    first=asyncio.create_task(execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider))
    await asyncio.wait_for(entered.wait(),5)
    try:
        await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
        assert provider.calls==1
        assert client.post(f'/api/campaigns/{campaign["id"]}/cancel').status_code==409
    finally: release.set(); await first


@pytest.mark.asyncio
async def test_transient_failure_keeps_backoff_and_does_not_run_early(app,client):
    campaign=setup_campaign(app,client); client.post(f'/api/campaigns/{campaign["id"]}/publish')
    class Limited(FakeProvider):
        async def publish(self,*args): self.calls+=1; raise ProviderError(ErrorReason.RATE_LIMITED,'Slow down',True)
    provider=Limited(); id=campaign['publications'][0]['id']
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    await execute_publication(id,app.state.sessions,app.state.settings,lambda *args:provider)
    assert provider.calls==1 and due_publications(app.state.sessions)==[]
    assert client.get(f'/api/publications/{id}').json()['status']=='retrying'


def test_owner_can_reconcile_existing_post_but_not_retry_an_uncertain_result(app,client):
    campaign=setup_campaign(app,client); id=campaign['publications'][0]['id']
    with app.state.sessions() as db:
        pub=db.get(Publication,id); pub.status='failed'; pub.error={'action_required':'RECONCILE'}; pub.attempts=1
        db.get(Campaign,campaign['id']).status='failed'; db.commit()
    response=client.post(f'/api/publications/{id}/reconcile',json={'resolution':'published','provider_id':'at://did:plc:owner/app.bsky.feed.post/record'})
    assert response.status_code==200 and response.json()['status']=='published'
    assert client.post(f'/api/publications/{id}/retry').status_code==409
