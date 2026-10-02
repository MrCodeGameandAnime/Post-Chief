import json
import httpx
import pytest
from postchief.providers.linkedin import LinkedInProvider
from postchief.providers.oauth import OAuthService
from provider_contracts import Media, ProviderError, PublicationPending, ErrorReason

CREDS={'author':'urn:li:person:owner','access_token':'secret','scopes':['w_member_social']}


@pytest.mark.asyncio
async def test_linkedin_text_requires_intent_and_retains_header_urn(app):
    calls=[]
    def transport(request):
        calls.append(request)
        assert request.headers['Linkedin-Version']=='202609'
        assert request.headers['X-Restli-Protocol-Version']=='2.0.0'
        assert request.headers['Authorization']=='Bearer secret'
        data=json.loads(request.content)
        assert data['author']=='urn:li:person:owner' and data['lifecycleState']=='PUBLISHED'
        assert data['commentary']=='A shipped release'
        return httpx.Response(201,headers={'x-restli-id':'urn:li:share:42'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider=LinkedInProvider(http,app.state.settings)
        with pytest.raises(PublicationPending) as first:
            await provider.publish(CREDS,'A shipped release',[],'key',{})
        assert not calls
        result=await provider.publish(CREDS,'A shipped release',[],'key',first.value.state)
        assert result.provider_id=='urn:li:share:42'
        assert result.url=='https://www.linkedin.com/feed/update/urn:li:share:42/'
        repeated=await provider.publish(CREDS,'A shipped release',[],'key',result.state)
        assert repeated.provider_id==result.provider_id and len(calls)==1


@pytest.mark.asyncio
async def test_linkedin_image_upload_reuses_urn_without_forbidden_member_get(tmp_path,app):
    file=tmp_path/'photo.png'; file.write_bytes(b'photo')
    calls=[]
    def transport(request):
        calls.append(request)
        if request.url.path=='/rest/images':
            assert json.loads(request.content)['initializeUploadRequest']['owner']==CREDS['author']
            return httpx.Response(200,json={'value':{'image':'urn:li:image:photo','uploadUrl':'https://www.linkedin.com/dms-uploads/photo'}})
        if request.method=='PUT':
            assert request.content==b'photo'
            return httpx.Response(201)
        assert request.method=='POST' and request.url.path=='/rest/posts'
        assert json.loads(request.content)['content']['media']['id']=='urn:li:image:photo'
        return httpx.Response(201,headers={'x-restli-id':'urn:li:share:42'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider=LinkedInProvider(http,app.state.settings)
        state={}; media=[Media(str(file),'image/png',5)]
        for phase in ['media_uploaded','publish_intent']:
            with pytest.raises(PublicationPending) as pending:
                await provider.publish(CREDS,'Photo',media,'key',state)
            state=pending.value.state
            assert state['phase']==phase
        await provider.publish(CREDS,'Photo',media,'key',state)
    assert len(calls)==3 and all(r.method!='GET' for r in calls)


@pytest.mark.asyncio
async def test_linkedin_lost_public_write_requires_reconciliation(app):
    def transport(request): raise httpx.ReadTimeout('Lost',request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider=LinkedInProvider(http,app.state.settings)
        with pytest.raises(ProviderError) as failure:
            await provider.publish(CREDS,'Release',[],'key',{'phase':'publish_intent'})
        assert failure.value.uncertain and not failure.value.retryable


@pytest.mark.asyncio
async def test_linkedin_rejects_untrusted_media_upload_host(tmp_path,app):
    file=tmp_path/'photo.png'; file.write_bytes(b'photo')
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(200,json={'value':{'image':'urn:li:image:photo','uploadUrl':'https://attacker.test/upload'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(ProviderError):
            await LinkedInProvider(http,app.state.settings).publish(CREDS,'Photo',[Media(str(file),'image/png',5)],'key',{})
    assert len(calls)==1


@pytest.mark.asyncio
async def test_linkedin_missing_analytics_permission_is_explicit(app):
    provider=LinkedInProvider(None,app.state.settings)
    with pytest.raises(ProviderError) as error:
        await provider.get_post_metrics(CREDS,'urn:li:share:42')
    assert error.value.reason==ErrorReason.PERMISSION_MISSING


@pytest.mark.asyncio
async def test_linkedin_oauth_uses_authenticated_userinfo_and_optional_refresh_token(app):
    def transport(request):
        if request.url.path=='/oauth/v2/accessToken':
            assert b'grant_type=authorization_code' in request.content
            return httpx.Response(200,json={'access_token':'access','expires_in':5184000,'refresh_token':'refresh','refresh_token_expires_in':31536000,'scope':'openid profile w_member_social'})
        assert request.url.path=='/v2/userinfo' and request.headers['Authorization']=='Bearer access'
        return httpx.Response(200,json={'sub':'member','name':'Owner'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        result=(await OAuthService(http,app.state.settings).exchange('linkedin','code'))[0]
    assert result['provider']=='linkedin' and result['id']=='urn:li:person:member'
    assert result['credentials']['refresh_token']=='refresh'


@pytest.mark.asyncio
async def test_linkedin_video_upload_persists_parts_and_seals_upload_credentials(tmp_path,app):
    file=tmp_path/'video.mp4'; file.write_bytes(b'abcdefgh')
    calls=[]
    def transport(request):
        calls.append(request)
        if request.url.params.get('action')=='initializeUpload':
            return httpx.Response(200,json={'value':{'video':'urn:li:video:v','uploadToken':'private-upload-token','uploadInstructions':[
                {'firstByte':0,'lastByte':3,'uploadUrl':'https://www.linkedin.com/dms-uploads/a?signature=private'},
                {'firstByte':4,'lastByte':7,'uploadUrl':'https://www.linkedin.com/dms-uploads/b?signature=private'}]}})
        if request.method=='PUT':
            assert request.content in (b'abcd',b'efgh')
            return httpx.Response(200,headers={'etag':'part-'+request.url.path[-1]})
        if request.url.params.get('action')=='finalizeUpload':
            assert json.loads(request.content)['finalizeUploadRequest']['uploadedPartIds']==['part-a','part-b']
            return httpx.Response(200)
        assert request.url.path=='/rest/posts'
        assert json.loads(request.content)['content']['media']['id']=='urn:li:video:v'
        return httpx.Response(201,headers={'x-restli-id':'urn:li:share:42'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider=LinkedInProvider(http,app.state.settings); state={}; media=[Media(str(file),'video/mp4',8)]
        for phase in ['video_upload','video_upload','video_upload','media_uploaded','publish_intent']:
            with pytest.raises(PublicationPending) as pending: await provider.publish(CREDS,'Video',media,'key',state)
            state=pending.value.state
            assert state['phase']==phase
            assert 'private' not in json.dumps(state)
        result=await provider.publish(CREDS,'Video',media,'key',state)
        assert result.provider_id=='urn:li:share:42'
    assert len(calls)==5


@pytest.mark.asyncio
async def test_linkedin_organization_selection_filters_revoked_and_ineligible_roles(app):
    def transport(request):
        if request.url.path=='/rest/organizationAcls':
            return httpx.Response(200,json={'elements':[
                {'organization':'urn:li:organization:1','roleAssignee':CREDS['author'],'role':'ADMINISTRATOR','state':'APPROVED'},
                {'organization':'urn:li:organization:2','roleAssignee':CREDS['author'],'role':'ADMINISTRATOR','state':'REVOKED'},
                {'organization':'urn:li:organization:3','roleAssignee':CREDS['author'],'role':'ANALYST','state':'APPROVED'}]})
        assert request.url.path=='/rest/organizations/1'
        return httpx.Response(200,json={'localizedName':'404 Builds'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        result=await LinkedInProvider(http,app.state.settings).organizations({**CREDS,'scopes':['w_organization_social','rw_organization_admin']})
    assert result==[{'id':'urn:li:organization:1','name':'404 Builds'}]
