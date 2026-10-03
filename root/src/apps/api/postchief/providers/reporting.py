"""Shared boundary for connections that provide context/reports, not publishing."""
from provider_contracts import Capabilities, ProviderError, ErrorReason


class ReportingProvider:
    reporting_only = True
    capabilities = Capabilities(text=False, analytics=True)
    limits = {'delivery': 'Account reports only; cannot publish campaigns'}
    idempotent = True

    def __init__(self, client, settings=None):
        self.http, self.settings = client, settings

    def validate(self, body, media):
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'This connection provides account reports and cannot publish campaigns')

    async def publish(self, *args, **kwargs):
        self.validate('', [])

    async def delete(self, *args, **kwargs):
        self.validate('', [])

    async def get_post_metrics(self, *args, **kwargs):
        raise ProviderError(ErrorReason.PERMISSION_MISSING, 'Use account reports for this connection')
