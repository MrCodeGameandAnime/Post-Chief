"""Native Graph adapters with persisted preparation and publication intent.

The queue must save each PublicationPending state before resuming. A recovered
job at publish_intent requires reconciliation because Graph has no dedupe key.
"""
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlparse
import httpx
from provider_contracts import Capabilities, Media, PublishResult, PublicationPending, ProviderError, ErrorReason


def public_media(media):
    for item in media:
        url = urlparse(item.url or '')
        if url.scheme != 'https' or not url.hostname or url.hostname in ('localhost','127.0.0.1','::1') or url.username:
            raise ProviderError(ErrorReason.MEDIA_INVALID,'This platform requires a publicly reachable HTTPS media URL')


class GraphProvider:
    idempotent = False
    host = 'https://graph.facebook.com'
    limits = {}

    def __init__(self, client, settings=None):
        self.http, self.settings = client, settings
        self.version = settings.meta_api_version if settings else 'v25.0'

    async def request(self, method, path, credentials, *, public_write=False, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        headers['Authorization'] = 'Bearer ' + credentials.get('access_token','')
        params = dict(kwargs.pop('params',{}))
        if self.settings and self.host == 'https://graph.facebook.com':
            secret = self.settings.meta_client_secret.get_secret_value()
            if secret:
                params['appsecret_proof'] = hmac.new(secret.encode(),credentials['access_token'].encode(),hashlib.sha256).hexdigest()
        try:
            response = await self.http.request(method,f'{self.host}/{self.version}/{path}',headers=headers,params=params,**kwargs)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR,'Graph request failed',retryable=not public_write,uncertain=public_write)
        try: value = response.json()
        except ValueError:
            raise ProviderError(ErrorReason.PROVIDER_ERROR,'Graph returned an invalid response',retryable=not public_write,uncertain=public_write)
        if response.is_error or value.get('error'):
            error = value.get('error',{})
            code = error.get('code')
            if code == 190 or response.status_code == 401:
                raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect this account')
            if code in (4,17,32,613) or response.status_code == 429:
                raise ProviderError(ErrorReason.RATE_LIMITED,'Platform rate limit reached',True)
            if code in (10,200) or response.status_code == 403:
                raise ProviderError(ErrorReason.PERMISSION_MISSING,'Required platform permission is missing')
            transient = response.status_code >= 500 or error.get('is_transient',False)
            raise ProviderError(ErrorReason.PROVIDER_ERROR if transient else ErrorReason.CONTENT_REJECTED,
                                'Platform could not accept this request',retryable=transient and not public_write,uncertain=transient and public_write)
        return value

    async def delete(self, credentials, provider_id):
        await self.request('DELETE',provider_id,credentials)


