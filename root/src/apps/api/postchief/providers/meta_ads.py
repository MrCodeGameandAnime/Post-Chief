"""Meta advertising discovery and synchronous read-only insights."""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from provider_contracts import ProviderError, ErrorReason
from postchief.providers.reporting import ReportingProvider
from postchief.providers.meta import GraphProvider


class MetaAdsProvider(ReportingProvider):
    requires_token_refresh = False
    limits = {'delivery': 'Read-only ad insights; no campaigns, budgets or spending', 'report_days': 30, 'campaign_rows': 1000}

    async def request(self, path, credentials, **kwargs):
        if credentials.get('expires_at') and datetime.fromisoformat(credentials['expires_at']) <= datetime.now(timezone.utc):
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Meta Ads grant expired; reconnect')
        value = await GraphProvider(self.http, self.settings).request('GET', path, credentials, follow_redirects=False, **kwargs)
        if not isinstance(value, dict): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads returned invalid report data')
        return value

    async def pages(self, path, credentials, params, max_rows):
        rows, seen, more = [], set(), False
        for _ in range(10):
            value = await self.request(path, credentials, params=params)
            native = value.get('data')
            if not isinstance(native, list) or len(native) > 100 or any(not isinstance(row, dict) for row in native):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads returned invalid report rows')
            rows.extend(native)
            paging = value.get('paging', {})
            if not isinstance(paging, dict) or not isinstance(paging.get('cursors', {}), dict):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads pagination invalid')
            more = bool(paging.get('next'))
            if len(rows) >= max_rows: return rows[:max_rows], more or len(rows) > max_rows
            if not more: return rows, False
            cursor = value.get('paging', {}).get('cursors', {}).get('after')
            if not isinstance(cursor, str) or not 0 < len(cursor) <= 4096 or cursor in seen:
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads pagination invalid')
            seen.add(cursor); params = {**params, 'after': cursor}
        return rows, more

    async def discover(self, credentials):
        permissions = await self.request('me/permissions', credentials)
        if not isinstance(permissions.get('data'),list): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads returned invalid permissions')
        if not any(isinstance(p, dict) and p.get('permission') == 'ads_read' and p.get('status') == 'granted' for p in permissions.get('data', [])):
            raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Grant Meta ads_read and reconnect')
        rows, more = await self.pages('me/adaccounts', credentials, {'fields': 'id,name,currency,timezone_name', 'limit': 100}, 100)
        if more: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'More than 100 Meta ad accounts; use a narrower grant')
        result, seen = [], set()
        for row in rows:
            identity = row.get('id')
            if not isinstance(identity, str) or not re.fullmatch(r'act_[0-9]{1,30}', identity):
                raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads account identity invalid')
            if identity in seen: continue
            seen.add(identity)
            result.append({'provider': 'meta_ads', 'id': identity, 'name': str(row.get('name') or identity)[:200],
                'credentials': {**credentials, 'id': identity}, 'expires_at': datetime.fromisoformat(credentials['expires_at'])})
        if not result: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'No authorized Meta ad accounts found')
        return result

    async def get_account_report(self, credentials):
        identity = credentials.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'act_[0-9]{1,30}', identity): raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Invalid Meta Ads account identity')
        profile = await self.request(identity, credentials, params={'fields': 'id,name,currency,timezone_name'})
        if profile.get('id') != identity: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Meta Ads account identity did not match')
        tz, currency = profile.get('timezone_name'), profile.get('currency')
        if not isinstance(tz, str) or not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads currency/timezone missing')
        try: end = datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)
        except (ZoneInfoNotFoundError, ValueError): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads account timezone unsupported')
        start = end - timedelta(days=29)
        params = {'time_range': json.dumps({'since': str(start), 'until': str(end)}), 'fields': 'account_id,campaign_id,campaign_name,date_start,date_stop,impressions,clicks,spend', 'limit': 100}
        daily, daily_more = await self.pages(identity + '/insights', credentials, {**params, 'level': 'account', 'time_increment': 1}, 31)
        campaigns, more = await self.pages(identity + '/insights', credentials, {**params, 'level': 'campaign'}, 1000)
        def sanitize(rows):
            result = []
            for row in rows:
                if row.get('account_id') != identity[4:]: raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Meta insights returned a different account')
                item = {'currency': currency}
                for key in ('date_start', 'date_stop'):
                    value = row.get(key)
                    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or not str(start) <= value <= str(end):
                        raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads report date invalid')
                    item[key] = value
                if row.get('campaign_id'):
                    if not re.fullmatch(r'[0-9]{1,30}', str(row['campaign_id'])): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta campaign identity invalid')
                    item.update(campaign_id=row['campaign_id'], campaign_name=str(row.get('campaign_name', ''))[:200])
                for key in ('impressions', 'clicks', 'spend'):
                    value = row.get(key)
                    if value is None: continue
                    pattern = r'[0-9]{1,19}(\.[0-9]{1,9})?' if key == 'spend' else r'[0-9]{1,19}'
                    if not isinstance(value, str) or not re.fullmatch(pattern, value): raise ProviderError(ErrorReason.PROVIDER_ERROR, 'Meta Ads metric invalid')
                    item[key] = value
                result.append(item)
            return result
        return {'schema_version': 1, 'normalized': {}, 'semantics': {'impressions': 'Meta native ad impressions', 'clicks': 'Meta native all ad clicks, not link clicks', 'spend': 'Meta native decimal spending in account currency'},
            'window': {'start': str(start), 'end': str(end), 'timezone': tz, 'inclusive': True},
            'context': {'account_id': identity, 'currency': currency, 'timezone': tz},
            'notice': 'Read-only 30 completed account-local dates. Decimal values remain strings. No conversion attribution, unique reach sums, or cross-currency totals inferred. Missing data is unavailable; recent results may change.',
            'coverage': {'more_campaigns': more, 'more_daily_rows': daily_more},
            'tables': [{'name': 'Daily account performance', 'rows': sanitize(daily)}, {'name': 'Campaign performance', 'rows': sanitize(campaigns)}]}
