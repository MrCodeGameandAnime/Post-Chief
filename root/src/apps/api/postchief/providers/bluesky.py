import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlparse, quote
import httpx
import regex
from provider_contracts import Capabilities, Media, PublishResult, ProviderError, ErrorReason, PublicationPending
from postchief.models import now

PDS = "https://bsky.social"
VIDEO = "https://video.bsky.app"
APPVIEW = "https://public.api.bsky.app"
COLLECTION = "app.bsky.feed.post"


def facets(body: str):
    result = []
    for match in re.finditer(r"https?://[^\s]+",body):
        uri = match.group().rstrip(".,!?:;)")
        start = len(body[:match.start()].encode())
        result.append({"index":{"byteStart":start,"byteEnd":start+len(uri.encode())},"features":[{"$type":"app.bsky.richtext.facet#link","uri":uri}]})
    return result


class BlueskyProvider:
    capabilities = Capabilities(text=True,image=True,video=True,carousel=True,analytics=True)
    limits = {"text_graphemes":300,"text_bytes":3000,"images":4,"image_bytes":2_000_000,"video_bytes":80*1024*1024}
    idempotent = True

    def __init__(self, client: httpx.AsyncClient):
        self.http = client

    @staticmethod
    def pds(credentials: dict):
        url = credentials.get("pds",PDS).rstrip("/")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or parsed.port not in (None,443) or not (host == "bsky.social" or host.endswith(".bsky.network")):
            raise ProviderError(ErrorReason.PERMISSION_MISSING,"This release supports Bluesky-hosted PDS accounts")
        return url

    async def request(self, method: str, base: str, endpoint: str, token: str | None = None, missing=False, **kwargs):
        headers = kwargs.pop("headers",{})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self.http.request(method,f"{base}/xrpc/{endpoint}",headers=headers,**kwargs)
        except httpx.HTTPError:
            # Stable repository keys allow publication reconciliation after a timeout.
            raise ProviderError(ErrorReason.NETWORK_ERROR,"Bluesky request did not complete",retryable=True) from None
        try:
            data = response.json()
        except ValueError:
            raise ProviderError(ErrorReason.PROVIDER_ERROR,"Bluesky returned an invalid response",retryable=True) from None
        if missing and response.status_code in (400,404) and data.get("error") in ("RecordNotFound","NotFound"):
            return None
        if response.is_error:
            if endpoint == "app.bsky.video.uploadVideo" and data.get("jobStatus"):
                return data
            if response.status_code == 401:
                raise ProviderError(ErrorReason.AUTH_EXPIRED,"Reconnect your Bluesky account")
            if response.status_code == 403:
                raise ProviderError(ErrorReason.PERMISSION_MISSING,"Bluesky denied this operation")
            if response.status_code == 429:
                raise ProviderError(ErrorReason.RATE_LIMITED,"Bluesky rate limit reached",retryable=True)
            if response.status_code >= 500:
                raise ProviderError(ErrorReason.PROVIDER_ERROR,"Bluesky is temporarily unavailable",retryable=True)
            raise ProviderError(ErrorReason.CONTENT_REJECTED,"Bluesky rejected the request")
        return data

    async def auth(self, credentials: dict, method: str, endpoint: str, **kwargs):
        if not credentials.get("accessJwt"):
            raise ProviderError(ErrorReason.AUTH_REVOKED,"Reconnect your Bluesky account")
        base = self.pds(credentials)
        try:
            return await self.request(method,base,endpoint,credentials["accessJwt"],**kwargs)
        except ProviderError as error:
            if error.reason != ErrorReason.AUTH_EXPIRED or not credentials.get("refreshJwt"):
                raise
        session = await self.request("POST",base,"com.atproto.server.refreshSession",credentials["refreshJwt"])
        if session.get("did") != credentials["did"]:
            raise ProviderError(ErrorReason.AUTH_REVOKED,"Bluesky refreshed an unexpected identity")
        credentials.update({k:session[k] for k in ("accessJwt","refreshJwt")})
        return await self.request(method,base,endpoint,credentials["accessJwt"],**kwargs)

    async def connect(self, identifier: str, password: str):
        session = await self.request("POST",PDS,"com.atproto.server.createSession",json={"identifier":identifier,"password":password})
        if session.get("active") is False:
            raise ProviderError(ErrorReason.ACCOUNT_RESTRICTED,"Bluesky account is inactive")
        service = next((s for s in session.get("didDoc",{}).get("service",[]) if s.get("id","").endswith("#atproto_pds")),None)
        endpoint = service["serviceEndpoint"] if service else PDS
        creds = {k:session[k] for k in ("did","handle","accessJwt","refreshJwt")}
        creds["pds"] = endpoint
        self.pds(creds)
        creds["pds_audience"] = "did:web:" + urlparse(endpoint).netloc
        return creds

    def validate(self, body: str, media: list[Media]):
        if len(regex.findall(r"\X",body)) > 300 or len(body.encode()) > 3000:
            raise ProviderError(ErrorReason.CONTENT_REJECTED,"Bluesky allows 300 graphemes and 3000 UTF-8 bytes")
        videos = [m for m in media if m.mime_type == "video/mp4"]
        if videos:
            if len(media) != 1 or videos[0].byte_size > self.limits["video_bytes"]:
                raise ProviderError(ErrorReason.MEDIA_INVALID,"Choose one MP4 video within Post Chief's 80 MiB upload limit")
        elif len(media) > 4 or any(m.mime_type not in ("image/png","image/jpeg","image/webp","image/gif") or m.byte_size > 2_000_000 for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID,"Bluesky allows up to four raster images, each at most 2,000,000 bytes")
        if not body.strip() and not media:
            raise ProviderError(ErrorReason.CONTENT_REJECTED,"Post requires text or media")

    @staticmethod
    def record_key(key: str):
        # feed.post requires TID syntax. Encode a stable 63-bit digest in base32-sort.
        # The record's createdAt remains authoritative; record key timestamps are untrusted.
        alphabet = "234567abcdefghijklmnopqrstuvwxyz"
        value = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],"big") & ((1 << 63)-1)
        chars = []
        for _ in range(13):
            chars.append(alphabet[value & 31])
            value >>= 5
        return "".join(reversed(chars))

    @staticmethod
    def result(data: dict, credentials: dict, state: dict):
        uri = data.get("uri","")
        prefix = f'at://{credentials["did"]}/{COLLECTION}/'
        if not uri.startswith(prefix):
            raise ProviderError(ErrorReason.PROVIDER_ERROR,"Bluesky returned an unexpected post identifier",retryable=True)
        rkey = uri[len(prefix):]
        return PublishResult(uri,f'https://bsky.app/profile/{quote(credentials["did"],safe=":")}/post/{quote(rkey,safe="")}',{**state,"cid":data.get("cid")})

    async def video_blob(self, credentials: dict, media: Media, key: str, state: dict):
        if state.get("video_blob"):
            return state["video_blob"]
        if state.get("video_job_id"):
            data = await self.request("GET",VIDEO,"app.bsky.video.getJobStatus",params={"jobId":state["video_job_id"]})
        else:
            service = await self.auth(credentials,"GET","com.atproto.server.getServiceAuth",params={"aud":credentials["pds_audience"],"lxm":"com.atproto.repo.uploadBlob","exp":int(time.time())+1800})
            data = await self.request("POST",VIDEO,"app.bsky.video.uploadVideo",service["token"],params={"did":credentials["did"],"name":self.record_key(key)+".mp4"},content=Path(media.path).read_bytes(),headers={"Content-Type":"video/mp4"})
        job = data["jobStatus"]
        state["video_job_id"] = job["jobId"]
        if job["state"] == "JOB_STATE_FAILED":
            raise ProviderError(ErrorReason.MEDIA_INVALID,"Bluesky could not process this video")
        if job.get("blob"):
            state["video_blob"] = job["blob"]
            return job["blob"]
        raise PublicationPending(dict(state),retry_after=30)

    async def publish(self, credentials: dict, body: str, media: list[Media], key: str, state: dict) -> PublishResult:
        self.validate(body,media)
        state = dict(state)
        rkey = self.record_key(key)
        existing = await self.auth(credentials,"GET","com.atproto.repo.getRecord",missing=True,params={"repo":credentials["did"],"collection":COLLECTION,"rkey":rkey})
        if existing:
            return self.result(existing,credentials,state)
        state.setdefault("created_at",now().isoformat())
        record = {"$type":COLLECTION,"text":body,"createdAt":state["created_at"]}
        links = facets(body)
        if links:
            record["facets"] = links
        if media and media[0].mime_type == "video/mp4":
            record["embed"] = {"$type":"app.bsky.embed.video","video":await self.video_blob(credentials,media[0],key,state),"alt":media[0].alt_text}
        elif media:
            images = []
            for item in media:
                blob = await self.auth(credentials,"POST","com.atproto.repo.uploadBlob",content=Path(item.path).read_bytes(),headers={"Content-Type":item.mime_type})
                images.append({"image":blob["blob"],"alt":item.alt_text})
            record["embed"] = {"$type":"app.bsky.embed.images","images":images}
        try:
            data = await self.auth(credentials,"POST","com.atproto.repo.putRecord",json={"repo":credentials["did"],"collection":COLLECTION,"rkey":rkey,"swapRecord":None,"validate":True,"record":record})
        except ProviderError as error:
            if error.reason != ErrorReason.CONTENT_REJECTED:
                raise
            # A concurrent/repeated request may have already created this stable key.
            data = await self.auth(credentials,"GET","com.atproto.repo.getRecord",missing=True,params={"repo":credentials["did"],"collection":COLLECTION,"rkey":rkey})
            if not data:
                raise error
        return self.result(data,credentials,state)

    async def get_post_metrics(self, credentials: dict, provider_id: str):
        data = await self.request("GET",APPVIEW,"app.bsky.feed.getPosts",params={"uris":provider_id})
        if not data.get("posts"):
            raise ProviderError(ErrorReason.PROVIDER_ERROR,"Bluesky post is not indexed yet",retryable=True)
        post = data["posts"][0]
        names = {"likeCount":"likes","replyCount":"replies","repostCount":"reposts","quoteCount":"quotes"}
        return {name:post[key] for key,name in names.items() if isinstance(post.get(key),int)}

    async def delete(self, credentials: dict, provider_id: str):
        parts = provider_id.split("/")
        if len(parts) != 5 or parts[2] != credentials["did"] or parts[3] != COLLECTION:
            raise ProviderError(ErrorReason.CONTENT_REJECTED,"Invalid Bluesky post identifier")
        await self.auth(credentials,"POST","com.atproto.repo.deleteRecord",json={"repo":credentials["did"],"collection":COLLECTION,"rkey":parts[4]})
