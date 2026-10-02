import httpx
import pytest
from postchief.providers.meta import FacebookProvider, InstagramProvider, ThreadsProvider
from provider_contracts import Media, PublicationPending, ProviderError, ErrorReason


@pytest.mark.asyncio
async def test_facebook_checkpoints_before_public_write_and_marks_timeout_uncertain():
    calls = []
    def transport(request):
        calls.append(request)
        raise httpx.ReadTimeout('response lost', request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider = FacebookProvider(http)
        with pytest.raises(PublicationPending) as checkpoint:
            await provider.publish({'id':'page','access_token':'secret'},'hello',[],'key',{})
        assert calls == []
        assert checkpoint.value.state['phase'] == 'publish_intent'
        with pytest.raises(ProviderError) as failure:
            await provider.publish({'id':'page','access_token':'secret'},'hello',[],'key',checkpoint.value.state)
        assert failure.value.uncertain and not failure.value.retryable
        assert failure.value.to_dict()['action_required'] == 'RECONCILE'


@pytest.mark.asyncio
@pytest.mark.parametrize('cls,edge,publish_edge',[(InstagramProvider,'media','media_publish'),(ThreadsProvider,'threads','threads_publish')])
async def test_meta_container_is_resumed_and_final_publish_is_checkpointed(cls,edge,publish_edge):
    calls = []
    def transport(request):
        calls.append(request.url.path)
        assert request.headers['Authorization'] == 'Bearer secret'
        if request.url.path.endswith('/'+edge):
            return httpx.Response(200,json={'id':'container'})
        if request.url.path.endswith('/container'):
            return httpx.Response(200,json={'status_code':'FINISHED','status':'FINISHED'})
        if request.url.path.endswith('/'+publish_edge):
            return httpx.Response(200,json={'id':'public-post'})
        raise AssertionError(request.url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider = cls(http)
        media = [Media('unused','image/jpeg',200,url='https://media.example.test/photo.jpg')]
        creds = {'id':'account','access_token':'secret'}
        with pytest.raises(PublicationPending) as first:
            await provider.publish(creds,'caption',media,'key',{})
        assert first.value.state['container_id'] == 'container'
        with pytest.raises(PublicationPending) as second:
            await provider.publish(creds,'caption',media,'key',first.value.state)
        assert second.value.state['phase'] == 'publish_intent'
        result = await provider.publish(creds,'caption',media,'key',second.value.state)
        assert result.provider_id == 'public-post'
        assert sum(path.endswith('/'+edge) for path in calls) == 1
        assert sum(path.endswith('/'+publish_edge) for path in calls) == 1


def test_instagram_requires_media_and_public_jpeg():
    provider = InstagramProvider(None)
    with pytest.raises(ProviderError): provider.validate('text',[])
    with pytest.raises(ProviderError): provider.validate('text',[Media('unused','image/png',100,url='http://localhost/file')])


@pytest.mark.asyncio
async def test_meta_normalizes_revoked_and_rate_limited_credentials():
    for code,reason,retryable in [(190,ErrorReason.AUTH_EXPIRED,False),(4,ErrorReason.RATE_LIMITED,True),(200,ErrorReason.PERMISSION_MISSING,False)]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400,json={'error':{'code':code,'message':'must not leak secret'}}))) as http:
            with pytest.raises(ProviderError) as error:
                await FacebookProvider(http).get_post_metrics({'id':'page','access_token':'secret'},'post')
            assert error.value.reason == reason and error.value.retryable == retryable
            assert 'secret' not in error.value.message


@pytest.mark.asyncio
async def test_facebook_video_waits_for_confirmed_publication():
    calls=[]
    def transport(request):
        calls.append(request.url.path)
        if request.url.host == 'rupload.facebook.com':
            assert request.headers['file_url']=='https://media.example.test/video.mp4'
            return httpx.Response(200,json={'success':True})
        if request.method=='GET':
            return httpx.Response(200,json={'status':{'publishing_phase':{'status':'complete'}}})
        if b'upload_phase=start' in request.content:
            return httpx.Response(200,json={'video_id':'video','upload_url':'https://attacker.test'})
        return httpx.Response(200,json={'success':True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider=FacebookProvider(http)
        state={}
        for phase in ['video_upload','publish_intent','video_processing']:
            with pytest.raises(PublicationPending) as pending:
                await provider.publish({'id':'page','access_token':'secret'},'demo',[Media('x','video/mp4',100,url='https://media.example.test/video.mp4')],'key',state)
            state=pending.value.state
            assert state['phase']==phase
        result=await provider.publish({'id':'page','access_token':'secret'},'demo',[Media('x','video/mp4',100,url='https://media.example.test/video.mp4')],'key',state)
        assert result.provider_id=='video'
        assert len(calls)==4
