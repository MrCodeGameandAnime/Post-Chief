"""Versioned, authenticated Looker export of saved reports, without provider I/O."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor, current_actor, require_scope
from postchief.db import get_db
from postchief.models import SocialAccount, AuditEvent
from postchief.analytics.accounts import event_query, reporting_account, snapshot

router = APIRouter(prefix='/reports/looker/v1', tags=['Looker Studio'])
FIELDS = [
    ('provider', 'Provider'), ('account_id', 'Post Chief account ID'), ('account_name', 'Account name'),
    ('snapshot_id', 'Snapshot ID'), ('collected_at', 'Collected at UTC'), ('source_table', 'Source table'),
    ('entity_id', 'Entity ID'), ('entity_name', 'Entity name'), ('date_start', 'Period start'), ('date_end', 'Period end'),
    ('timezone', 'Reporting timezone'), ('currency', 'Native currency'), ('metric', 'Native metric'),
    ('metric_raw', 'Exact native value'), ('metric_number', 'Chart value'), ('unit', 'Unit'),
    ('meaning', 'Native meaning'), ('value_status', 'Value status'), ('coverage', 'Source coverage'), ('notice', 'Source notice')]
METRICS = {'google_ads': {'impressions', 'clicks', 'cost_micros'}, 'meta_ads': {'impressions', 'clicks', 'spend'}, 'tiktok_ads': {'impressions', 'clicks', 'spend'}}


def field_schema():
    return [{'name': key, 'label': label, 'dataType': 'NUMBER' if key == 'metric_number' else 'STRING',
        'semantics': {'conceptType': 'METRIC' if key == 'metric_number' else 'DIMENSION',
                      'semanticType': 'NUMBER' if key == 'metric_number' else 'TEXT', 'isReaggregatable': key == 'metric_number'},
        **({'defaultAggregationType': 'NONE'} if key == 'metric_number' else {})} for key, label in FIELDS]


@router.get('/schema')
def schema(actor: Actor = Depends(current_actor)):
    require_scope(actor, 'analytics:read')
    return JSONResponse({'schema_version': 1, 'schema': field_schema()}, headers={'Cache-Control': 'no-store'})


def latest(db, org_id, account_id):
    row = db.scalar(event_query(org_id, account_id, ['account.report']).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1))
    return snapshot(row)


def tables(provider, report):
    result = [('Window totals' if provider == 'web' else 'Snapshot metrics')] if report.get('normalized') else []
    if provider in METRICS:
        result += [table['name'] for table in report.get('tables', []) if isinstance(table, dict) and isinstance(table.get('name'), str)]
    return result


@router.get('/sources')
def sources(actor: Actor = Depends(current_actor), db: Session = Depends(get_db), offset: int = Query(default=0, ge=0)):
    require_scope(actor, 'analytics:read')
    from postchief.providers.registry import PROVIDERS
    providers = [name for name, cls in PROVIDERS.items() if getattr(cls, 'account_reporting', False)]
    accounts = db.scalars(select(SocialAccount).where(SocialAccount.org_id == actor.org_id, SocialAccount.provider.in_(providers)).order_by(SocialAccount.id).offset(offset).limit(100)).all()
    result = []
    for account in accounts:
        saved = latest(db, actor.org_id, account.id)
        if not saved: continue
        for table in tables(account.provider, saved['report']):
            result.append({'account_id': account.id, 'account_name': account.name, 'provider': account.provider, 'table': table})
    return JSONResponse({'schema_version': 1, 'sources': result, 'next_offset': offset + 100 if len(accounts) == 100 else None}, headers={'Cache-Control': 'no-store'})


def numeric(raw, micros):
    text = str(raw)
    if text.startswith('<'): return None, 'censored'
    try: value = Decimal(text)
    except InvalidOperation: return None, 'unavailable'
    if not value.is_finite(): return None, 'unavailable'
    # Preserve exact values separately; avoid silently rounding int64 counters.
    if len(value.normalize().as_tuple().digits) > 15 or abs(value) > Decimal('9007199254740991'):
        return None, 'precision_limited'
    return float(value / Decimal(1000000) if micros else value), 'available'


@router.get('/data')
def data(account_id: str, source_table: str = Query(max_length=100), start_date: date | None = None, end_date: date | None = None,
         actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, 'analytics:read')
    if bool(start_date) != bool(end_date) or (start_date and end_date and start_date > end_date): raise HTTPException(422, 'Provide an ordered start and end date together')
    account = reporting_account(db, actor, account_id)
    saved = latest(db, actor.org_id, account.id)
    if not saved: raise HTTPException(409, 'Collect an account report before connecting Looker Studio')
    report = saved['report']
    if report.get('schema_version') != 1: raise HTTPException(409, 'Unsupported source report version')
    if source_table not in tables(account.provider, report): raise HTTPException(422, 'Select a supported source table from /sources')
    if source_table in ('Snapshot metrics', 'Window totals'):
        period = report.get('window', {})
        dimensions = {'date_start': period.get('start'), 'date_stop': period.get('end')} if source_table == 'Window totals' else {'date': saved['collected_at'][:10]}
        native = [{**dimensions, **report.get('normalized', {})}]
        metrics = set(report.get('normalized', {}))
    else:
        native = next(table['rows'] for table in report['tables'] if table['name'] == source_table)
        metrics = METRICS[account.provider]
    window, context = report.get('window', {}), report.get('context', {})
    result = []
    import json
    for row in native:
        begin = row.get('date') or row.get('date_start') or window.get('start') or saved['collected_at'][:10]
        end = row.get('date') or row.get('date_stop') or window.get('end') or begin
        if not isinstance(begin, str) or not isinstance(end, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', begin) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', end):
            raise HTTPException(409, 'Source contains an unsupported date format')
        # Whole period totals are never prorated to a narrower dashboard date range.
        if start_date and (begin < str(start_date) or end > str(end_date)): continue
        for metric in sorted(metrics):
            raw = row.get(metric)
            if raw is None: continue
            number, status = numeric(raw, metric == 'cost_micros')
            result.append({'provider': account.provider, 'account_id': account.id, 'account_name': account.name,
                'snapshot_id': saved['id'], 'collected_at': saved['collected_at'], 'source_table': source_table,
                'entity_id': str(row.get('campaign_id') or account.remote_id), 'entity_name': str(row.get('campaign_name') or account.name),
                'date_start': begin, 'date_end': end, 'timezone': 'UTC' if source_table == 'Snapshot metrics' else window.get('timezone', ''),
                'currency': row.get('currency') or context.get('currency', ''), 'metric': metric, 'metric_raw': str(raw),
                'metric_number': number, 'unit': 'currency' if metric in ('spend', 'cost_micros') else 'native_count',
                'meaning': report.get('semantics', {}).get(metric, metric), 'value_status': status,
                'coverage': json.dumps(report.get('coverage', {}), ensure_ascii=False), 'notice': report.get('notice', '')})
    if len(result) > 4000: raise HTTPException(409, 'Source exceeds the Looker row limit; choose a smaller table')
    return JSONResponse({'schema_version': 1, 'schema': field_schema(), 'rows': result,
        'snapshot_id': saved['id'], 'collected_at': saved['collected_at'], 'notice': 'Saved snapshot only; no provider refresh. Choose one table and metric per chart. Period rows excluded when not wholly inside the requested range. No automatic summing across metrics, currencies, or account/campaign grains.'}, headers={'Cache-Control': 'no-store'})
