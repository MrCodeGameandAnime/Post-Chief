"""TikTok Marketing API advertiser grants and bounded BASIC reports."""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.reporting import ReportingProvider

BASE = 'https://business-api.tiktok.com/open_api/v1.3/'


def numeric(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]{1,30}', value) is not None


def authorization_url(settings, redirect, state):
    parsed = urlsplit(settings.tiktok_ads_authorization_url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True); params = dict(pairs)
    if (parsed.scheme != 'https' or parsed.netloc != 'business-api.tiktok.com' or parsed.path.rstrip('/') != '/portal/auth' or
        parsed.fragment or len(pairs) != len(params) or params.get('app_id') != settings.tiktok_ads_app_id or params.get('redirect_uri') != redirect or
        any(k in params for k in ('secret', 'access_token', 'auth_code', 'code')) or not settings.tiktok_ads_secret.get_secret_value()):
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Configure TikTok Ads app ID, secret and app-generated advertiser authorization URL matching the exact callback')
    params['state'] = state
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ''))


class TikTokAdsProvider(ReportingProvider):
    requires_token_refresh = False
    limits = {'delivery': 'Read-only advertiser reports; no ad or budget mutations', 'report_days': 30, 'report_rows': 1000}

    async def request(self, method, path, credentials=None, **kwargs):
        # No general TikTok mutation endpoint is exposed by this adapter.
        allowed = {'oauth2/access_token/': 'POST', 'oauth2/advertiser/get/': 'GET', 'advertiser/info/': 'GET', 'report/integrated/get/': 'GET'}
        if allowed.get(path) != method: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok Ads operation is not supported')
        try:
            response = await self.http.request(method, BASE + path, headers={'Access-Token': credentials['access_token']} if credentials else {}, follow_redirects=False, **kwargs)
            value = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError(ErrorReason.NETWORK_ERROR, 'TikTok advertiser request could not be confirmed; retry or reconnect', retryable=method == 'GET')
        if response.status_code == 401: raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok advertiser grant revoked; reconnect')
        if response.status_code == 403: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Review TikTok advertiser grant and reporting permissions')
        code = value.get('code') if isinstance(value, dict) else None
        if response.status_code == 429 or code in (40016, 40100, 40133): raise ProviderError(ErrorReason.RATE_LIMITED, 'TikTok advertiser quota reached; retry later', retryable=method == 'GET')
        if code in (40101, 40102, 40103, 40104, 40105, 40107, 40110, 40113, 40131):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'TikTok advertiser grant failed; reconnect and check authorization')
        if response.status_code >= 500: raise ProviderError(ErrorReason.NETWORK_ERROR, 'TikTok Ads unavailable; retry later', retryable=method == 'GET')
        if response.is_error or not isinstance(value, dict) or type(value.get('code')) is not int or value['code'] != 0:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok Ads rejected the request; review advertiser authorization and enabled reporting permissions')
        data = value.get('data')
        if not isinstance(data, dict): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok Ads returned invalid data')
        return data

    async def discover(self, credentials):
        value = await self.request('GET', 'oauth2/advertiser/get/', credentials, params={'app_id': self.settings.tiktok_ads_app_id, 'secret': self.settings.tiktok_ads_secret.get_secret_value()})
        rows = value.get('list', [])
        if not isinstance(rows, list) or len(rows) > 100: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok Ads discovery supports at most 100 advertisers')
        result, seen = [], set()
        for row in rows:
            cid = str(row.get('advertiser_id', '')) if isinstance(row, dict) else ''
            if not numeric(cid): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok advertiser identity invalid')
            if cid in seen: continue
            seen.add(cid)
            result.append({'provider': 'tiktok_ads', 'id': cid, 'name': str(row.get('advertiser_name') or cid)[:200], 'credentials': {**credentials, 'id': cid}})
        if not result: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'No TikTok advertisers granted access')
        return result

    async def get_account_report(self, credentials):
        cid = credentials.get('id')
        if not numeric(cid): raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok advertiser identity invalid')
        value = await self.request('GET', 'advertiser/info/', credentials, params={'advertiser_ids': json.dumps([cid]), 'fields': json.dumps(['advertiser_id', 'name', 'currency', 'timezone'])})
        rows = value.get('list', [])
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or str(rows[0].get('advertiser_id')) != cid:
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'TikTok advertiser identity did not match')
        profile = rows[0]; tz, currency = profile.get('timezone'), profile.get('currency')
        if not isinstance(tz, str) or not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok advertiser currency/timezone unavailable')
        try: end = datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)
        except (ZoneInfoNotFoundError, ValueError): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok advertiser timezone is not an IANA zone; review account configuration')
        start = end - timedelta(days=29)
        params = {'advertiser_id': cid, 'report_type': 'BASIC', 'service_type': 'AUCTION', 'data_level': 'AUCTION_CAMPAIGN',
            'dimensions': json.dumps(['campaign_id', 'stat_time_day']), 'metrics': json.dumps(['impressions', 'clicks', 'spend']), 'start_date': str(start), 'end_date': str(end), 'page_size': 100}
        report, seen, more = [], set(), False
        for page in range(1, 11):
            data = await self.request('GET', 'report/integrated/get/', credentials, params={**params, 'page': page})
            native = data.get('list', [])
            if not isinstance(native, list) or len(native) > 100: raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok report rows invalid')
            for row in native:
                dims, metrics = (row.get('dimensions', {}), row.get('metrics', {})) if isinstance(row, dict) else ({}, {})
                if not isinstance(dims, dict) or not isinstance(metrics, dict): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok report cells invalid')
                campaign, day = dims.get('campaign_id'), dims.get('stat_time_day')
                if not numeric(campaign) or not isinstance(day, str) or len(day) > 30 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}( 00:00:00)?', day) or not str(start) <= day[:10] <= str(end):
                    raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok report dimensions invalid')
                key = (campaign, day[:10])
                if key in seen: raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok returned duplicate campaign/day rows')
                seen.add(key)
                item = {'campaign_id': campaign, 'date': day[:10], 'currency': currency}
                for name in ('impressions', 'clicks', 'spend'):
                    raw = metrics.get(name)
                    if raw is None: continue
                    # Censored native counts remain censored text, never fabricated zero.
                    if not isinstance(raw, str) or not re.fullmatch(r'(<[0-9]{1,19}|[0-9]{1,19}(\.[0-9]{1,9})?)', raw):
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok report metric invalid')
                    item[name] = raw
                report.append(item)
            info = data.get('page_info', {})
            if not isinstance(info, dict): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok report pagination invalid')
            total = info.get('total_page')
            if type(total) is not int or total < 0: raise ProviderError(ErrorReason.PROVIDER_ERROR, 'TikTok report pagination invalid')
            more = page < total
            if not more: break
        return {'schema_version': 1, 'normalized': {}, 'semantics': {'impressions': 'TikTok native auction ad impressions', 'clicks': 'TikTok native auction ad clicks', 'spend': 'TikTok native decimal spending in advertiser currency'},
            'window': {'start': str(start), 'end': str(end), 'timezone': tz, 'inclusive': True}, 'context': {'advertiser_id': cid, 'currency': currency, 'timezone': tz},
            'coverage': {'rows_returned': len(report), 'more_rows': more},
            'notice': 'Read-only BASIC AUCTION campaign/day report for 30 completed advertiser-local dates, bounded to 1,000 rows. Reservation and specialized campaign products are outside this report. Native censored counts remain text. Missing data is unavailable; no conversion attribution or currency conversion inferred.',
            'tables': [{'name': 'Campaign daily performance', 'rows': report}]}
