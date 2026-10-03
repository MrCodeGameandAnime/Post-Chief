"""TikTok Accounts API grants and owned account/post insights; no ad writes."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
import json
import re
import httpx
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.reporting import ReportingProvider

BASE = 'https://business-api.tiktok.com/open_api/v1.3/'
REQUIRED_SCOPES = {'user.info.basic', 'user.info.stats', 'user.insights', 'video.list'}
PROFILE_FIELDS = ['display_name', 'followers_count', 'following_count', 'total_likes', 'videos_count',
                  'video_views', 'profile_views', 'likes', 'comments', 'shares']
VIDEO_FIELDS = ['item_id', 'create_time', 'caption', 'media_type', 'is_ad', 'video_views', 'likes', 'comments', 'shares', 'reach']


def authorization_url(settings, redirect, state):
    value = settings.tiktok_business_authorization_url
    if not value or not settings.tiktok_business_client_id or not settings.tiktok_business_client_secret.get_secret_value():
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Configure the TikTok Business app ID, secret and app-generated account holder authorization URL')
    parsed = urlsplit(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    params = dict(pairs)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.tiktok.com' or
        parsed.path.rstrip('/') != '/v2/auth/authorize' or parsed.fragment or len(pairs) != len(params) or
        any(k in params for k in ('client_secret', 'access_token', 'refresh_token', 'code', 'auth_code'))):
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Use the app-generated TikTok account holder authorization URL')
    app_ids = [params[k] for k in ('client_key', 'client_id') if k in params]
    if not app_ids or any(v != settings.tiktok_business_client_id for v in app_ids) or params.get('redirect_uri') != redirect or params.get('response_type') != 'code':
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok Business authorization URL must match the configured app ID and exact callback ending in /')
    target = urlsplit(redirect)
    if target.scheme != 'https' or ':' in target.netloc or target.username or target.query or target.fragment or not 10 <= len(redirect) <= 512:
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok Business requires a public HTTPS callback without a port, query or fragment')
    params.update(state=state, disable_auto_auth='1')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ''))


def token_credentials(value, previous=None):
    previous = previous or {}
    if (not isinstance(value, dict) or not all(isinstance(value.get(k), str) and value[k] for k in ('access_token', 'refresh_token', 'open_id')) or
        type(value.get('expires_in')) is not int or not 0 < value['expires_in'] <= 86400 or len(value['open_id']) > 200):
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok Business did not return a renewable account grant; reconnect')
    if previous.get('id') and previous['id'] != value['open_id']:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok Business renewal returned a different account; reconnect')
    scopes = value.get('scope', '')
    if not isinstance(scopes, str):
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok Business returned invalid account scopes; reconnect')
    return {**previous, 'id': value['open_id'], 'access_token': value['access_token'], 'refresh_token': value['refresh_token'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=value['expires_in'])).isoformat(),
            'scopes': scopes.split(',')}


def counts(row, fields):
    return {key: row[key] for key in fields if type(row.get(key)) is int and 0 <= row[key] <= 10**20}


class TikTokBusinessProvider(ReportingProvider):
    limits = {**ReportingProvider.limits, 'recent_posts': 100, 'daily_window': '7 completed UTC dates; metrics can lag 24–48 hours'}

    async def request(self, method, path, credentials=None, *, rotation=False, **kwargs):
        headers = {'Access-Token': credentials['access_token']} if credentials else {}
        try:
            response = await self.http.request(method, BASE + path, headers=headers, follow_redirects=False, **kwargs)
            value = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'TikTok Business request could not be confirmed', retryable=not rotation)
        code = value.get('code') if isinstance(value, dict) else None
        if response.status_code == 429 or code in (40016, 40100, 40133):
            raise ProviderError(ErrorReason.RATE_LIMITED, 'TikTok Business is rate limited; try later', retryable=True)
        if response.status_code == 401 or code in (40101, 40102, 40103, 40104, 40105, 40107, 40110, 40113, 40131):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok Business authorization failed; reconnect and check the account grant')
        if response.status_code >= 500 or (type(code) is int and 50000 <= code < 60000):
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'TikTok Business is unavailable', retryable=not rotation)
        if response.status_code == 403 or code in (40001, 40118, 40124, 40125):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok Business account permissions or app access are missing; review Accounts API access and consent')
        if response.is_error or type(code) is not int or code != 0 or not isinstance(value.get('data'), dict):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business rejected the operation or returned an invalid report; review the app and account settings')
        return value['data']

    async def refresh_auth(self, credentials):
        value = await self.request('POST', 'tt_user/oauth2/refresh_token/', rotation=True,
            json={'client_id': self.settings.tiktok_business_client_id,
                  'client_secret': self.settings.tiktok_business_client_secret.get_secret_value(),
                  'grant_type': 'refresh_token', 'refresh_token': credentials['refresh_token']})
        credentials.update(token_credentials(value, credentials))

    async def inspect(self, credentials):
        value = await self.request('POST', 'tt_user/token_info/get/', json={
            'app_id': self.settings.tiktok_business_client_id, 'access_token': credentials['access_token']})
        if str(value.get('app_id')) != self.settings.tiktok_business_client_id or value.get('creator_id') != credentials['id']:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok Business grant identity does not match this app/account')
        if not isinstance(value.get('scope'), str) or REQUIRED_SCOPES - set(value['scope'].split(',')):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Grant TikTok Accounts basic profile, profile statistics, account insights and video list permissions, then reconnect')
        credentials['scopes'] = value['scope'].split(',')

    async def profile(self, credentials, fields=None, **params):
        value = await self.request('GET', 'business/get/', credentials, params={
            'business_id': credentials['id'], 'fields': json.dumps(fields or ['display_name']), **params})
        if not isinstance(value.get('display_name'), str) or not 0 < len(value['display_name']) <= 200:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business account identity is incomplete')
        return value

    async def get_account_report(self, credentials):
        await self.inspect(credentials)
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        start = end - timedelta(days=6)
        profile = await self.profile(credentials, PROFILE_FIELDS, start_date=str(start), end_date=str(end))
        lifetime = counts(profile, ('followers_count', 'following_count', 'total_likes', 'videos_count'))
        daily = profile.get('metrics', [])
        if not isinstance(daily, list) or len(daily) > 60:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business returned invalid daily profile data')
        daily_rows = []
        seen_dates = set()
        for row in daily:
            day = row.get('date') if isinstance(row, dict) else None
            if not isinstance(day, str) or not str(start) <= day <= str(end) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or day in seen_dates:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business returned invalid or duplicated daily dates')
            seen_dates.add(day)
            daily_rows.append({'date': day, **counts(row, ('followers_count', 'video_views', 'profile_views', 'likes', 'comments', 'shares'))})
        posts, seen_ids, seen_cursors = [], set(), set()
        cursor = None
        more = False
        post_error = None
        try:
            for _ in range(5):
                params = {'business_id': credentials['id'], 'fields': json.dumps(VIDEO_FIELDS), 'max_count': 20}
                if cursor is not None: params['cursor'] = cursor
                page = await self.request('GET', 'business/video/list/', credentials, params=params)
                values = page.get('videos')
                if not isinstance(values, list) or len(values) > 20 or type(page.get('has_more')) is not bool:
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business returned an invalid post page')
                for row in values:
                    item_id = row.get('item_id') if isinstance(row, dict) else None
                    if not isinstance(item_id, str) or not re.fullmatch(r'\d{1,30}', item_id):
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business returned an invalid post identity')
                    if item_id in seen_ids: continue
                    seen_ids.add(item_id)
                    post = {'id': item_id, **counts(row, ('video_views', 'likes', 'comments', 'shares', 'reach'))}
                    if isinstance(row.get('caption'), str):
                        post['caption'] = row['caption'][:500]
                        if len(row['caption']) > 500: post['caption_truncated'] = True
                    if isinstance(row.get('create_time'), str): post['created_at_provider'] = row['create_time'][:40]
                    if row.get('media_type') in ('VIDEO', 'PHOTO'): post['media_type'] = row['media_type']
                    if type(row.get('is_ad')) is bool: post['is_ad'] = row['is_ad']
                    posts.append(post)
                more = page['has_more']
                if not more: break
                cursor = page.get('cursor')
                if type(cursor) is not int or cursor < 0 or cursor in seen_cursors:
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Business returned invalid post pagination')
                seen_cursors.add(cursor)
        except ProviderError as error:
            post_error = error.to_dict()
        return {'schema_version': 1, 'normalized': lifetime,
            'semantics': {k: 'TikTok Accounts API lifetime ' + k.replace('_', ' ') for k in lifetime},
            'window': {'start': str(start), 'end': str(end), 'timezone': 'UTC', 'inclusive': True},
            'notice': 'Daily metrics may lag 24–48 hours. Video views include organic and paid activity. Post counters are lifetime values, not the daily window. Missing dates/values are unavailable.',
            'tables': [{'name': 'Daily profile metrics', 'rows': daily_rows}, {'name': 'Recent public posts', 'rows': posts}],
            'coverage': {'profile_dates_returned': len(daily_rows), 'post_limit': 100, 'posts_returned': len(posts),
                         'more_posts': more, 'post_error': post_error},
            'context': {'display_name': profile['display_name']}}
