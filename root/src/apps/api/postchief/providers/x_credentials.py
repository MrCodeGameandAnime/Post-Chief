"""Serialize rotating refresh grants with a durable, encrypted account intent."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from postchief.models import SocialAccount
from postchief.vault import Vault
from provider_contracts import ProviderError, ErrorReason


async def current_x_credentials(sessions, settings, account_id, org_id, provider, provider_name='x'):
    vault = Vault(settings.encryption_key.get_secret_value())
    now = datetime.now(timezone.utc)
    label = 'X' if provider_name == 'x' else 'Pinterest'
    with sessions() as db:
        account = db.scalar(select(SocialAccount).where(SocialAccount.id == account_id,
            SocialAccount.org_id == org_id, SocialAccount.provider == provider_name).with_for_update())
        if not account or not account.active or not account.credentials:
            raise ProviderError(ErrorReason.AUTH_REVOKED, 'Reconnect ' + label)
        credentials = vault.decrypt(account.credentials)
        if credentials.get('_refresh_started'):
            started = datetime.fromisoformat(credentials['_refresh_started'])
            if now - started < timedelta(minutes=2):
                raise ProviderError(ErrorReason.NETWORK_ERROR, label + ' account renewal is in progress; try again shortly', retryable=True)
            raise ProviderError(ErrorReason.AUTH_REVOKED, label + ' account renewal was interrupted; reconnect')
        if datetime.fromisoformat(credentials['expires_at']) > now + timedelta(minutes=5):
            return credentials
        original = dict(credentials)
        previous = account.credentials
        credentials['_refresh_started'] = now.isoformat()
        intent = vault.encrypt(credentials)
        claimed = db.execute(update(SocialAccount).where(SocialAccount.id == account_id,
            SocialAccount.org_id == org_id, SocialAccount.credentials == previous, SocialAccount.active.is_(True))
            .values(credentials=intent))
        if claimed.rowcount != 1:
            db.rollback()
            raise ProviderError(ErrorReason.NETWORK_ERROR, label + ' account renewal is already claimed; try again', retryable=True)
        db.commit()
    # A crash or lost response retains the intent, preventing reuse of a rotated token.
    try:
        await provider.refresh_auth(credentials)
    except ProviderError as error:
        if error.retryable:
            with sessions() as db:
                db.execute(update(SocialAccount).where(SocialAccount.id == account_id,
                    SocialAccount.org_id == org_id, SocialAccount.credentials == intent, SocialAccount.active.is_(True))
                    .values(credentials=vault.encrypt(original)))
                db.commit()
        raise
    credentials.pop('_refresh_started', None)
    with sessions() as db:
        saved = db.execute(update(SocialAccount).where(SocialAccount.id == account_id,
            SocialAccount.org_id == org_id, SocialAccount.credentials == intent, SocialAccount.active.is_(True))
            .values(credentials=vault.encrypt(credentials), expires_at=datetime.fromisoformat(credentials['expires_at'])))
        db.commit()
        if saved.rowcount != 1:
            raise ProviderError(ErrorReason.NETWORK_ERROR, label + ' connection changed during renewal; try again', retryable=True)
    return credentials
