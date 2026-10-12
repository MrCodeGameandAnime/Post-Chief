import pytest
import httpx
from postchief_mcp.client import ApiBridge


@pytest.mark.asyncio
async def test_bridge_timeout_does_not_replay_mutation(app, monkeypatch):
    calls = []
    async def interrupted(self, method, path, **kwargs):
        calls.append((method, path))
        raise TimeoutError()
    monkeypatch.setattr(httpx.AsyncClient, 'request', interrupted)
    result = await ApiBridge(app).request('POST', '/api/agent/actions', 'test-only',
        json={'action': 'campaign.publish', 'target_id': 'test-only'})
    assert result['status'] == 'outcome_unknown'
    assert result['retryable'] is False
    assert calls == [('POST', '/api/agent/actions')]


@pytest.mark.asyncio
async def test_bridge_response_bounds_preserve_mutation_uncertainty(app, monkeypatch):
    async def too_large(self, method, path, **kwargs):
        return httpx.Response(200, content=b'x' * 1_048_577)
    monkeypatch.setattr(httpx.AsyncClient, 'request', too_large)
    bridge = ApiBridge(app)
    for method, path in [('GET', '/api/campaigns'), ('POST', '/api/agent/actions')]:
        result = await bridge.request(method, path, 'test-only')
        assert result['status'] == 'result_too_large'
        assert result['mutation_may_have_completed'] == (method == 'POST')
        assert result['retryable'] is False
