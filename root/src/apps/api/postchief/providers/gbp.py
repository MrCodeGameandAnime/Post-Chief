"""Location-bound Google Business Profile posts and performance reports."""
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
import httpx
from PIL import Image, UnidentifiedImageError
from provider_contracts import Capabilities, ProviderError, ErrorReason, PublicationPending, PublishResult
from postchief.providers.google import refresh_google

SCOPES = 'https://www.googleapis.com/auth/business.manage'
ACCOUNTS = 'https://mybusinessaccountmanagement.googleapis.com/v1/'
LOCATIONS = 'https://mybusinessbusinessinformation.googleapis.com/v1/'
POSTS = 'https://mybusiness.googleapis.com/v4/'
PERFORMANCE = 'https://businessprofileperformance.googleapis.com/v1/'
DAILY = ('BUSINESS_IMPRESSIONS_DESKTOP_MAPS', 'BUSINESS_IMPRESSIONS_DESKTOP_SEARCH',
         'BUSINESS_IMPRESSIONS_MOBILE_MAPS', 'BUSINESS_IMPRESSIONS_MOBILE_SEARCH',
         'BUSINESS_DIRECTION_REQUESTS', 'CALL_CLICKS', 'WEBSITE_CLICKS')


def location_id(value):
    return isinstance(value, str) and re.fullmatch(r'accounts/[0-9]{1,30}/locations/[0-9]{1,30}', value) is not None


def post_id(value, location):
    return location_id(location) and isinstance(value, str) and re.fullmatch(re.escape(location) + r'/localPosts/[A-Za-z0-9_-]{1,200}', value) is not None


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048: return None
    try: parsed = urlsplit(value)
    except ValueError: return None
    return value if parsed.scheme == 'https' and parsed.netloc in ('www.google.com', 'google.com', 'maps.google.com', 'search.google.com') and not parsed.fragment else None


