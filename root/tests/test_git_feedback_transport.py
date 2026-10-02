import httpx
import pytest
from postchief.github.service import GitHubService,GitHubError


async def authorized(*args,**kwargs):return 'scoped-token'


@pytest.mark.asyncio
async def test_feedback_uses_one_base_tree_commit_and_fast_forward_only():
    seen=[]
    def handler(request):
        import json
        seen.append(request)
        path=request.url.path
        if request.method=='GET':return httpx.Response(200,json={'object':{'sha':'a'*40}})
        data=json.loads(request.content)
        if path.endswith('/trees'):
            assert data['base_tree']=='c'*40 and len(data['tree'])==3
            return httpx.Response(201,json={'sha':'d'*40})
        if path.endswith('/commits'):
            assert data['parents']==['a'*40] and data['tree']=='d'*40
            return httpx.Response(201,json={'sha':'b'*40})
        assert request.method=='PATCH' and data=={'sha':'b'*40,'force':False}
        return httpx.Response(200,json={'object':{'sha':'b'*40}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service=GitHubService('','',http);service.authorized=authorized
        paths=['posts/post.md','docs/CONTENT_LEDGER.md','docs/ANALYTICS.md']
        snapshot={'head':'a'*40,'tree':'c'*40,'branch':'main','files':{p:{'mode':'100644'} for p in paths}}
        result=await service.commit_feedback(1,'owner/social',snapshot,{p:'content' for p in paths},'Feedback')
        assert result['sha']=='b'*40
    assert len(seen)==4


@pytest.mark.asyncio
async def test_concurrent_git_ref_update_is_never_forced():
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'object':{'sha':'a'*40}})
        if request.method=='PATCH':return httpx.Response(422,json={'message':'Not a fast forward'})
        return httpx.Response(201,json={'sha':'b'*40})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service=GitHubService('','',http);service.authorized=authorized
        with pytest.raises(GitHubError) as error:
            await service.commit_feedback(1,'owner/social',{'head':'a'*40,'tree':'c'*40,'branch':'main','files':{'posts/post.md':{'mode':'100644'}}},{'posts/post.md':'content'},'Feedback')
        assert error.value.status==409


@pytest.mark.asyncio
async def test_feedback_snapshot_rejects_symlink_parent():
    def handler(request):
        path=request.url.path
        if path=='/repos/owner/social':return httpx.Response(200,json={'default_branch':'main'})
        if '/git/ref/' in path:return httpx.Response(200,json={'object':{'sha':'a'*40}})
        if '/git/commits/' in path:return httpx.Response(200,json={'tree':{'sha':'c'*40}})
        return httpx.Response(200,json={'tree':[{'path':'docs','mode':'120000','type':'blob','sha':'d'*40}],'truncated':False})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service=GitHubService('','',http);service.authorized=authorized
        with pytest.raises(GitHubError) as error:await service.feedback_snapshot(1,'owner/social',['docs/ANALYTICS.md'])
        assert error.value.status==422
