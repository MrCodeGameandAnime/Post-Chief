"""Explicit API bridge; never proxy arbitrary URLs or owner credentials."""
import re
import anyio
import httpx

READ_PATHS = (
    r'/api/auth/me', r'/api/settings/autonomy', r'/api/connections',
    r'/api/campaigns(?:/[A-Za-z0-9-]+)?', r'/api/publications/[A-Za-z0-9-]+',
    r'/api/assets', r'/api/github/(?:workspace|repositories|content)',
    r'/api/analytics(?:/publications/[A-Za-z0-9-]+)?',
    r'/api/reports/accounts(?:/[A-Za-z0-9-]+(?:/history)?)?',
    r'/api/(?:approvals|audit|agent/runs)',
)
WRITE_PATHS = (r'/api/agent/actions', r'/api/agent/runs', r'/api/assets/github')


class ApiBridge:
    def __init__(self, app):
        self.app = app

    async def request(self, method, path, token, *, params=None, json=None):
        patterns = READ_PATHS if method == 'GET' else WRITE_PATHS if method == 'POST' else ()
        if not any(re.fullmatch(pattern, path) for pattern in patterns):
            raise ValueError('API route is not exposed through MCP')
        try:
            with anyio.fail_after(35):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                        base_url=self.app.state.settings.public_url, follow_redirects=False) as client:
                    response = await client.request(method, path, params=params, json=json,
                        headers={'Authorization': 'Bearer ' + token})
        except (TimeoutError, httpx.HTTPError):
            return {'status': 'outcome_unknown' if method != 'GET' else 'error',
                'error': 'Request interrupted; read current state before any retry', 'retryable': False}
        if len(response.content) > 1_048_576:
            return {'status': 'result_too_large', 'http_status': response.status_code,
                'error': 'Response exceeds the tool limit; use smaller pages or a specific record',
                'mutation_may_have_completed': method != 'GET', 'retryable': False}
        try: data = response.json()
        except ValueError:
            return {'status': 'error', 'http_status': response.status_code, 'error': 'Unexpected API response'}
        if response.status_code == 428:
            return {'status': 'approval_required', 'http_status': 428, 'approval': data['detail']}
        if response.is_error:
            return {'status': 'error', 'http_status': response.status_code, 'error': data, 'retryable': False}
        return {'status': 'ok', 'http_status': response.status_code, 'data': data}
