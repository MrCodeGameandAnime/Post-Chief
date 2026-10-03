"""TikTok Login Kit, inbox video handoff and native Display API counters."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import math
import re
import httpx
from provider_contracts import Capabilities, PublicationPending, PublishResult, ProviderError, ErrorReason

BASE = 'https://open.tiktokapis.com/v2/'
SCOPES = 'user.info.basic,user.info.profile,video.list,video.upload'


def tokens(value, previous=None):
    previous = previous or {}
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) and value[k] for k in ('access_token','refresh_token','open_id')) or type(value.get('expires_in')) is not int or not 0 < value['expires_in'] <= 366 * 86400:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok did not return a renewable grant; reconnect')
    if previous.get('id') and previous['id'] != value['open_id']:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok renewal returned a different creator; reconnect')
    return {**previous, 'id': value['open_id'], 'access_token': value['access_token'], 'refresh_token': value['refresh_token'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=value['expires_in'])).isoformat(),
        'scopes': value.get('scope','').split(',')}


def mp4_duration(path):
    """Read the bounded movie header without trusting user-supplied duration."""
    with Path(path).open('rb') as handle:
        total = handle.seek(0, 2)
        def atoms(start, end):
            position = start
            for _ in range(10000):
                if position + 8 > end: return
                handle.seek(position); header = handle.read(8)
                size = int.from_bytes(header[:4],'big'); kind = header[4:]; offset = 8
                if size == 1:
                    size = int.from_bytes(handle.read(8),'big'); offset = 16
                elif size == 0: size = end - position
                if size < offset or position + size > end: return
                yield kind, position + offset, position + size
                position += size
        for kind, start, end in atoms(0,total):
            if kind != b'moov': continue
            for child, payload, stop in atoms(start,end):
                if child != b'mvhd': continue
                handle.seek(payload); data = handle.read(min(32,stop-payload))
                version = data[0] if data else -1
                if version == 0 and len(data) >= 20:
                    scale = int.from_bytes(data[12:16],'big'); ticks = int.from_bytes(data[16:20],'big')
                elif version == 1 and len(data) >= 32:
                    scale = int.from_bytes(data[20:24],'big'); ticks = int.from_bytes(data[24:32],'big')
                else: break
                if scale and ticks: return ticks / scale
    raise ProviderError(ErrorReason.MEDIA_INVALID, 'TikTok requires an MP4 with a readable movie duration')


class TikTokProvider:
    capabilities = Capabilities(text=False, video=True, analytics=True)
    limits = {'videos':1, 'media_bytes':80 * 1024 * 1024, 'duration_seconds':600,
        'delivery':'TikTok inbox; creator completes native posting', 'owner_consent_required':True}
    idempotent = False

    def __init__(self, client, settings=None): self.http,self.settings=client,settings

    def validate(self, body, media):
        if len(media)!=1 or media[0].mime_type!='video/mp4' or not 0 < media[0].byte_size <= 80 * 1024 * 1024:
            raise ProviderError(ErrorReason.MEDIA_INVALID,'TikTok inbox uploads require one MP4 up to 80 MiB')
        duration=mp4_duration(media[0].path)
        if not math.isfinite(duration) or not 0 < duration <= 600:
            raise ProviderError(ErrorReason.MEDIA_INVALID,'TikTok inbox videos must be at most 10 minutes; trim further in TikTok if your account requires it')

    def validate_options(self, options, title=''):
        if not options or options.get('consent_to_inbox') is not True:
            raise ProviderError(ErrorReason.PERMISSION_MISSING,'Review the video and consent to sending it to your TikTok inbox')
        return {'consent_to_inbox':True}

    async def request(self, method, path, credentials=None, *, public_write=False, **kwargs):
        headers={'Authorization':'Bearer '+credentials['access_token']} if credentials else {}
        try:
            response=await self.http.request(method,BASE+path,headers=headers,follow_redirects=False,**kwargs)
            value=response.json()
        except (httpx.HTTPError,ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR,'TikTok request could not be confirmed',retryable=not public_write,uncertain=public_write)
        error=value.get('error',{}) if isinstance(value,dict) else {}
        code=error.get('code') if isinstance(error,dict) else error
        if response.status_code==401 or code in ('access_token_invalid','invalid_grant'):
            raise ProviderError(ErrorReason.AUTH_REVOKED,'TikTok authorization failed; reconnect')
        if response.status_code==429 or code=='rate_limit_exceeded':
            raise ProviderError(ErrorReason.RATE_LIMITED,'TikTok is rate limited; retry later',retryable=True)
        if response.status_code>=500:
            raise ProviderError(ErrorReason.NETWORK_ERROR,'TikTok is unavailable',retryable=not public_write,uncertain=public_write)
        if response.is_error or code not in ('ok',None):
            reason=ErrorReason.PERMISSION_MISSING if code in ('scope_not_authorized','url_ownership_unverified') else ErrorReason.CONTENT_REJECTED
            raise ProviderError(reason,'TikTok rejected the operation; review app scopes, verified media URL and creator eligibility')
        if not isinstance(value,dict): raise ProviderError(ErrorReason.PROVIDER_ERROR,'TikTok returned an invalid response',uncertain=public_write)
        return value

    async def refresh_auth(self, credentials):
        value=await self.request('POST','oauth/token/',data={'client_key':self.settings.tiktok_client_key,
            'client_secret':self.settings.tiktok_client_secret.get_secret_value(),'grant_type':'refresh_token',
            'refresh_token':credentials['refresh_token']},public_write=True)
        credentials.update(tokens(value,credentials))

    async def profile(self, credentials):
        value=await self.request('GET','user/info/',credentials,params={'fields':'open_id,display_name,username'})
        user=value.get('data',{}).get('user',{})
        if user.get('open_id')!=credentials.get('id') or not isinstance(user.get('display_name'),str):
            raise ProviderError(ErrorReason.AUTH_REVOKED,'TikTok creator identity could not be confirmed; reconnect')
        return {k:user[k] for k in ('open_id','display_name','username') if k in user}

    async def publish(self, credentials, body, media, key, state):
        self.validate(body,media); self.validate_options(state.get('tiktok'))
        if state.get('publish_id'):
            value=await self.request('POST','post/publish/status/fetch/',credentials,json={'publish_id':state['publish_id']})
            data=value.get('data',{}); status=data.get('status')
            metadata={**state.get('_public_metadata',{}),'processing_status':status}
            state['_public_metadata']=metadata
            if status=='FAILED':
                raise ProviderError(ErrorReason.CONTENT_REJECTED,'TikTok could not complete this inbox upload; review the native task before retrying',uncertain=True)
            if status=='SEND_TO_USER_INBOX':
                metadata['delivery']='inbox'; metadata['visibility']='Not posted yet'
                raise PublicationPending(state,retry_after=300,status='awaiting_owner')
            if status=='PUBLISH_COMPLETE':
                ids=data.get('publicaly_available_post_id',[])
                ids=[str(i) for i in ids if type(i) in (int,str) and re.fullmatch(r'[0-9]{1,30}',str(i))]
                metadata.update(delivery='native_post_completed',visibility='public' if ids else 'Not reported',post_ids=ids)
                username=state.get('creator',{}).get('username')
                url=f'https://www.tiktok.com/@{username}/video/{ids[0]}' if ids and isinstance(username,str) and re.fullmatch(r'[A-Za-z0-9_.]{1,64}',username) else None
                metadata['provider_id_kind']='post' if ids else 'publish_task'
                return PublishResult(ids[0] if ids else state['publish_id'],url,state,metadata)
            if status not in ('PROCESSING_UPLOAD','PROCESSING_DOWNLOAD'):
                raise ProviderError(ErrorReason.PROVIDER_ERROR,'TikTok returned an unknown task status; reconcile this inbox handoff',uncertain=True)
            raise PublicationPending(state,retry_after=30)
        if state.get('phase')!='publish_intent':
            creator=await self.profile(credentials)
            state.update(creator=creator,phase='publish_intent')
            raise PublicationPending(state,retry_after=1)
        parsed=urlsplit(media[0].url or '')
        expected=urlsplit(self.settings.public_url)
        if parsed.scheme!='https' or parsed.netloc!=expected.netloc or not parsed.path.startswith('/api/media/'):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'TikTok requires a public HTTPS media URL on your verified domain')
        value=await self.request('POST','post/publish/inbox/video/init/',credentials,public_write=True,
            json={'source_info':{'source':'PULL_FROM_URL','video_url':media[0].url}})
        publish_id=value.get('data',{}).get('publish_id')
        if not isinstance(publish_id,str) or not 1<=len(publish_id)<=64:
            raise ProviderError(ErrorReason.PROVIDER_ERROR,'TikTok accepted no valid task ID; reconcile the creator inbox',uncertain=True)
        state.update(publish_id=publish_id,phase='processing',_public_metadata={'delivery':'inbox_upload',
            'creator':state['creator']['display_name'],'processing_status':'PROCESSING_DOWNLOAD'})
        raise PublicationPending(state,retry_after=30)

    async def get_post_metrics(self, credentials, provider_id):
        if not re.fullmatch(r'[0-9]{1,30}',provider_id):
            raise ProviderError(ErrorReason.PERMISSION_MISSING,'TikTok did not return a public video ID; native counters are unavailable')
        value=await self.request('POST','video/query/',credentials,params={'fields':'id,share_url,view_count,like_count,comment_count,share_count'},
            json={'filters':{'video_ids':[provider_id]}})
        videos=value.get('data',{}).get('videos',[])
        if len(videos)!=1 or str(videos[0].get('id'))!=provider_id:
            raise ProviderError(ErrorReason.PERMISSION_MISSING,'TikTok public video counters are not available')
        result={'provider':videos[0]}
        for native,normalized in (('view_count','views'),('like_count','likes'),('comment_count','comments'),('share_count','shares')):
            value=videos[0].get(native)
            if type(value) is int and value>=0: result[normalized]=value
        return result
