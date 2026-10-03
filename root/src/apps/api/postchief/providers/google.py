"""Shared confidential Google OAuth grants; tokens never enter public records."""
from datetime import datetime, timedelta, timezone
import httpx
from provider_contracts import ProviderError, ErrorReason

TOKEN_URL = 'https://oauth2.googleapis.com/token'


def token_credentials(value, previous=None):
    previous = previous or {}
    access = value.get('access_token')
    refresh = value.get('refresh_token') or previous.get('refresh_token')
    seconds = value.get('expires_in')
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or type(seconds) is not int or not 0 < seconds <= 86400:
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'Google did not return a renewable grant; reconnect and grant offline access')
    return {**previous, 'access_token': access, 'refresh_token': refresh,
        'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}


async def refresh_google(http, settings, credentials):
    try:
        response = await http.post(TOKEN_URL, data={'grant_type': 'refresh_token',
            'client_id': settings.google_client_id, 'client_secret': settings.google_client_secret.get_secret_value(),
            'refresh_token': credentials.get('refresh_token', '')})
        value = response.json()
    except (httpx.HTTPError, ValueError):
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'Google renewal could not be confirmed; reconnect')
    if response.status_code == 429:
        raise ProviderError(ErrorReason.RATE_LIMITED, 'Google renewal is rate limited; try again later', retryable=True)
    if response.is_error or not isinstance(value, dict) or value.get('error'):
        raise ProviderError(ErrorReason.AUTH_REVOKED, 'Google renewal failed; reconnect and check consent')
    credentials.update(token_credentials(value, credentials))
