"""Read-only GA4 properties and native reports; no website configuration writes."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.reporting import ReportingProvider
from postchief.providers.google import refresh_google

SCOPES = 'https://www.googleapis.com/auth/analytics.readonly'
ADMIN = 'https://analyticsadmin.googleapis.com/v1beta/'
DATA = 'https://analyticsdata.googleapis.com/v1beta/'
METRICS = ('activeUsers', 'sessions', 'screenPageViews')


class WebAnalyticsProvider(ReportingProvider):
    limits = {'delivery': 'Read-only GA4 property reports; cannot publish', 'report_days': 30}

    async def refresh_auth(self, credentials):
        await refresh_google(self.http, self.settings, credentials)

    async def request(self, method, url, credentials, **kwargs):
        try:
            response = await self.http.request(method, url, headers={'Authorization': 'Bearer ' + credentials['access_token']},
                                               follow_redirects=False, **kwargs)
            value = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'Google Analytics report could not be read; retry later', retryable=True)
        if response.status_code == 401:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Google Analytics authorization failed; reconnect')
        if response.status_code == 403:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Review Analytics API access, enabled Admin/Data APIs and property permissions')
        if response.status_code == 429:
            raise ProviderError(ErrorReason.RATE_LIMITED, 'Google Analytics quota reached; retry later', retryable=True)
        if response.status_code >= 500:
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'Google Analytics is unavailable; retry later', retryable=True)
        if response.is_error or not isinstance(value, dict):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics rejected this report request')
        return value

    async def discover(self, credentials):
        result, seen, tokens = [], set(), set(); params = {'pageSize': 20}
        for _ in range(5):
            value = await self.request('GET', ADMIN + 'accountSummaries', credentials, params=params)
            accounts = value.get('accountSummaries', [])
            if not isinstance(accounts, list) or len(accounts) > 20:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid Analytics accounts')
            for account in accounts:
                summaries = account.get('propertySummaries', []) if isinstance(account, dict) else None
                if not isinstance(summaries, list) or len(summaries) > 100:
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid Analytics properties')
                for item in summaries:
                    if not isinstance(item, dict): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid property identity')
                    identity, name = item.get('property'), item.get('displayName')
                    if not isinstance(identity, str) or not re.fullmatch(r'properties/[0-9]{1,30}', identity) or not isinstance(name, str) or not 0 < len(name) <= 200:
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid property identity')
                    if identity in seen: continue
                    seen.add(identity)
                    result.append({'provider': 'web', 'id': identity, 'name': name,
                                   'credentials': {**credentials, 'id': identity},
                                   'expires_at': datetime.fromisoformat(credentials['expires_at'])})
                    if len(result) > 100:
                        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'More than 100 Analytics properties were granted; connect using a more specific Google account')
            token = value.get('nextPageToken')
            if not token: break
            if not isinstance(token, str) or len(token) > 4096 or token in tokens:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned invalid Analytics pagination')
            tokens.add(token); params['pageToken'] = token
        else:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Analytics discovery exceeded five pages; connect using a more specific Google account')
        if not result: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'No accessible GA4 properties found')
        return result

    def rows(self, value, dimensions, limit):
        dh, mh = value.get('dimensionHeaders', []), value.get('metricHeaders', [])
        if not isinstance(dh, list) or not isinstance(mh, list) or [h.get('name') if isinstance(h, dict) else None for h in dh] != dimensions or [h.get('name') if isinstance(h, dict) else None for h in mh] != list(METRICS):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics returned unexpected columns')
        native = value.get('rows', [])
        if not isinstance(native, list) or len(native) > limit:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics returned an oversized report')
        result = []
        for item in native:
            dv, mv = (item.get('dimensionValues', []), item.get('metricValues', [])) if isinstance(item, dict) else (None, None)
            if not isinstance(dv, list) or not isinstance(mv, list) or len(dv) != len(dimensions) or len(mv) != len(METRICS):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics returned invalid row values')
            row = {}
            for name, cell in zip(dimensions, dv):
                text = cell.get('value') if isinstance(cell, dict) else None
                if not isinstance(text, str) or len(text) > 500:
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics returned an invalid dimension')
                row[name] = text
            for name, cell in zip(METRICS, mv):
                text = cell.get('value') if isinstance(cell, dict) else None
                if not isinstance(text, str) or not re.fullmatch(r'[0-9]{1,19}', text):
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics returned an invalid native count')
                row[name] = int(text)
            result.append(row)
        return result

    async def get_account_report(self, credentials):
        identity = credentials.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'properties/[0-9]{1,30}', identity):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Reconnect this GA4 property')
        property_info = await self.request('GET', ADMIN + identity, credentials)
        zone = property_info.get('timeZone')
        try:
            if property_info.get('name') != identity or not isinstance(zone, str) or len(zone) > 100: raise ValueError()
            end = datetime.now(ZoneInfo(zone)).date() - timedelta(days=1); start = end - timedelta(days=29)
        except (ZoneInfoNotFoundError, ValueError):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google returned an invalid Analytics property timezone')
        base = {'dateRanges': [{'startDate': start.isoformat(), 'endDate': end.isoformat()}],
                'metrics': [{'name': name} for name in METRICS], 'returnPropertyQuota': True}
        totals = await self.request('POST', DATA + identity + ':runReport', credentials, json={**base, 'limit': '1'})
        total_rows = self.rows(totals, [], 1)
        tables, coverage = [], {}
        for name, dims, limit in (('Daily website activity', ['date'], 30), ('Session channels', ['sessionDefaultChannelGroup'], 25)):
            value = await self.request('POST', DATA + identity + ':runReport', credentials,
                json={**base, 'dimensions': [{'name': d} for d in dims], 'limit': str(limit),
                      'orderBys': [{'dimension': {'dimensionName': dims[0]}}]})
            rows = self.rows(value, dims, limit)
            if dims == ['date']:
                dates = [row['date'] for row in rows]
                if len(set(dates)) != len(dates) or any(not re.fullmatch(r'[0-9]{8}', day) or not start.strftime('%Y%m%d') <= day <= end.strftime('%Y%m%d') for day in dates):
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Analytics returned duplicate or out-of-window dates')
            metadata = value.get('metadata', {})
            tables.append({'name': name, 'rows': rows})
            coverage[name] = {'rows_returned': len(rows), 'native_row_count': value.get('rowCount'),
                              'limited': type(value.get('rowCount')) is int and value['rowCount'] > len(rows),
                              'subject_to_thresholding': metadata.get('subjectToThresholding') if isinstance(metadata, dict) else None,
                              'data_loss_from_other_row': metadata.get('dataLossFromOtherRow') if isinstance(metadata, dict) else None}
            if isinstance(metadata, dict) and isinstance(metadata.get('samplingMetadatas'), list):
                coverage[name]['sampling'] = metadata['samplingMetadatas'][:10]
        metadata = totals.get('metadata', {})
        if not isinstance(metadata, dict): metadata = {}
        return {'schema_version': 1, 'normalized': total_rows[0] if total_rows else {},
                'semantics': {'activeUsers': 'GA4 active users over the complete requested window; not the sum of daily users',
                              'sessions': 'GA4 sessions over the complete requested window', 'screenPageViews': 'GA4 page/screen views over the complete requested window'},
                'window': {'start': start.isoformat(), 'end': end.isoformat(), 'timezone': zone, 'inclusive': True},
                'notice': 'Read-only native GA4 data. Tracking must already be installed. Reports can lag and may be thresholded; channel tables are bounded. Missing rows are unavailable, not zero. Active users are not summed across dates or channels.',
                'tables': tables, 'coverage': coverage,
                'context': {'property': identity, 'subject_to_thresholding': metadata.get('subjectToThresholding'),
                            'data_loss_from_other_row': metadata.get('dataLossFromOtherRow'),
                            'sampling': metadata.get('samplingMetadatas', [])[:10] if isinstance(metadata.get('samplingMetadatas', []), list) else []}}
