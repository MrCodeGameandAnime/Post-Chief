import json
import re
import httpx
import pytest
from postchief.providers.bluesky import BlueskyProvider
from provider_contracts import Media, ProviderError, PublicationPending, ErrorReason


CREDS = {"did":"did:plc:404","handle":"404.bsky.social","accessJwt":"access","refreshJwt":"refresh","pds":"https://bsky.social","pds_audience":"did:web:pds.host.bsky.network"}


@pytest.mark.asyncio
async def test_bluesky_reconciles_stable_record_on_repeated_publication():
    records = {}
    def transport(request):
        method = request.url.path.split("/")[-1]
        if method == "com.atproto.repo.getRecord":
            record = records.get(request.url.params["rkey"])
            return httpx.Response(200,json=record) if record else httpx.Response(400,json={"error":"RecordNotFound","message":"Not found"})
        if method == "com.atproto.repo.putRecord":
            payload = json.loads(request.content)
            if not re.fullmatch(r"[2-7ab][2-7a-z]{12}",payload["rkey"]):
                return httpx.Response(400,json={"error":"InvalidRecord","message":"feed.post requires a TID record key"})
            assert payload["swapRecord"] is None
            assert payload["repo"] == "did:plc:404"
            assert payload["record"]["text"] == "🚀 https://404.build"
            facet = payload["record"]["facets"][0]
            assert facet["index"] == {"byteStart":5,"byteEnd":22}
            value = {"uri":f'at://did:plc:404/app.bsky.feed.post/{payload["rkey"]}',"cid":"record-cid","value":payload["record"]}
            records[payload["rkey"]] = value
            return httpx.Response(200,json=value)
        raise AssertionError(f"Unexpected method {method}")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider = BlueskyProvider(http)
        first = await provider.publish(dict(CREDS),"🚀 https://404.build",[],"publication-1",{})
        second = await provider.publish(dict(CREDS),"🚀 https://404.build",[],"publication-1",{})
        assert first.provider_id == second.provider_id
        assert len(records) == 1
        assert first.url.startswith("https://bsky.app/profile/did:plc:404/post/")


@pytest.mark.asyncio
async def test_bluesky_images_upload_as_blobs_and_validate_byte_limits(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"image-data")
    blob = {"$type":"blob","ref":{"$link":"blob-cid"},"mimeType":"image/png","size":10}
    def transport(request):
        method = request.url.path.split("/")[-1]
        if method.endswith("getRecord"):
            return httpx.Response(400,json={"error":"RecordNotFound"})
        if method.endswith("uploadBlob"):
            assert request.content == b"image-data"
            assert request.headers["content-type"] == "image/png"
            return httpx.Response(200,json={"blob":blob})
        payload = json.loads(request.content)
        assert payload["record"]["embed"]["images"][0] == {"image":blob,"alt":"Release screenshot"}
        return httpx.Response(200,json={"uri":"at://did:plc:404/app.bsky.feed.post/key","cid":"cid"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider = BlueskyProvider(http)
        result = await provider.publish(dict(CREDS),"Screenshot",[Media(str(path),"image/png",10,"Release screenshot")],"image-publication",{})
        assert result.provider_id.endswith("/key")
        with pytest.raises(ProviderError) as invalid:
            provider.validate("Image",[Media(str(path),"image/png",2_000_001)])
        assert invalid.value.reason == ErrorReason.MEDIA_INVALID
        provider.validate("👩‍💻" * 200,[])
        with pytest.raises(ProviderError):
            provider.validate("a" * 301,[])


@pytest.mark.asyncio
async def test_bluesky_video_processing_resumes_without_reupload(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    uploads = []
    blob = {"$type":"blob","ref":{"$link":"video-cid"},"mimeType":"video/mp4","size":5}
    def transport(request):
        method = request.url.path.split("/")[-1]
        if method.endswith("getRecord"):
            return httpx.Response(400,json={"error":"RecordNotFound"})
        if method.endswith("getServiceAuth"):
            assert request.url.params["aud"] == "did:web:pds.host.bsky.network"
            assert request.url.params["lxm"] == "com.atproto.repo.uploadBlob"
            return httpx.Response(200,json={"token":"service-token"})
        if method.endswith("uploadVideo"):
            assert request.headers["authorization"] == "Bearer service-token"
            uploads.append(request.content)
            return httpx.Response(200,json={"jobStatus":{"jobId":"job-1","did":"did:plc:404","state":"JOB_STATE_ENCODING"}})
        if method.endswith("getJobStatus"):
            return httpx.Response(200,json={"jobStatus":{"jobId":"job-1","did":"did:plc:404","state":"JOB_STATE_COMPLETED","blob":blob}})
        payload = json.loads(request.content)
        assert payload["record"]["embed"]["video"] == blob
        return httpx.Response(200,json={"uri":"at://did:plc:404/app.bsky.feed.post/video","cid":"video-record"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider = BlueskyProvider(http)
        with pytest.raises(PublicationPending) as pending:
            await provider.publish(dict(CREDS),"Demo",[Media(str(path),"video/mp4",5)],"video-publication",{})
        result = await provider.publish(dict(CREDS),"Demo",[Media(str(path),"video/mp4",5)],"video-publication",pending.value.state)
        assert result.provider_id.endswith("/video")
        assert uploads == [b"video"]


@pytest.mark.asyncio
async def test_bluesky_expired_session_refreshes_and_rate_limits_are_retryable():
    def transport(request):
        if request.url.path.endswith("refreshSession"):
            assert request.headers["authorization"] == "Bearer refresh"
            return httpx.Response(200,json={"accessJwt":"new-access","refreshJwt":"new-refresh","did":"did:plc:404"})
        if request.headers.get("authorization") == "Bearer access":
            return httpx.Response(401,json={"error":"ExpiredToken"})
        return httpx.Response(429,json={"error":"RateLimitExceeded"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        provider = BlueskyProvider(http)
        creds = dict(CREDS)
        with pytest.raises(ProviderError) as limited:
            await provider.publish(creds,"Hello",[],"key",{})
        assert creds["accessJwt"] == "new-access"
        assert limited.value.reason == ErrorReason.RATE_LIMITED
        assert limited.value.retryable is True
