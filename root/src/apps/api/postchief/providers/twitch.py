"""Read-only Twitch channel context and counters, with validated user grants."""
import re
from datetime import datetime, timedelta, timezone
import httpx
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.reporting import ReportingProvider

BASE = 'https://api.twitch.tv/helix/'
TOKEN = 'https://id.twitch.tv/oauth2/token'
SCOPES = 'moderator:read:followers'


def numeric_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]{1,30}', value) is not None


def tokens(value, previous=None):
    previous = previous or {}
    access, refresh, seconds = value.get('access_token'), value.get('refresh_token'), value.get('expires_in')
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or type(seconds) is not int or not 0 < seconds <= 31536000:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'Twitch did not return a renewable user grant; reconnect')
    return {**previous, 'access_token': access, 'refresh_token': refresh,
            'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}


class TwitchProvider(ReportingProvider):
    limits = {'delivery': 'Read-only channel context/counters; cannot publish', 'recent_videos': 20}

    async def request(self, method, url, credentials=None, *, rotation=False, **kwargs):
        headers = {'Client-Id': self.settings.twitch_client_id}
        if credentials: headers['Authorization'] = 'Bearer ' + credentials['access_token']
        try:
            response = await self.http.request(method, url, headers=headers, follow_redirects=False, **kwargs)
            value = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError(ErrorReason.AUTH_REVOKED if rotation else ErrorReason.NETWORK_ERROR,
                                'Twitch request could not be confirmed; reconnect' if rotation else 'Twitch report could not be read; retry later', retryable=not rotation)
        if response.status_code == 401:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Twitch authorization is invalid; reconnect')
        if response.status_code == 403:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Twitch denied access; review channel identity and consent scopes')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'Twitch rate limit reached; retry later', retryable=not rotation)
        if response.status_code >= 500:
            raise ProviderError(ErrorReason.AUTH_REVOKED if rotation else ErrorReason.NETWORK_ERROR,
                                'Twitch renewal could not be confirmed; reconnect' if rotation else 'Twitch is unavailable; retry later', retryable=not rotation)
        if response.is_error or not isinstance(value, dict):
            raise ProviderError(ErrorReason.AUTH_REVOKED if rotation else ErrorReason.PROVIDER_ERROR, 'Twitch rejected this request; review the connection')
        return value

    async def inspect(self, credentials):
        value = await self.request('GET', 'https://id.twitch.tv/oauth2/validate', credentials)
        scopes, identity = value.get('scopes'), value.get('user_id')
        if value.get('client_id') != self.settings.twitch_client_id or not numeric_id(identity) or (credentials.get('id') and credentials['id'] != identity):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Twitch grant belongs to a different app or user; reconnect')
        if not isinstance(scopes, list) or len(scopes) > 100 or any(not isinstance(scope, str) for scope in scopes) or SCOPES not in scopes:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Grant Twitch follower read access and reconnect')
        if type(value.get('expires_in')) is not int or value['expires_in'] <= 0:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Twitch authorization has expired; reconnect')
        return value

    async def refresh_auth(self, credentials):
        value = await self.request('POST', TOKEN, rotation=True, data={'client_id': self.settings.twitch_client_id,
            'client_secret': self.settings.twitch_client_secret.get_secret_value(), 'grant_type': 'refresh_token',
            'refresh_token': credentials['refresh_token']})
        renewed = tokens(value, credentials)
        await self.inspect(renewed)
        credentials.update(renewed)

    def data(self, value, limit):
        rows = value.get('data')
        if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(row, dict) for row in rows):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Twitch returned invalid report records')
        return rows

    async def profile(self, credentials):
        rows = self.data(await self.request('GET', BASE + 'users', credentials), 1)
        if len(rows) != 1 or rows[0].get('id') != credentials['id']:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Twitch user identity did not match the grant; reconnect')
        login, name = rows[0].get('login'), rows[0].get('display_name')
        if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,100}', login) or not isinstance(name, str) or not 0 < len(name) <= 200:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Twitch returned an incomplete channel identity')
        return {'login': login, 'name': name, 'description': str(rows[0].get('description', ''))[:1000]}

    async def get_account_report(self, credentials):
        await self.inspect(credentials)
        profile = await self.profile(credentials)
        identity = credentials['id']
        channels = self.data(await self.request('GET', BASE + 'channels', credentials, params={'broadcaster_id': identity}), 1)
        if len(channels) != 1 or channels[0].get('broadcaster_id') != identity:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Twitch channel identity did not match')
        streams = self.data(await self.request('GET', BASE + 'streams', credentials, params={'user_id': identity, 'first': 1}), 1)
        normalized, semantics, errors = {}, {}, {}
        context = {**profile, 'channel_id': identity, 'is_live': bool(streams),
                   'title': str(channels[0].get('title', ''))[:200], 'category': str(channels[0].get('game_name', ''))[:200]}
        if streams:
            if streams[0].get('user_id') != identity: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Twitch returned a stream for another channel')
            count = streams[0].get('viewer_count')
            if type(count) is int and count >= 0:
                normalized['current_viewers'] = count; semantics['current_viewers'] = 'Twitch concurrent viewers at collection time, not unique lifetime viewers'
            context['stream_started_at'] = str(streams[0].get('started_at', ''))[:100]
        try:
            value = await self.request('GET', BASE + 'channels/followers', credentials, params={'broadcaster_id': identity, 'first': 1})
            count = value.get('total')
            if type(count) is int and count >= 0:
                normalized['followers'] = count; semantics['followers'] = 'Twitch total channel followers at collection time'
            else: raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Twitch did not return a follower total')
            # The individual follower list is deliberately discarded.
        except ProviderError as error: errors['followers'] = error.to_dict()
        videos, more = [], False
        try:
            value = await self.request('GET', BASE + 'videos', credentials, params={'user_id': identity, 'first': 20, 'sort': 'time'})
            for row in self.data(value, 20):
                if not numeric_id(row.get('id')) or row.get('user_id') != identity:
                    raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Twitch returned a video for another channel')
                item = {'id': row['id'], 'title': str(row.get('title', ''))[:200], 'url': 'https://www.twitch.tv/videos/' + row['id'],
                        'created_provider': str(row.get('created_at', ''))[:100], 'duration_provider': str(row.get('duration', ''))[:100],
                        'type': str(row.get('type', ''))[:30]}
                if type(row.get('view_count')) is int and row['view_count'] >= 0: item['lifetime_views'] = row['view_count']
                videos.append(item)
            pagination = value.get('pagination', {})
            more = isinstance(pagination, dict) and bool(pagination.get('cursor'))
        except ProviderError as error: errors['videos'] = error.to_dict()
        return {'schema_version': 1, 'normalized': normalized, 'semantics': semantics,
                'notice': 'Channel snapshot and recent native video counters. Offline does not mean zero historical viewers. Video views are lifetime per video, not a channel audience total. Historical broadcaster audience analytics are not exposed by this connector. No follower identities are retained.',
                'tables': [{'name': 'Recent Twitch videos', 'rows': videos}],
                'coverage': {'videos_returned': len(videos), 'more_videos': more, 'errors': errors}, 'context': context}
