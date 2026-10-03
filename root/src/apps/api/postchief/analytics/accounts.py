"""Durable, scoped account report snapshots shared by reporting integrations."""
from datetime import datetime, timedelta, timezone
import asyncio
import json
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from postchief.auth import Actor, current_actor, require_scope
from postchief.db import get_db
from postchief.models import SocialAccount, AuditEvent
from postchief.campaigns.service import audit, iso
from postchief.providers.registry import PROVIDERS, get_provider
from postchief.providers.x_credentials import current_x_credentials
from provider_contracts import ProviderError, ErrorReason

router = APIRouter(prefix='/reports', tags=['Account reports'])


def reporting_account(db, actor, account_id, lock=False):
    query = select(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.org_id == actor.org_id)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row or not getattr(PROVIDERS.get(row.provider), 'account_reporting', False):
        raise HTTPException(404, 'Reporting connection not found')
    return row


def event_query(org_id, account_id, actions):
    return select(AuditEvent).where(AuditEvent.org_id == org_id,
        AuditEvent.action.in_(actions), AuditEvent.details['account_id'].as_string() == account_id)


def snapshot(row):
    return {'id': row.id, 'collected_at': iso(row.created_at), 'report': row.details['report']} if row else None


def latest_report(db, actor, row):
    latest = db.scalar(event_query(actor.org_id, row.id, ['account.report']).order_by(AuditEvent.created_at.desc()).limit(1))
    outcome = db.scalar(event_query(actor.org_id, row.id, ['account.report', 'account.report_error']).order_by(AuditEvent.created_at.desc()).limit(1))
    return {'account_id': row.id, 'provider': row.provider, 'account_name': row.name, 'active': row.active,
            'latest': snapshot(latest), 'error': outcome.details.get('error') if outcome and outcome.action == 'account.report_error' else None}


@router.get('/accounts')
def accounts(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0),
             actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, 'analytics:read')
    names = [name for name, cls in PROVIDERS.items() if getattr(cls, 'account_reporting', False)]
    rows = db.scalars(select(SocialAccount).where(SocialAccount.org_id == actor.org_id, SocialAccount.provider.in_(names))
        .order_by(SocialAccount.created_at.desc()).offset(offset).limit(limit))
    return [latest_report(db, actor, row) for row in rows]


@router.get('/accounts/{account_id}')
def latest(account_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, 'analytics:read')
    return latest_report(db, actor, reporting_account(db, actor, account_id))


@router.get('/accounts/{account_id}/history')
def history(account_id: str, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
            actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, 'analytics:read'); reporting_account(db, actor, account_id)
    rows = db.scalars(event_query(actor.org_id, account_id, ['account.report']).order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit))
    return [snapshot(row) for row in rows]


@router.get('/accounts/{account_id}/export')
def export(account_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, 'analytics:read')
    value = latest_report(db, actor, reporting_account(db, actor, account_id))
    return JSONResponse(value, headers={'Content-Disposition': 'attachment; filename="account-report.json"', 'Cache-Control': 'no-store'})


@router.post('/accounts/{account_id}/refresh')
async def refresh(account_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor, 'analytics:collect')
    names = [name for name, cls in PROVIDERS.items() if getattr(cls, 'account_reporting', False)]
    # Acquire a write lock before reading the cooldown on SQLite too.
    db.execute(update(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.org_id == actor.org_id,
        SocialAccount.provider.in_(names)).values(name=SocialAccount.name))
    account = reporting_account(db, actor, account_id, lock=True)
    if not account.active or not account.credentials:
        raise HTTPException(409, 'Reconnect this reporting account before collecting')
    requested = db.scalar(event_query(actor.org_id, account_id, ['account.report_requested']).order_by(AuditEvent.created_at.desc()).limit(1))
    if requested and datetime.fromisoformat(iso(requested.created_at)) > datetime.now(timezone.utc) - timedelta(seconds=60):
        raise HTTPException(429, 'This report was requested recently; wait one minute before refreshing')
    audit(db, actor, 'account.report_requested', {'account_id': account_id, 'provider': account.provider})
    db.commit()
    provider_name = account.provider
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            provider = get_provider(provider_name, http, request.app.state.settings)
            if getattr(provider, 'requires_token_refresh', True):
                credentials = await current_x_credentials(request.app.state.sessions, request.app.state.settings,
                    account_id, actor.org_id, provider, provider_name=provider_name)
            else:
                from postchief.vault import Vault
                credentials = Vault(request.app.state.settings.encryption_key.get_secret_value()).decrypt(account.credentials)
            try:
                report = await asyncio.wait_for(provider.get_account_report(credentials), timeout=90)
            except TimeoutError:
                raise ProviderError(ErrorReason.NETWORK_ERROR, 'Account report collection timed out; last successful report retained', retryable=True)
        if len(json.dumps(report, ensure_ascii=False, allow_nan=False).encode()) > 256 * 1024:
            raise ProviderError(ErrorReason.PROVIDER_ERROR, 'The account report exceeded the supported snapshot size')
        # Disconnect or reconnect during collection invalidates this result.
        account = reporting_account(db, actor, account_id, lock=True)
        from postchief.vault import Vault
        current = Vault(request.app.state.settings.encryption_key.get_secret_value()).decrypt(account.credentials) if account.credentials else {}
        if not account.active or current != credentials:
            raise HTTPException(409, 'Connection changed during collection; request the report again')
        audit(db, actor, 'account.report', {'account_id': account_id, 'provider': provider_name, 'report': report})
        db.commit()
    except ProviderError as error:
        audit(db, actor, 'account.report_error', {'account_id': account_id, 'provider': provider_name, 'error': error.to_dict()})
        db.commit()
        raise
    return latest_report(db, actor, account)