class FacebookProvider(GraphProvider):
    capabilities = Capabilities(text=True,image=True,video=True,carousel=True,analytics=True)
    limits = {'text':63206,'images':10,'media_bytes':80*1024*1024}

    def validate(self, body, media):
        if len(body)>63206 or not (body.strip() or media):
            raise ProviderError(ErrorReason.CONTENT_REJECTED,'Facebook requires content within its text limit')
        if len(media)>10 or any(m.byte_size>80*1024*1024 for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'Facebook media exceeds the application limit')
        if any(m.mime_type == 'video/mp4' for m in media):
            if len(media)!=1: raise ProviderError(ErrorReason.MEDIA_INVALID,'Publish one video per Facebook publication')
            public_media(media)
        elif any(m.mime_type not in ('image/jpeg','image/png','image/webp') for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'Facebook accepts raster images or one MP4')

    async def publish(self, credentials, body, media, key, state):
        self.validate(body,media)
        state = dict(state)
        page = credentials['id']
        if state.get('provider_id'):
            return PublishResult(state['provider_id'],state=state)
        if media and media[0].mime_type == 'video/mp4':
            if state.get('phase') == 'video_processing':
                value = await self.request('GET',state['video_id'],credentials,params={'fields':'status'})
                status = value.get('status',{})
                if status.get('video_status') == 'error' or status.get('publishing_phase',{}).get('status') == 'error':
                    raise ProviderError(ErrorReason.MEDIA_INVALID,'Facebook video processing failed')
                if status.get('publishing_phase',{}).get('status') != 'complete':
                    raise PublicationPending(state,30)
                state.update(provider_id=state['video_id'],phase='published')
                return PublishResult(state['video_id'],f'https://www.facebook.com/reel/{state["video_id"]}',state)
            if not state.get('video_id'):
                value = await self.request('POST',f'{page}/video_reels',credentials,data={'upload_phase':'start'})
                state.update(video_id=value['video_id'],phase='video_upload')
                raise PublicationPending(state,1)
            if state.get('phase') == 'video_upload':
                # Construct the trusted upload host instead of following response URLs.
                try:
                    response = await self.http.post(f'https://rupload.facebook.com/video-upload/{self.version}/{state["video_id"]}',
                        headers={'Authorization':'OAuth '+credentials['access_token'],'file_url':media[0].url})
                    response.raise_for_status()
                except httpx.HTTPError:
                    raise ProviderError(ErrorReason.NETWORK_ERROR,'Facebook video upload failed',True)
                state['phase']='publish_intent'
                raise PublicationPending(state,1)
            value = await self.request('POST',f'{page}/video_reels',credentials,public_write=True,
                data={'upload_phase':'finish','video_state':'PUBLISHED','video_id':state['video_id'],'description':body})
            if not value.get('success'):
                raise ProviderError(ErrorReason.PROVIDER_ERROR,'Facebook video publication was not confirmed',uncertain=True)
            state['phase']='video_processing'
            raise PublicationPending(state,30)
        photo_ids = list(state.get('photo_ids',[]))
        if len(photo_ids)<len(media):
            item = media[len(photo_ids)]
            with Path(item.path).open('rb') as file:
                value = await self.request('POST',f'{page}/photos',credentials,data={'published':'false'},files={'source':(Path(item.path).name,file,item.mime_type)})
            photo_ids.append(value['id'])
            state.update(photo_ids=photo_ids,phase='photos')
            raise PublicationPending(state,1)
        if state.get('phase') != 'publish_intent':
            state['phase']='publish_intent'
            raise PublicationPending(state,1)
        data = {'message':body}
        if photo_ids: data['attached_media'] = json.dumps([{'media_fbid':id} for id in photo_ids])
        value = await self.request('POST',f'{page}/feed',credentials,public_write=True,data=data)
        if not value.get('id'): raise ProviderError(ErrorReason.PROVIDER_ERROR,'Facebook publication ID is missing',uncertain=True)
        state.update(provider_id=value['id'],phase='published')
        return PublishResult(value['id'],f'https://www.facebook.com/{value["id"]}',state)

    async def get_post_metrics(self, credentials, provider_id):
        data = await self.request('GET',provider_id,credentials,params={'fields':'reactions.summary(true),comments.summary(true),shares'})
        return {'reactions':data.get('reactions',{}).get('summary',{}).get('total_count'),
                'comments':data.get('comments',{}).get('summary',{}).get('total_count'),
                'shares':data.get('shares',{}).get('count'),'provider':data}


