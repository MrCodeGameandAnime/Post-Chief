"""Validate maintained Twitch grants on worker startup and at least hourly."""
from datetime import datetime, timedelta, timezone
from cryptography.fernet import InvalidToken
import httpx
from sqlalchemy import select, update, func, or_
from postchief.models import SocialAccount, AuditEvent
from postchief.vault import Vault
from postchief.providers.twitch import TwitchProvider
from postchief.providers.x_credentials import current_x_credentials
from provider_contracts import ProviderError, ErrorReason


def due_validation(sessions, startup=False):
    with sessions() as db:
        latest = select(AuditEvent.org_id.label('org_id'), AuditEvent.details['account_id'].as_string().label('account_id'),
                        func.max(AuditEvent.created_at).label('requested')).where(AuditEvent.action == 'connection.twitch_validation_requested')
        latest = latest.group_by(AuditEvent.org_id, AuditEvent.details['account_id'].as_string()).subquery()
        query = select(SocialAccount.id).outerjoin(latest, (latest.c.account_id == SocialAccount.id) & (latest.c.org_id == SocialAccount.org_id))
        query = query.where(SocialAccount.provider == 'twitch', SocialAccount.active.is_(True))
        if not startup: query = query.where(or_(latest.c.requested.is_(None), latest.c.requested <= datetime.now(timezone.utc) - timedelta(minutes=50)))
        return list(db.scalars(query.order_by(latest.c.requested.asc(), SocialAccount.created_at).limit(1000)))


async def validate_connection(account_id, sessions, settings, startup=False):
    now = datetime.now(timezone.utc); vault = Vault(settings.encryption_key.get_secret_value())
    with sessions() as db:
        db.execute(update(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.provider == 'twitch').values(name=SocialAccount.name))
        account = db.scalar(select(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.provider == 'twitch').with_for_update())
        if not account or not account.active: return
        last = db.scalar(select(AuditEvent).where(AuditEvent.org_id == account.org_id, AuditEvent.action == 'connection.twitch_validation_requested',
                            AuditEvent.details['account_id'].as_string() == account.id).order_by(AuditEvent.created_at.desc()).limit(1))
        if last and not startup:
            checked = last.created_at.replace(tzinfo=timezone.utc) if last.created_at.tzinfo is None else last.created_at
            if checked > now - timedelta(minutes=50): return
        org_id, original = account.org_id, account.credentials
        db.add(AuditEvent(org_id=org_id, actor_id='worker', action='connection.twitch_validation_requested', details={'account_id': account_id}))
        db.commit()
    credentials, error = None, None
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            adapter = TwitchProvider(http, settings)
            credentials = await current_x_credentials(sessions, settings, account_id, org_id, adapter, provider_name='twitch')
            await adapter.inspect(credentials)
    except ProviderError as value: error = value
    except (InvalidToken, ValueError, KeyError):
        error = ProviderError(ErrorReason.AUTH_REVOKED, 'Stored Twitch authorization is invalid; reconnect')
    with sessions() as db:
        account = db.scalar(select(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.org_id == org_id).with_for_update())
        if not account or not account.active: return
        try:
            current = vault.decrypt(account.credentials)
            unchanged = current == credentials if credentials is not None else account.credentials == original
            if error and error.reason == ErrorReason.AUTH_REVOKED and credentials is None and current.get('_refresh_started'):
                unchanged = current.get('access_token') == vault.decrypt(original).get('access_token')
        except (InvalidToken, ValueError): unchanged = account.credentials == original
        if not unchanged: return
        if error and error.reason in (ErrorReason.AUTH_REVOKED, ErrorReason.PERMISSION_MISSING):
            account.active, account.credentials, account.expires_at = False, '', None
        db.add(AuditEvent(org_id=org_id, actor_id='worker', action='connection.twitch_validation_error' if error else 'connection.twitch_validated',
                          details={'account_id': account_id, **({'error': error.to_dict()} if error else {})}))
        db.commit()
