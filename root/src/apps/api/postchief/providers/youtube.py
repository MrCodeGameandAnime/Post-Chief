"""Resumable uploads: persist the session before sending video bytes."""
import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import httpx
from provider_contracts import Capabilities, Media, PublishResult, PublicationPending, ProviderError, ErrorReason
from postchief.providers.google import refresh_google

BASE = 'https://www.googleapis.com/youtube/v3/'
UPLOAD = 'https://www.googleapis.com/upload/youtube/v3/videos'
SCOPES = 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly'
CHUNK = 4 * 1024 * 1024


def channel_id(value):
    return isinstance(value, str) and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value) is not None


def video_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', value) is not None


def session_url(value):
    if not isinstance(value, str): return False
    parsed = urlsplit(value)
    return parsed.scheme == 'https' and parsed.netloc == 'www.googleapis.com' and parsed.path == '/upload/youtube/v3/videos' and bool(parsed.query) and not parsed.fragment


class YouTubeProvider:
    capabilities = Capabilities(text=False, video=True, analytics=True)
    limits = {'videos': 1, 'media_bytes': 80 * 1024 * 1024, 'title': 100,
        'description_utf8_bytes': 5000, 'visibility': ['private', 'unlisted', 'public'],
        'audience_declaration_required': True, 'upload': 'Resumable MP4'}
    # Public bytes always use the same persisted resumable session. An expired
    # session is reconciled, never replaced after bytes might have been accepted.
    idempotent = True

    def __init__(self, client, settings): self.http, self.settings = client, settings

    def validate(self, body: str, media: list[Media]):
        if len(media) != 1 or media[0].mime_type != 'video/mp4' or not 0 < media[0].byte_size <= 80 * 1024 * 1024:
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'YouTube requires one MP4 video, up to the current 80 MiB asset limit')
        if len(body.encode('utf-8')) > 5000 or '<' in body or '>' in body:
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'YouTube description must be at most 5,000 UTF-8 bytes and cannot contain angle brackets')

    def validate_options(self, options, title):
        options = options or {}
        title = options.get('title') or title
        if not isinstance(title, str) or not title.strip() or len(title) > 100 or '<' in title or '>' in title:
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Choose a YouTube title of 1–100 characters without angle brackets')
        if options.get('privacy_status', 'private') not in ('private', 'unlisted', 'public') or type(options.get('made_for_kids')) is not bool:
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Choose YouTube visibility and explicitly declare whether the video is made for kids')
        return {**options, 'title': title.strip(), 'privacy_status': options.get('privacy_status', 'private')}

    async def refresh_auth(self, credentials):
        await refresh_google(self.http, self.settings, credentials)

    async def request(self, method, url, credentials, **kwargs):
        try:
            response = await self.http.request(method, url,
                headers={'Authorization': 'Bearer ' + credentials['access_token'], **kwargs.pop('headers', {})},
                follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'YouTube request was interrupted; resume the existing upload', retryable=True)
        if response.status_code in (200, 201, 308): return response
        try: value = response.json()
        except ValueError: value = {}
        reasons = [item.get('reason') for item in value.get('error', {}).get('errors', []) if isinstance(item, dict)] if isinstance(value.get('error'), dict) else []
        if response.status_code == 401:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'YouTube authorization failed; reconnect')
        if response.status_code == 429 or any(r in ('quotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded', 'userRateLimitExceeded') for r in reasons):
            raise ProviderError(ErrorReason.RATE_LIMITED, 'YouTube quota or rate limit reached; retry later', retryable=True)
        if response.status_code >= 500:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'YouTube is unavailable; resume the existing upload', retryable=True)
        if response.status_code == 404 and url.startswith(UPLOAD):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube upload session expired; check the channel for an accepted video before retrying', uncertain=True)
        if response.status_code == 403:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'YouTube denied access; review scopes, channel access and project audit')
        raise ProviderError(ErrorReason.CONTENT_REJECTED, 'YouTube rejected this request; review video metadata and media')

    def accepted(self, response, state):
        try: value = response.json()
        except ValueError: value = {}
        if not video_id(value.get('id')):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube upload response has no valid video ID; reconcile the channel before retrying', uncertain=True)
        state['video_id'] = value['id']
        state['_public_metadata'] = {'video_id': value['id'], 'url': 'https://www.youtube.com/watch?v=' + value['id'],
            'requested_visibility': state['youtube']['privacy_status'], 'processing_status': 'processing'}
        raise PublicationPending(state, retry_after=15)

    async def publish(self, credentials, body, media, key, state):
        self.validate(body, media)
        options = self.validate_options(state.get('youtube'), '')
        if not channel_id(credentials.get('id')):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Reconnect the YouTube channel')
        if state.get('video_id'):
            response = await self.request('GET', BASE + 'videos', credentials,
                params={'part': 'snippet,status,processingDetails', 'id': state['video_id']})
            items = response.json().get('items', [])
            if len(items) != 1 or items[0].get('id') != state['video_id'] or items[0].get('snippet', {}).get('channelId') != credentials['id']:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube video is unavailable or belongs to another channel; reconcile the upload', uncertain=True)
            item = items[0]; status = item.get('status', {}); processing = item.get('processingDetails', {}).get('processingStatus')
            actual = status.get('privacyStatus')
            metadata = {**state['_public_metadata'], 'visibility': actual, 'processing_status': processing or status.get('uploadStatus')}
            state['_public_metadata'] = metadata
            if processing in ('failed', 'terminated') or status.get('uploadStatus') in ('failed', 'rejected', 'deleted'):
                raise ProviderError(ErrorReason.CONTENT_REJECTED, 'YouTube processing failed; review the existing video in YouTube Studio', uncertain=True)
            if (processing and processing != 'succeeded') or (not processing and status.get('uploadStatus') != 'processed'):
                raise PublicationPending(state, retry_after=30)
            if actual != options['privacy_status']:
                raise ProviderError(ErrorReason.PERMISSION_MISSING, 'YouTube retained a different visibility than requested; review the existing video and project audit before any new upload', uncertain=True)
            return PublishResult(state['video_id'], metadata['url'], state, metadata)
        path = Path(media[0].path)
        with path.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if path.stat().st_size != media[0].byte_size or (state.get('media_sha256') and state['media_sha256'] != digest):
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'Video bytes changed after the upload started; reconcile the session', uncertain=bool(state.get('upload_session')))
        total = media[0].byte_size
        if not state.get('upload_session'):
            response = await self.request('POST', UPLOAD, credentials,
                params={'uploadType': 'resumable', 'part': 'snippet,status'},
                headers={'X-Upload-Content-Length': str(total), 'X-Upload-Content-Type': media[0].mime_type},
                json={'snippet': {'title': options['title'], 'description': body, 'categoryId': '22'},
                    'status': {'privacyStatus': options['privacy_status'], 'selfDeclaredMadeForKids': options['made_for_kids']}})
            location = response.headers.get('location')
            if not session_url(location):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube returned an invalid upload session; no video bytes were sent')
            state.update(upload_session=location, media_sha256=digest, youtube=options)
            raise PublicationPending(state, retry_after=1)
        if not session_url(state['upload_session']):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Stored YouTube upload session is invalid; reconcile the upload', uncertain=True)
        # Query before every chunk, including recovery after a lost final response.
        response = await self.request('PUT', state['upload_session'], credentials,
            content=b'', headers={'Content-Length': '0', 'Content-Range': f'bytes */{total}'})
        if response.status_code != 308: self.accepted(response, state)
        received = response.headers.get('range')
        match = re.fullmatch(r'bytes=0-([0-9]+)', received) if received else None
        if received and not match:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube returned an invalid upload offset; reconcile the session', uncertain=True)
        offset = int(match[1]) + 1 if match else 0
        if offset >= total:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube accepted all bytes without confirming the video; reconcile the channel', uncertain=True)
        with path.open('rb') as handle:
            handle.seek(offset); chunk = handle.read(CHUNK)
        end = offset + len(chunk) - 1
        response = await self.request('PUT', state['upload_session'], credentials, content=chunk,
            headers={'Content-Type': media[0].mime_type, 'Content-Length': str(len(chunk)), 'Content-Range': f'bytes {offset}-{end}/{total}'})
        if response.status_code != 308: self.accepted(response, state)
        raise PublicationPending(state, retry_after=1)

    async def get_post_metrics(self, credentials, provider_id):
        if not video_id(provider_id):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'YouTube video identifier is invalid')
        response = await self.request('GET', BASE + 'videos', credentials,
            params={'part': 'snippet,statistics,status', 'id': provider_id})
        items = response.json().get('items', [])
        if len(items) != 1 or items[0].get('snippet', {}).get('channelId') != credentials.get('id'):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'YouTube video is unavailable for this channel')
        stats = items[0].get('statistics', {})
        result = {'provider': {'statistics': stats, 'visibility': items[0].get('status', {}).get('privacyStatus')}}
        for native, normalized in (('viewCount', 'views'), ('likeCount', 'likes'), ('commentCount', 'comments')):
            if isinstance(stats.get(native), str) and re.fullmatch(r'[0-9]{1,20}', stats[native]): result[normalized] = int(stats[native])
        end = datetime.now(ZoneInfo('America/Los_Angeles')).date(); start = end - timedelta(days=29)
        try:
            response = await self.request('GET', 'https://youtubeanalytics.googleapis.com/v2/reports', credentials,
                params={'ids': 'channel==' + credentials['id'], 'startDate': start.isoformat(), 'endDate': end.isoformat(),
                    'metrics': 'views,likes,comments,shares,estimatedMinutesWatched', 'filters': 'video==' + provider_id})
            report = response.json()
            result['provider']['analytics'] = {'start_date': start.isoformat(), 'requested_end_date': end.isoformat(),
                'timezone': 'America/Los_Angeles', 'reporting_can_lag': True, 'report': report}
            headers = report.get('columnHeaders', []); rows = report.get('rows', [])
            if len(rows) == 1 and len(rows[0]) == len(headers):
                values = {h.get('name'): value for h, value in zip(headers, rows[0]) if isinstance(h, dict)}
                for native, normalized, factor in (('shares', 'shares', 1), ('estimatedMinutesWatched', 'watch_time', 60)):
                    value = values.get(native)
                    if type(value) in (int, float) and math.isfinite(value) and value >= 0: result[normalized] = value * factor
        except ProviderError as error:
            # Preserve valid lifetime statistics when the separate Analytics API
            # is unavailable. Missing window metrics stay missing, never zero.
            result['provider']['analytics_error'] = error.to_dict()
        return result
