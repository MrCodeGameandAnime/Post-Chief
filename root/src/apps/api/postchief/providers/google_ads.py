"""Fixed read-only Google Ads queries; no mutation or owner-supplied GAQL."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.web_analytics import WebAnalyticsProvider

SCOPES = 'https://www.googleapis.com/auth/adwords'
BASE = 'https://googleads.googleapis.com/v25/'
PROFILE = 'SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, customer.manager FROM customer LIMIT 1'


def identity(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]{1,20}', value) is not None


class GoogleAdsProvider(WebAnalyticsProvider):
    limits = {'delivery': 'Read-only ad reports; no campaign or budget mutations', 'report_days': 30, 'campaign_rows': 1000}

    async def request(self, method, url, credentials, **kwargs):
        headers = {'developer-token': self.settings.google_ads_developer_token.get_secret_value()}
        manager = credentials.get('login_customer_id')
        if manager:
            if not identity(manager): raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Invalid Google Ads manager identity; reconnect')
            headers['login-customer-id'] = manager
        # This adapter has only listAccessibleCustomers and fixed search endpoints.
        if not headers['developer-token']:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Configure a Google Ads developer token before connecting')
        import httpx
        try:
            response = await self.http.request(method, url, headers={**headers, 'Authorization': 'Bearer ' + credentials['access_token']}, follow_redirects=False, **kwargs)
            value = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'Google Ads report unavailable; retry later', retryable=True)
        if response.status_code == 401: raise ProviderError(ErrorReason.AUTH_REVOKED, 'Google Ads grant expired or revoked; reconnect')
        if response.status_code == 403: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Review Google Ads developer-token access, customer permissions and manager identity')
        if response.status_code == 429: raise ProviderError(ErrorReason.RATE_LIMITED, 'Google Ads quota reached; retry later', retryable=True)
        if response.status_code >= 500: raise ProviderError(ErrorReason.NETWORK_ERROR, 'Google Ads unavailable; retry later', retryable=True)
        if response.is_error or not isinstance(value, dict): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads rejected the fixed report query')
        return value

    async def query(self, credentials, query, limit):
        if not identity(credentials.get('id')): raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Invalid Google Ads customer identity')
        value = await self.request('POST', BASE + 'customers/' + credentials['id'] + '/googleAds:search', credentials, json={'query': query})
        rows = value.get('results', [])
        if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(row, dict) for row in rows):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads returned invalid report rows')
        # Limits are in GAQL; unexpected pagination is rejected rather than silently dropped.
        if value.get('nextPageToken'): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads report exceeded its fixed row bound')
        return rows

    async def profile(self, credentials):
        rows = await self.query(credentials, PROFILE, 1)
        row = rows[0].get('customer') if len(rows) == 1 else None
        if not isinstance(row, dict) or str(row.get('id')) != credentials['id']:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google Ads customer identity did not match')
        currency, tz = row.get('currencyCode'), row.get('timeZone')
        if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency) or not isinstance(tz, str):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads account currency/timezone unavailable')
        try: ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads account timezone is unsupported')
        return {'id': credentials['id'], 'name': str(row.get('descriptiveName') or credentials['id'])[:200], 'currency': currency, 'timezone': tz, 'manager': row.get('manager', False)}

    async def discover(self, credentials):
        value = await self.request('GET', BASE + 'customers:listAccessibleCustomers', credentials)
        resources = value.get('resourceNames', [])
        if not isinstance(resources, list) or len(resources) > 100:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google Ads discovery supports at most 100 directly accessible customers')
        result, seen = [], set()
        for resource in resources:
            if not isinstance(resource, str) or not re.fullmatch(r'customers/[0-9]{1,20}', resource):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads returned an invalid customer identity')
            direct = {**credentials, 'id': resource.split('/')[1]}
            profile = await self.profile(direct)
            candidates = [profile]
            if profile['manager']:
                children = await self.query(direct, 'SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager FROM customer_client WHERE customer_client.status = ENABLED AND customer_client.manager = FALSE LIMIT 101', 101)
                if len(children) > 100: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Google Ads manager has more than 100 clients; use a narrower grant')
                candidates = []
                for child in children:
                    row = child.get('customerClient', {})
                    cid = str(row.get('id', ''))
                    if not identity(cid): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads returned an invalid client identity')
                    candidates.append({'id': cid, 'name': str(row.get('descriptiveName') or cid)[:200]})
            for candidate in candidates:
                cid = candidate['id']
                if cid in seen: continue
                seen.add(cid)
                grant = {**credentials, 'id': cid}
                if profile['manager']: grant['login_customer_id'] = profile['id']
                await self.profile(grant)
                result.append({'provider': 'google_ads', 'id': cid, 'name': candidate['name'], 'credentials': grant, 'expires_at': datetime.fromisoformat(grant['expires_at'])})
                if len(result) > 100: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'More than 100 Google Ads clients; use a narrower grant')
        if not result: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'No accessible Google Ads client accounts found')
        return result

    async def get_account_report(self, credentials):
        profile = await self.profile(credentials)
        if profile['manager']: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Connect a Google Ads client account for performance reports')
        end = datetime.now(ZoneInfo(profile['timezone'])).date() - timedelta(days=1)
        start = end - timedelta(days=29)
        fields = 'metrics.impressions, metrics.clicks, metrics.cost_micros'
        condition = f"segments.date BETWEEN '{start}' AND '{end}'"
        daily = await self.query(credentials, f'SELECT segments.date, {fields} FROM customer WHERE {condition} ORDER BY segments.date LIMIT 31', 31)
        campaigns = await self.query(credentials, f'SELECT campaign.id, campaign.name, {fields} FROM campaign WHERE {condition} ORDER BY campaign.id LIMIT 1001', 1001)
        more = len(campaigns) > 1000
        def metrics(row):
            native = row.get('metrics', {})
            result = {}
            for key in ('impressions', 'clicks', 'costMicros'):
                raw = native.get(key)
                if raw is None: continue
                if not isinstance(raw, str) or not re.fullmatch(r'[0-9]{1,19}', raw): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads returned an invalid native metric')
                result['cost_micros' if key == 'costMicros' else key] = raw
            return result
        days, seen = [], set()
        for row in daily:
            day = row.get('segments', {}).get('date')
            if not isinstance(day, str) or not start.isoformat() <= day <= end.isoformat() or day in seen or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads returned an invalid reporting date')
            seen.add(day); days.append({'date': day, 'currency': profile['currency'], **metrics(row)})
        rows = []
        for row in campaigns[:1000]:
            campaign = row.get('campaign', {}); cid = str(campaign.get('id', ''))
            if not identity(cid): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Google Ads campaign identity unavailable')
            rows.append({'campaign_id': cid, 'campaign_name': str(campaign.get('name', ''))[:200], 'currency': profile['currency'], **metrics(row)})
        return {'schema_version': 1, 'normalized': {}, 'semantics': {'impressions': 'Native Google Ads impressions', 'clicks': 'Native Google Ads clicks', 'cost_micros': 'Native integer micros in account currency; 1,000,000 micros = one currency unit'},
            'window': {'start': str(start), 'end': str(end), 'timezone': profile['timezone'], 'inclusive': True},
            'context': profile, 'coverage': {'more_campaigns': more, 'campaigns_returned': len(rows), 'days_returned': len(days)},
            'notice': 'Read-only reports for 30 completed account-local dates. Integer metrics remain decimal strings to preserve precision. Missing rows/counters are unavailable, not zero. Recent reports may be revised by Google. No conversion attribution or cross-account currency totals are inferred.',
            'tables': [{'name': 'Daily account performance', 'rows': days}, {'name': 'Campaign performance', 'rows': rows}]}
