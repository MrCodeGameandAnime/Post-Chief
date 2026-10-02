from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
import httpx
from postchief.vault import Vault
from provider_contracts import Capabilities, PublishResult, PublicationPending, ProviderError, ErrorReason


class LinkedInProvider:
    capabilities=Capabilities(text=True,image=True,video=True,carousel=True,analytics=True)
    limits={'text':3000,'images':20,'media_bytes':80*1024*1024,'multi_image_type':'organic_multiImage'}
    idempotent=False

    def __init__(self,client,settings=None):
        self.http,self.settings=client,settings
        self.version=settings.linkedin_api_version if settings else '202609'

    def headers(self,credentials):
        return {'Authorization':'Bearer '+credentials['access_token'],'Linkedin-Version':self.version,'X-Restli-Protocol-Version':'2.0.0'}

    async def request(self,method,path,credentials,public_write=False,**kwargs):
        try:
            response=await self.http.request(method,'https://api.linkedin.com/rest/'+path,headers=self.headers(credentials),**kwargs)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR,'LinkedIn request failed',not public_write,public_write)
        if response.status_code==401: raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect LinkedIn')
        if response.status_code==403: raise ProviderError(ErrorReason.PERMISSION_MISSING,'LinkedIn requires additional permission or an approved Page role')
        if response.status_code==429: raise ProviderError(ErrorReason.RATE_LIMITED,'LinkedIn rate limit reached',True)
        if response.is_error:
            transient=response.status_code>=500
            raise ProviderError(ErrorReason.PROVIDER_ERROR if transient else ErrorReason.CONTENT_REJECTED,
                'LinkedIn could not accept this request',transient and not public_write,transient and public_write)
        return response

    def validate(self,body,media):
        if len(body)>3000 or not (body.strip() or media):
            raise ProviderError(ErrorReason.CONTENT_REJECTED,'LinkedIn requires content within the 3000-character limit')
        if len(media)>20 or any(m.byte_size>80*1024*1024 for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn media exceeds the application limit')
        if any(m.mime_type=='video/mp4' for m in media):
            if len(media)!=1: raise ProviderError(ErrorReason.MEDIA_INVALID,'Use one video per LinkedIn publication')
        elif any(m.mime_type not in ('image/jpeg','image/png','image/gif') for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn supports JPEG, PNG, GIF or one MP4')

    async def upload(self,url,content,credentials,video=False):
        parsed=urlparse(url)
        host=parsed.hostname or ''
        if parsed.scheme!='https' or parsed.username or parsed.port not in (None,443) or not (host=='www.linkedin.com' or host.endswith('.linkedin.com') or host.endswith('.licdn.com')):
            raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn returned an untrusted upload host')
        headers={'Content-Type':'application/octet-stream'}
        if not video: headers['Authorization']='Bearer '+credentials['access_token']
        try:
            response=await self.http.put(url,content=content,headers=headers,follow_redirects=False)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR,'LinkedIn media upload failed',True)
        if response.is_redirect: raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn upload redirect was rejected')
        if response.is_error: raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn rejected this upload',response.status_code>=500)
        return response

    def vault(self):
        if not self.settings: raise ProviderError(ErrorReason.PROVIDER_ERROR,'Encryption settings required for resumable uploads')
        return Vault(self.settings.encryption_key.get_secret_value())

    async def publish(self,credentials,body,media,key,state):
        self.validate(body,media)
        state=dict(state)
        if state.get('provider_id'):
            id=state['provider_id']
            return PublishResult(id,f'https://www.linkedin.com/feed/update/{id}/',state)
        author=credentials['author']
        if media and media[0].mime_type=='video/mp4':
            if not state.get('video'):
                response=await self.request('POST','videos',credentials,params={'action':'initializeUpload'},json={'initializeUploadRequest':{
                    'owner':author,'fileSizeBytes':media[0].byte_size,'uploadCaptions':False,'uploadThumbnail':False}})
                value=response.json()['value']
                state.update(video=value['video'],sealed_upload=self.vault().encrypt(value),parts=[],phase='video_upload')
                raise PublicationPending(state,1)
            if state.get('phase')=='video_upload':
                value=self.vault().decrypt(state['sealed_upload'])
                instructions=value['uploadInstructions']; parts=list(state.get('parts',[]))
                if len(parts)<len(instructions):
                    instruction=instructions[len(parts)]
                    start,end=int(instruction['firstByte']),int(instruction['lastByte'])
                    if start<0 or end<start or end>=media[0].byte_size or end-start+1>80*1024*1024:
                        raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn returned an invalid video byte range')
                    with Path(media[0].path).open('rb') as file:
                        file.seek(start); chunk=file.read(end-start+1)
                    if len(chunk)!=end-start+1: raise ProviderError(ErrorReason.MEDIA_INVALID,'Video bytes changed during upload')
                    response=await self.upload(instruction['uploadUrl'],chunk,credentials,True)
                    etag=response.headers.get('etag')
                    if not etag: raise ProviderError(ErrorReason.PROVIDER_ERROR,'LinkedIn upload part ID missing',True)
                    parts.append(etag.strip('"'))
                    state['parts']=parts
                    raise PublicationPending(state,1)
                await self.request('POST','videos',credentials,params={'action':'finalizeUpload'},json={'finalizeUploadRequest':{
                    'video':state['video'],'uploadToken':value.get('uploadToken',''),'uploadedPartIds':parts}})
                state.pop('sealed_upload',None)
                state['phase']='media_uploaded'
                raise PublicationPending(state,30)
        else:
            images=list(state.get('images',[]))
            if len(images)<len(media):
                item=media[len(images)]
                response=await self.request('POST','images',credentials,params={'action':'initializeUpload'},json={'initializeUploadRequest':{'owner':author}})
                value=response.json()['value']
                with Path(item.path).open('rb') as file: content=file.read()
                await self.upload(value['uploadUrl'],content,credentials)
                images.append(value['image']); state.update(images=images,phase='media_uploaded')
                raise PublicationPending(state,10)
        if state.get('phase')!='publish_intent':
            # Versioned image GET is forbidden with only w_member_social. Upload
            # checkpoints give processing time; only poll when read is supported.
            scopes=set(credentials.get('scopes',[]))
            can_read=bool(scopes & {'rw_ads','w_organization_social','w_power_creators'})
            if can_read:
                ids=[state['video']] if state.get('video') else state.get('images',[])
                edge='videos' if state.get('video') else 'images'
                for id in ids:
                    response=await self.request('GET',edge+'/'+quote(id,safe=''),credentials)
                    status=response.json().get('status')
                    if status=='PROCESSING_FAILED': raise ProviderError(ErrorReason.MEDIA_INVALID,'LinkedIn media processing failed')
                    if status!='AVAILABLE': raise PublicationPending(state,30)
            state['phase']='publish_intent'
            raise PublicationPending(state,1)
        payload={'author':author,'commentary':body,'visibility':'PUBLIC','distribution':{'feedDistribution':'MAIN_FEED',
            'targetEntities':[],'thirdPartyDistributionChannels':[]},'lifecycleState':'PUBLISHED','isReshareDisabledByAuthor':False}
        if state.get('video'): payload['content']={'media':{'id':state['video']}}
        elif len(state.get('images',[]))==1: payload['content']={'media':{'id':state['images'][0]}}
        elif state.get('images'): payload['content']={'multiImage':{'images':[{'id':id,'altText':media[i].alt_text} for i,id in enumerate(state['images'])]}}
        response=await self.request('POST','posts',credentials,public_write=True,json=payload)
        id=response.headers.get('x-restli-id')
        if not id: raise ProviderError(ErrorReason.PROVIDER_ERROR,'LinkedIn publication ID missing',uncertain=True)
        state.update(provider_id=id,phase='published')
        return PublishResult(id,f'https://www.linkedin.com/feed/update/{id}/',state)

    async def get_post_metrics(self,credentials,provider_id):
        scopes=set(credentials.get('scopes',[]))
        if credentials['author'].startswith('urn:li:organization:'):
            if 'r_organization_social' not in scopes: raise ProviderError(ErrorReason.PERMISSION_MISSING,'LinkedIn organization analytics permission required')
            kind='ugcPosts' if ':ugcPost:' in provider_id else 'shares'
            response=await self.request('GET','organizationalEntityShareStatistics',credentials,params={'q':'organizationalEntity',
                'organizationalEntity':credentials['author'],kind:f'List({provider_id})'})
            data=response.json(); elements=data.get('elements',[])
            stats=elements[0].get('totalShareStatistics',{}) if elements else {}
            return {'impressions':stats.get('impressionCount'),'reach':stats.get('uniqueImpressionsCount'),'likes':stats.get('likeCount'),
                'comments':stats.get('commentCount'),'shares':stats.get('shareCount'),'clicks':stats.get('clickCount'),'provider':data}
        if 'r_member_postAnalytics' not in scopes:
            raise ProviderError(ErrorReason.PERMISSION_MISSING,'LinkedIn member analytics requires approved r_member_postAnalytics access')
        result={'provider':{}}
        kind='ugc' if ':ugcPost:' in provider_id else 'share'
        for metric,field in [('IMPRESSION','impressions'),('MEMBERS_REACHED','reach'),('REACTION','reactions'),('COMMENT','comments'),('RESHARE','shares'),('LINK_CLICKS','clicks')]:
            response=await self.request('GET','memberCreatorPostAnalytics',credentials,params={'q':'entity','entity':f'({kind}:{provider_id})','queryType':metric,'aggregation':'TOTAL'})
            data=response.json(); result['provider'][metric]=data
            if data.get('elements'): result[field]=data['elements'][0].get('count')
        return result

    async def delete(self,credentials,provider_id):
        await self.request('DELETE','posts/'+quote(provider_id,safe=''),credentials)

    async def organizations(self,credentials):
        if 'w_organization_social' not in credentials.get('scopes',[]):
            raise ProviderError(ErrorReason.PERMISSION_MISSING,'Enable approved LinkedIn organization permissions and reconnect')
        organizations={}
        for start in range(0,10000,100):
            response=await self.request('GET','organizationAcls',credentials,params={'q':'roleAssignee','state':'APPROVED','count':100,'start':start})
            data=response.json()
            for acl in data.get('elements',[]):
                urn=acl.get('organizationTarget',acl.get('organization',''))
                if (acl.get('state')=='APPROVED' and acl.get('role') in ('ADMINISTRATOR','DIRECT_SPONSORED_CONTENT_POSTER','CONTENT_ADMIN')
                    and acl.get('roleAssignee')==credentials['author'] and urn.startswith('urn:li:organization:') and urn.rsplit(':',1)[-1].isdigit()):
                    organizations[urn]=urn
            if not any(link.get('rel')=='next' for link in data.get('paging',{}).get('links',[])): break
        result=[]
        for urn in organizations:
            response=await self.request('GET','organizations/'+urn.rsplit(':',1)[-1],credentials)
            result.append({'id':urn,'name':response.json().get('localizedName',urn)})
        return result

    async def refresh_auth(self,credentials):
        if not credentials.get('refresh_token') or not self.settings:
            raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect LinkedIn; this app has no programmatic refresh token')
        try:
            response=await self.http.post('https://www.linkedin.com/oauth/v2/accessToken',data={
                'grant_type':'refresh_token','refresh_token':credentials['refresh_token'],
                'client_id':self.settings.linkedin_client_id,'client_secret':self.settings.linkedin_client_secret.get_secret_value()})
            value=response.json()
        except (httpx.HTTPError,ValueError): raise ProviderError(ErrorReason.NETWORK_ERROR,'LinkedIn refresh failed',True)
        if response.is_error or not value.get('access_token'): raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect LinkedIn')
        credentials.update(access_token=value['access_token'],expires_at=(datetime.now(timezone.utc)+timedelta(seconds=int(value['expires_in']))).isoformat())
        if value.get('refresh_token'): credentials['refresh_token']=value['refresh_token']
        return credentials