class GoogleBusinessProvider:
    capabilities = Capabilities(text=True, image=True, analytics=True)
    account_reporting = True
    idempotent = False
    limits = {'body': 1500, 'images': 1, 'image_bytes': 5 * 1024 * 1024,
              'topic': 'STANDARD', 'language': 'en-US', 'delivery': 'Location local post; LIVE status required'}

    def __init__(self, client, settings): self.http, self.settings = client, settings

    async def refresh_auth(self, credentials):
        await refresh_google(self.http, self.settings, credentials)

    async def request(self, method, url, credentials, *, write=False, **kwargs):
        try:
            response = await self.http.request(method, url, headers={'Authorization': 'Bearer ' + credentials['access_token']},
                                               follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'Google Business Profile request was interrupted', retryable=not write, uncertain=write)
        if response.status_code == 401:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Google Business Profile authorization failed; reconnect')
        if response.status_code == 403:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google denied access; review Business Profile API approval, enabled APIs, consent and location access')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'Google Business Profile quota reached; retry later', retryable=not write, uncertain=write)
        if response.status_code >= 500:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'Google Business Profile is unavailable', retryable=not write, uncertain=write)
        if response.is_error:
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Google rejected this request; review the selected location and local post requirements')
        try: value = response.json()
        except ValueError: value = None
        if not isinstance(value, dict):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an invalid response', uncertain=write)
        return value

    async def pages(self, url, credentials, key, params, *, limit):
        rows, seen = [], set()
        for _ in range(10):
            value = await self.request('GET', url, credentials, params=params)
            items = value.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google location discovery returned invalid records')
            rows.extend(items)
            if len(rows) > limit:
                raise ProviderError(ErrorReason.PERMISSION_MISSING, 'This Google grant exceeds the supported discovery size; use a Google account with fewer locations')
            token = value.get('nextPageToken')
            if not token: return rows
            if not isinstance(token, str) or len(token) > 4096 or token in seen:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google location discovery returned invalid pagination')
            seen.add(token); params = {**params, 'pageToken': token}
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google discovery exceeds the supported page limit; use a more specific Google grant')

    async def discover(self, credentials):
        accounts = await self.pages(ACCOUNTS + 'accounts', credentials, 'accounts', {'pageSize': 20}, limit=20)
        result, seen = [], set()
        for account in accounts:
            parent = account.get('name')
            if not isinstance(parent, str) or not re.fullmatch(r'accounts/[0-9]{1,30}', parent):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an invalid business account identity')
            locations = await self.pages(LOCATIONS + parent + '/locations', credentials, 'locations',
                                         {'pageSize': 100, 'readMask': 'name,title,storefrontAddress'}, limit=100)
            for location in locations:
                name, title = location.get('name'), location.get('title')
                if not isinstance(name, str) or not re.fullmatch(r'locations/[0-9]{1,30}', name) or not isinstance(title, str) or not 0 < len(title) <= 200:
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an incomplete location identity')
                if name in seen: continue
                seen.add(name)
                identity = parent + '/' + name
                address = location.get('storefrontAddress', {})
                locality = address.get('locality') if isinstance(address, dict) else None
                label = title + (' · ' + locality[:80] if isinstance(locality, str) and locality else '')
                result.append({'provider': 'gbp', 'id': identity, 'name': label[:200],
                               'credentials': {**credentials, 'id': identity, 'location_name': name},
                               'expires_at': datetime.fromisoformat(credentials['expires_at'])})
                if len(result) > 100:
                    raise ProviderError(ErrorReason.PERMISSION_MISSING, 'This grant contains more than 100 locations; use a more specific Google account')
        if not result:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'No authorized Business Profile locations found; review business access and API approval')
        return result

    def validate(self, body, media):
        if not body.strip() or len(body) > 1500:
            raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Google local posts require 1–1,500 characters of text')
        if len(media) > 1 or any(m.mime_type not in ('image/jpeg', 'image/png') or not 10 * 1024 <= m.byte_size <= 5 * 1024 * 1024 for m in media):
            raise ProviderError(ErrorReason.MEDIA_INVALID, 'Google local posts accept one JPEG or PNG, between 10 KiB and 5 MiB')
        for item in media:
            try:
                with Image.open(item.path) as image:
                    if min(image.size) < 250 or image.format not in ('JPEG', 'PNG'): raise ValueError()
            except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
                raise ProviderError(ErrorReason.MEDIA_INVALID, 'Google photos must be valid JPEG/PNG images at least 250 pixels wide and tall')

    async def publish(self, credentials, body, media, key, state):
        location = credentials.get('id')
        if not location_id(location):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Reconnect the Google Business Profile location')
        accepted = state.get('gbp_post_id')
        if accepted:
            if not post_id(accepted, location):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Stored Google post belongs to a different location; reconcile this attempt', uncertain=True)
            value = await self.request('GET', POSTS + accepted, credentials)
        else:
            self.validate(body, media)
            if state.get('phase') != 'publish_intent':
                state['phase'] = 'publish_intent'
                raise PublicationPending(state, retry_after=1)
            payload = {'summary': body, 'languageCode': 'en-US', 'topicType': 'STANDARD'}
            if media:
                parsed = urlsplit(media[0].url or '')
                if parsed.scheme != 'https' or parsed.netloc != urlsplit(self.settings.public_url).netloc or not parsed.path.startswith('/api/media/'):
                    raise ProviderError(ErrorReason.MEDIA_INVALID, 'Google requires a public HTTPS media delivery URL')
                payload['media'] = [{'sourceUrl': media[0].url}]
            value = await self.request('POST', POSTS + location + '/localPosts', credentials, write=True, json=payload)
            accepted = value.get('name')
            if not post_id(accepted, location):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google did not confirm a valid local post identity; reconcile the location before retrying', uncertain=True)
            state.update(gbp_post_id=accepted, phase='accepted')
            state['_public_metadata'] = {'local_post_name': accepted, 'state': value.get('state'), 'url': public_url(value.get('searchUrl'))}
            # Commit the accepted resource before any further reads or errors.
            raise PublicationPending(state, retry_after=15)
        if value.get('name') != accepted:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned a different post identity; reconcile this attempt', uncertain=True)
        status = value.get('state')
        metadata = {'local_post_name': accepted, 'state': status, 'url': public_url(value.get('searchUrl'))}
        state['_public_metadata'] = metadata
        if status == 'LIVE': return PublishResult(accepted, metadata['url'], state, metadata)
        if status == 'PROCESSING': raise PublicationPending(state, retry_after=30)
        raise ProviderError(ErrorReason.CONTENT_REJECTED, 'Google accepted this local post but it is not live; review the existing post instead of creating another', uncertain=True)

    async def get_post_metrics(self, credentials, provider_id):
        if not post_id(provider_id, credentials.get('id')):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google post does not belong to this location')
        value = await self.request('GET', POSTS + provider_id, credentials)
        if value.get('name') != provider_id:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned a different local post')
        end = datetime.now(timezone.utc); start = end - timedelta(days=30)
        report = await self.request('POST', POSTS + credentials['id'] + '/localPosts:reportInsights', credentials,
            json={'localPostNames': [provider_id], 'basicRequest': {'metricRequests': [
                {'metric': 'LOCAL_POST_VIEWS_SEARCH', 'options': ['AGGREGATED_TOTAL']},
                {'metric': 'LOCAL_POST_ACTIONS_CALL_TO_ACTION', 'options': ['AGGREGATED_TOTAL']}],
                'timeRange': {'startTime': start.isoformat(), 'endTime': end.isoformat()}}})
        result = {'provider': {'state': value.get('state'), 'start_time': start.isoformat(), 'end_time': end.isoformat(),
                               'timezone': report.get('timeZone'), 'reporting_can_lag': True}}
        rows = report.get('localPostMetrics', [])
        if not isinstance(rows, list) or len(rows) > 1:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid local post metrics')
        if rows:
            if not isinstance(rows[0], dict) or rows[0].get('localPostName') != provider_id:
                raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google returned metrics for a different post')
            values = rows[0].get('metricValues', [])
            if not isinstance(values, list) or len(values) > 10:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid metric values')
            for metric in values:
                if not isinstance(metric, dict): continue
                total = metric.get('totalValue', {})
                number = total.get('value') if isinstance(total, dict) else None
                name = {'LOCAL_POST_VIEWS_SEARCH': 'views', 'LOCAL_POST_ACTIONS_CALL_TO_ACTION': 'clicks'}.get(metric.get('metric'))
                # Legacy post insight omission means unavailable, unlike daily performance.
                if name and isinstance(number, str) and re.fullmatch(r'[0-9]{1,19}', number): result[name] = int(number)
        return result

    async def get_account_report(self, credentials):
        identity = credentials.get('id')
        if not location_id(identity):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Reconnect this Google location')
        end = datetime.now(timezone.utc).date() - timedelta(days=1); start = end - timedelta(days=29)
        params = [('dailyMetrics', metric) for metric in DAILY]
        for label, day in (('start_date', start), ('end_date', end)):
            params.extend((f'dailyRange.{label}.{field}', str(getattr(day, field))) for field in ('year', 'month', 'day'))
        name = identity.split('/', 2)[2]
        value = await self.request('GET', PERFORMANCE + name + ':fetchMultiDailyMetricsTimeSeries', credentials, params=params)
        groups = value.get('multiDailyMetricTimeSeries', [])
        if not isinstance(groups, list) or len(groups) > 10:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid performance series')
        rows, seen = [], set()
        for group in groups:
            series = group.get('dailyMetricTimeSeries', []) if isinstance(group, dict) else None
            if not isinstance(series, list) or len(series) > 20:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid daily metrics')
            for metric in series:
                if not isinstance(metric, dict) or metric.get('dailyMetric') not in DAILY or metric.get('dailySubEntityType'): continue
                key = metric['dailyMetric']
                if key in seen: raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned duplicate daily series')
                seen.add(key)
                times = metric.get('timeSeries', {})
                values = times.get('datedValues', []) if isinstance(times, dict) else None
                if not isinstance(values, list) or len(values) > 31:
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid dated values')
                dates = set()
                for item in values:
                    if not isinstance(item, dict) or not isinstance(item.get('date'), dict):
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an invalid report date')
                    parts = item['date']
                    try:
                        if any(type(parts.get(k)) is not int for k in ('year', 'month', 'day')): raise ValueError()
                        day = date(parts['year'], parts['month'], parts['day'])
                    except (ValueError, KeyError):
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an invalid report date')
                    if not start <= day <= end or day in dates:
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned duplicate or out-of-window dates')
                    dates.add(day)
                    number = item.get('value', '0')  # Google DatedValue omits value for native zero.
                    if not isinstance(number, str) or not re.fullmatch(r'[0-9]{1,19}', number):
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an invalid daily count')
                    rows.append({'date': day.isoformat(), 'metric': key, 'value': int(number)})
        return {'schema_version': 1, 'normalized': {}, 'semantics': {},
                'window': {'start': start.isoformat(), 'end': end.isoformat(), 'timezone': 'Google location reporting dates', 'inclusive': True},
                'notice': 'Location-level metrics, not post metrics. Reporting can lag. Impressions count unique users per day and surface; do not sum them into unique reach. Missing dates are unavailable. An omitted count in a returned dated value is native zero.',
                'tables': [{'name': 'Daily location performance', 'rows': sorted(rows, key=lambda row: (row['date'], row['metric']))}],
                'coverage': {'requested_metrics': list(DAILY), 'returned_metrics': sorted(seen), 'dates_returned': len({r['date'] for r in rows})},
                'context': {'location': identity}}
