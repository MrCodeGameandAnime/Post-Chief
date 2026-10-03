"""Image Pins with a persisted public-write intent and fixed Pinterest origins."""
import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from provider_contracts import Capabilities, PublishResult, PublicationPending, ProviderError, ErrorReason

BASE = 'https://api.pinterest.com/v5/'
SCOPES = 'boards:read,pins:read,pins:write,user_accounts:read'


def numeric_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]{1,30}', value) is not None


def token_credentials(value):
    if (not isinstance(value, dict) or not isinstance(value.get('access_token'), str)
            or not value['access_token'] or type(value.get('expires_in')) is not int
            or not 0 < value['expires_in'] <= 366 * 86400):
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'Pinterest token response was invalid; reconnect')
    result = {'access_token': value['access_token'],
              'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=value['expires_in'])).isoformat()}
    if isinstance(value.get('refresh_token'), str) and value['refresh_token']:
        result['refresh_token'] = value['refresh_token']
    return result


class PinterestProvider:
    capabilities = Capabilities(text=False, image=True, analytics=True)
    limits = {'text': 800, 'images': 1, 'media_bytes': 10_000_000,
              'board': 'Owner-selected public board', 'analytics_window': 'last 30 UTC days'}
    idempotent = False

    def __init__(self, client, settings=None):
        self.http, self.settings = client, settings

    async def request(self, method, path, credentials, *, public_write=False, **kwargs):
        try:
            response = await self.http.request(method, BASE + path,
                headers={'Authorization': 'Bearer ' + credentials['access_token']},
                follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'Pinterest request failed',
                                retryable=not public_write, uncertain=public_write)
        if response.status_code == 401:
            raise ProviderError(ErrorReason.AUTH_EXPIRED, 'Reconnect Pinterest')
        if response.status_code == 403:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Review Pinterest app access and permissions')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'Pinterest rate limit reached', retryable=True)
        if response.is_error or response.is_redirect:
            transient = response.status_code >= 500
            raise ProviderError(ErrorReason.PROVIDER_ERROR if transient else ErrorReason.CONTENT_REJECTED,
                'Pinterest could not accept this request', retryable=transient and not public_write,
                uncertain=transient and public_write)
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
        except ValueError:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Pinterest returned an invalid response',
                                retryable=not public_write, uncertain=public_write)
        return value

    def validate(self, body, media):
        if len(body) > 800:
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Pinterest descriptions are limited to 800 characters')
        if (len(media) != 1 or media[0].mime_type not in ('image/jpeg', 'image/png')
                or media[0].byte_size > 10_000_000 or len(media[0].alt_text) > 500):
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'Pinterest requires one JPEG/PNG up to 10 MB, with alt text up to 500 characters')

    async def publish(self, credentials, body, media, key, state):
        self.validate(body, media)
        state = dict(state)
        board_id = state.get('board_id') or credentials.get('board_id')
        if not numeric_id(board_id):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Choose a public Pinterest board in Connections before delivery')
        if state.get('phase') != 'publish_intent':
            board = await self.request('GET', 'boards/' + board_id, credentials)
            if board.get('id') != board_id or board.get('privacy') != 'PUBLIC':
                raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Pinterest board must remain accessible and public')
            state.update(board_id=board_id, phase='publish_intent')
            raise PublicationPending(state, 1)
        item = media[0]
        content = Path(item.path).read_bytes()
        if len(content) != item.byte_size or len(content) > 10_000_000:
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'Pinterest source media changed; review the asset')
        value = await self.request('POST', 'pins', credentials, public_write=True, json={
            'board_id': board_id, 'description': body, 'alt_text': item.alt_text,
            'media_source': {'source_type': 'image_base64', 'content_type': item.mime_type,
                             'data': base64.b64encode(content).decode('ascii')}})
        if not numeric_id(value.get('id')):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Pinterest publication ID missing; inspect Pinterest before retrying', uncertain=True)
        state.update(provider_id=value['id'], phase='published')
        return PublishResult(value['id'], 'https://www.pinterest.com/pin/' + value['id'] + '/', state)

    async def get_post_metrics(self, credentials, provider_id):
        if not numeric_id(provider_id):
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Invalid Pinterest Pin ID')
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=29)
        value = await self.request('GET', 'pins/' + provider_id + '/analytics', credentials,
            params={'start_date': start.isoformat(), 'end_date': end.isoformat(),
                    'metric_types': 'IMPRESSION,PIN_CLICK,OUTBOUND_CLICK,SAVE'})
        group = value.get('all', value.get('ALL', {}))
        summary = group.get('summary_metrics', {}) if isinstance(group, dict) else {}
        if not isinstance(summary, dict):
            summary = {}
        return {'impressions': summary.get('IMPRESSION'), 'clicks': summary.get('OUTBOUND_CLICK'),
                'window': {'start_date': start.isoformat(), 'end_date': end.isoformat(), 'timezone': 'UTC'},
                'provider': value}

    async def refresh_auth(self, credentials):
        if not self.settings or not credentials.get('refresh_token'):
            raise ProviderError(ErrorReason.AUTH_EXPIRED, 'Reconnect Pinterest to renew consent')
        try:
            response = await self.http.post(BASE + 'oauth/token',
                auth=httpx.BasicAuth(self.settings.pinterest_client_id, self.settings.pinterest_client_secret.get_secret_value()),
                data={'grant_type': 'refresh_token', 'refresh_token': credentials['refresh_token']}, timeout=30)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Pinterest renewal was unconfirmed; reconnect')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'Pinterest renewal rate limit reached', retryable=True)
        if response.is_error or response.is_redirect:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Pinterest token renewal failed; reconnect')
        try:
            updated = token_credentials(response.json())
        except ValueError:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Pinterest renewal was unconfirmed; reconnect')
        credentials.update(updated)
        return credentials
