"""X API v2: owner OAuth, image preparation and persisted public-write intent."""
import base64
import hashlib
import hmac
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from provider_contracts import Capabilities, PublishResult, PublicationPending, ProviderError, ErrorReason

SCOPES = 'tweet.read tweet.write users.read media.write offline.access'
BASE = 'https://api.x.com/2/'


def pkce_verifier(settings, state):
    # Server-keyed derivation binds the verifier to one random, single-use state.
    value = hmac.new(settings.signing_key.get_secret_value().encode(),
                     ('postchief:x:pkce:' + state).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def token_auth(settings):
    return httpx.BasicAuth(settings.x_client_id, settings.x_client_secret.get_secret_value())


def token_credentials(value):
    if not isinstance(value, dict) or not isinstance(value.get('access_token'), str) or not value['access_token']:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'X did not return an access token; reconnect')
    seconds = value.get('expires_in')
    if type(seconds) is not int or not 0 < seconds <= 365 * 86400:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'X returned an invalid token lifetime; reconnect')
    result = {'access_token': value['access_token'],
              'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}
    if isinstance(value.get('refresh_token'), str) and value['refresh_token']:
        result['refresh_token'] = value['refresh_token']
    return result


def text_length(body):
    """Conservative bound, not a full twitter-text parser (complex emoji overcount)."""
    def weight(text):
        return sum(1 if ord(c) <= 0x10ff or 0x2000 <= ord(c) <= 0x200d
                   or 0x2010 <= ord(c) <= 0x201f or 0x2032 <= ord(c) <= 0x2037 else 2 for c in text)
    body = unicodedata.normalize('NFC', body)
    total, end = 0, 0
    for match in re.finditer(r'https?://[^\s]+', body):
        total += weight(body[end:match.start()]) + max(23, weight(match.group()))
        end = match.end()
    return total + weight(body[end:])


def numeric_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]{1,19}', value) is not None


class XProvider:
    capabilities = Capabilities(text=True, image=True, carousel=True, analytics=True)
    limits = {'text': 280, 'images': 4, 'media_bytes': 5_000_000,
              'text_counting': 'conservative weighted bound', 'analytics_refresh': 'manual'}
    idempotent = False

    def __init__(self, client, settings=None):
        self.http, self.settings = client, settings

    async def request(self, method, path, credentials, *, public_write=False, **kwargs):
        try:
            response = await self.http.request(method, BASE + path,
                headers={'Authorization': 'Bearer ' + credentials['access_token']},
                follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'X request failed',
                                retryable=not public_write, uncertain=public_write)
        if response.status_code == 401:
            raise ProviderError(ErrorReason.AUTH_EXPIRED, 'Reconnect X')
        if response.status_code == 402:
            raise ProviderError(ErrorReason.ACCOUNT_RESTRICTED, 'Review X API credits and spending limit')
        if response.status_code == 403:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Review X app permissions and account access')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'X rate limit reached', retryable=True)
        if response.is_error or response.is_redirect:
            transient = response.status_code >= 500
            raise ProviderError(ErrorReason.PROVIDER_ERROR if transient else ErrorReason.CONTENT_REJECTED,
                'X could not accept this request', retryable=transient and not public_write,
                uncertain=transient and public_write)
        try:
            value = response.json()
        except ValueError:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'X returned an invalid response',
                                retryable=not public_write, uncertain=public_write)
        if not isinstance(value, dict) or value.get('errors'):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'X did not confirm the complete result', uncertain=public_write)
        return value

    def validate(self, body, media):
        if not (body.strip() or media) or text_length(body) > 280:
            raise ProviderError(ErrorReason.CONTENT_REJECTED,
                'Use X copy within 280 weighted characters; long URLs and complex emoji are counted conservatively')
        if len(media) > 4 or any(m.byte_size > 5_000_000 for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'X supports up to four images, each at most 5 MB')
        if any(m.mime_type not in ('image/jpeg', 'image/png', 'image/webp') for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'This X adapter accepts JPEG, PNG and WebP images')

    async def publish(self, credentials, body, media, key, state):
        self.validate(body, media)
        state = dict(state)
        if state.get('provider_id'):
            return PublishResult(state['provider_id'], 'https://x.com/i/web/status/' + state['provider_id'], state)
        images = list(state.get('images', []))
        now = datetime.now(timezone.utc)
        if any(datetime.fromisoformat(image['expires_at']) <= now for image in images):
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'X upload expired; review this destination before retrying')
        # Image upload is preparation. Save each returned ID before another step.
        if len(images) < len(media):
            item = media[len(images)]
            with Path(item.path).open('rb') as file:
                value = await self.request('POST', 'media/upload', credentials,
                    files={'media': ('image', file, item.mime_type)}, data={'media_category': 'tweet_image'})
            data = value.get('data', {})
            seconds = data.get('expires_after_secs')
            if not numeric_id(data.get('id')) or type(seconds) is not int or seconds <= 0:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'X did not return a usable image upload')
            if data.get('processing_info', {}).get('state') not in (None, 'succeeded'):
                # This adapter accepts synchronous raster uploads only.
                raise ProviderError(ErrorReason.MEDIA_INVALID, 'X image processing was not completed')
            images.append({'id': data['id'], 'expires_at': (now + timedelta(seconds=min(seconds, 86400))).isoformat()})
            state.update(images=images, phase='media_uploaded')
            raise PublicationPending(state, 1)
        if state.get('phase') != 'publish_intent':
            state['phase'] = 'publish_intent'
            raise PublicationPending(state, 1)
        payload = {'text': body}
        if images:
            payload['media'] = {'media_ids': [image['id'] for image in images]}
        value = await self.request('POST', 'tweets', credentials, public_write=True, json=payload)
        id = value.get('data', {}).get('id')
        if not numeric_id(id):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'X publication ID missing; inspect X before retrying', uncertain=True)
        state.update(provider_id=id, phase='published')
        return PublishResult(id, 'https://x.com/i/web/status/' + id, state)

    async def get_post_metrics(self, credentials, provider_id):
        if not numeric_id(provider_id):
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Invalid X publication ID')
        value = await self.request('GET', 'tweets/' + provider_id, credentials,
                                   params={'tweet.fields': 'public_metrics'})
        data = value.get('data', {})
        if data.get('id') != provider_id or not isinstance(data.get('public_metrics'), dict):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'X public metrics are unavailable for this post')
        metrics = data['public_metrics']
        return {'likes': metrics.get('like_count'), 'comments': metrics.get('reply_count'),
                'shares': metrics.get('retweet_count', metrics.get('repost_count')),
                'impressions': metrics.get('impression_count'), 'provider': metrics}

    async def refresh_auth(self, credentials):
        if not self.settings or not credentials.get('refresh_token'):
            raise ProviderError(ErrorReason.AUTH_EXPIRED, 'Reconnect X to renew account consent')
        try:
            response = await self.http.post(BASE + 'oauth2/token', auth=token_auth(self.settings),
                data={'grant_type': 'refresh_token', 'refresh_token': credentials['refresh_token']}, timeout=30)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'X token renewal was unconfirmed; reconnect instead of retrying it')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'X token renewal rate limit reached', retryable=True)
        if response.is_error:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'X token renewal failed; reconnect')
        try:
            updated = token_credentials(response.json())
        except ValueError:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'X token renewal was unconfirmed; reconnect')
        credentials.update(updated)
        return credentials
