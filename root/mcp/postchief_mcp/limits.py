"""Bound authorization request sizes and rate-limit unauthenticated endpoints."""
from collections import OrderedDict, deque
from time import monotonic
from fastapi.responses import JSONResponse


def install_limits(app):
    buckets = OrderedDict()
    paths = {f'/api/mcp/oauth/{name}' for name in ('authorize', 'token', 'revoke', 'consent')}
    global_buckets = {path: deque() for path in paths}

    @app.middleware('http')
    async def authorization_limits(request, call_next):
        if not request.url.path.startswith('/api/mcp/oauth/'):
            return await call_next(request)
        if request.url.path not in paths:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        global_times = global_buckets[request.url.path]
        at = monotonic()
        while global_times and global_times[0] <= at - 60: global_times.popleft()
        if len(global_times) >= 120:
            return JSONResponse({'error': 'temporarily_unavailable'}, status_code=429,
                headers={'Retry-After': '60', 'Cache-Control': 'no-store'})
        global_times.append(at)
        host = request.client.host if request.client else 'unknown'
        bucket_key = (host, request.url.path)
        times = buckets.setdefault(bucket_key, deque())
        buckets.move_to_end(bucket_key)
        while len(buckets) > 1024: buckets.popitem(last=False)
        while times and times[0] <= at - 60: times.popleft()
        if len(times) >= 60:
            return JSONResponse({'error': 'temporarily_unavailable'}, status_code=429,
                headers={'Retry-After': '60', 'Cache-Control': 'no-store'})
        times.append(at)
        if len(request.url.query) > 8192:
            return JSONResponse({'error': 'invalid_request'}, status_code=413)
        if request.method == 'POST':
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 8192: return JSONResponse({'error': 'invalid_request'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