class InstagramProvider(GraphProvider):
    capabilities = Capabilities(text=False,image=True,video=True,carousel=True,analytics=True)
    limits = {'text':2200,'carousel':10,'media_bytes':80*1024*1024}
    edge, publish_edge, status_field = 'media','media_publish','status_code'

    def validate(self, body, media):
        if len(body)>self.limits['text']:
            raise ProviderError(ErrorReason.CONTENT_REJECTED,'Caption exceeds platform text limit')
        if (not media and not self.capabilities.text) or len(media)>self.limits['carousel']:
            raise ProviderError(ErrorReason.MEDIA_INVALID,'This publication requires supported media within the carousel limit')
        if not body.strip() and not media:
            raise ProviderError(ErrorReason.CONTENT_REJECTED,'Publication requires text or media')
        allowed = ('image/jpeg','video/mp4') if self.edge == 'media' else ('image/jpeg','image/png','video/mp4')
        if any(m.mime_type not in allowed or m.byte_size>80*1024*1024 for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'Use JPEG images or supported MP4 video within the application size limit')
        public_media(media)

    def container_data(self, body, media, child=False):
        caption = 'caption' if self.edge == 'media' else 'text'
        data = {} if child else {caption:body}
        if not media: data['media_type']='TEXT'
        elif media[0].mime_type == 'video/mp4':
            data.update(media_type=('VIDEO' if child or self.edge == 'threads' else 'REELS'),video_url=media[0].url)
        else:
            data['image_url']=media[0].url
            if self.edge == 'threads': data['media_type']='IMAGE'
            if media[0].alt_text: data['alt_text']=media[0].alt_text
        if child: data['is_carousel_item']='true'
        return data

    async def publish(self, credentials, body, media, key, state):
        self.validate(body,media)
        state = dict(state)
        if state.get('provider_id'): return PublishResult(state['provider_id'],state=state)
        children = list(state.get('children',[]))
        if len(media)>1 and len(children)<len(media):
            value = await self.request('POST',f'{credentials["id"]}/{self.edge}',credentials,
                data=self.container_data('',[media[len(children)]],True))
            children.append(value['id'])
            state.update(children=children,phase='children')
            raise PublicationPending(state,1)
        if not state.get('container_id'):
            if len(media)>1:
                # Every child must finish processing before creating its parent.
                for child in children:
                    await self.ready(credentials,child,state)
                data = {'media_type':'CAROUSEL','children':','.join(children),('caption' if self.edge=='media' else 'text'):body}
            else: data = self.container_data(body,media)
            value = await self.request('POST',f'{credentials["id"]}/{self.edge}',credentials,data=data)
            state.update(container_id=value['id'],phase='processing')
            raise PublicationPending(state,5)
        if state.get('phase') != 'publish_intent':
            await self.ready(credentials,state['container_id'],state)
            state['phase']='publish_intent'
            raise PublicationPending(state,1)
        value = await self.request('POST',f'{credentials["id"]}/{self.publish_edge}',credentials,public_write=True,data={'creation_id':state['container_id']})
        if not value.get('id'): raise ProviderError(ErrorReason.PROVIDER_ERROR,'Platform publication ID is missing',uncertain=True)
        state.update(provider_id=value['id'],phase='published')
        return PublishResult(value['id'],state=state)

    async def ready(self, credentials, container_id, state):
        data = await self.request('GET',container_id,credentials,params={'fields':self.status_field})
        status = data.get(self.status_field)
        if status in ('ERROR','EXPIRED'):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'Platform media processing failed or expired')
        if status == 'PUBLISHED':
            raise ProviderError(ErrorReason.PROVIDER_ERROR,'Container is already published; reconcile its publication ID',uncertain=True)
        if status != 'FINISHED': raise PublicationPending(state,30)

    async def get_post_metrics(self, credentials, provider_id):
        data = await self.request('GET',provider_id,credentials,params={'fields':'like_count,comments_count,permalink'})
        return {'likes':data.get('like_count'),'comments':data.get('comments_count'),'provider':data}


class ThreadsProvider(InstagramProvider):
    host = 'https://graph.threads.net'
    capabilities = Capabilities(text=True,image=True,video=True,carousel=True,analytics=True)
    limits = {'text':500,'carousel':20,'media_bytes':80*1024*1024}
    edge, publish_edge, status_field = 'threads','threads_publish','status'

    def __init__(self, client, settings=None):
        super().__init__(client,settings)
        self.version='v1.0'

    async def refresh_auth(self, credentials):
        # Only unexpired long-lived tokens can be refreshed.
        try:
            response=await self.http.get(self.host+'/refresh_access_token',params={'grant_type':'th_refresh_token'},
                headers={'Authorization':'Bearer '+credentials['access_token']})
            data=response.json()
        except (httpx.HTTPError,ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR,'Threads token refresh failed',True)
        if response.is_error or not data.get('access_token'):
            raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect Threads')
        from datetime import datetime, timedelta, timezone
        credentials.update(access_token=data['access_token'],expires_at=(datetime.now(timezone.utc)+timedelta(seconds=int(data['expires_in']))).isoformat())
        return credentials

    async def get_post_metrics(self, credentials, provider_id):
        data = await self.request('GET',f'{provider_id}/insights',credentials,params={'metric':'views,likes,replies,reposts,quotes'})
        result = {'provider':data}
        mapping = {'replies':'comments','reposts':'shares'}
        for metric in data.get('data',[]):
            values=metric.get('values',[])
            if values: result[mapping.get(metric['name'],metric['name'])]=values[-1].get('value')
        return result
