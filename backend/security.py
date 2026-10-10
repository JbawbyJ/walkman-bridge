"""Exact-origin, per-launch capability boundary and bounded multipart parsing."""
from __future__ import annotations
import hmac
from starlette.formparsers import MultiPartParser, MultiPartException
from starlette.responses import JSONResponse
from media_store import MAX_FILE, MAX_BATCH

COOKIE = 'nightops_session'
CSP = "; ".join(["default-src 'none'", "script-src 'self'", "style-src 'self' 'unsafe-inline'",
    "font-src 'self'", "img-src 'self' data:", "connect-src 'self'", "media-src 'self' blob:",
    "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'", "object-src 'none'"])


class Boundary:
    def __init__(self, app, token, origin):
        self.app, self.token, self.origin = app, token, origin

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        host = headers.get(b'host', b'').decode('latin1')
        origin = headers.get(b'origin', b'').decode('latin1')
        def token_matches(value):
            return bool(self.token) and hmac.compare_digest(value, self.token.encode())
        native = token_matches(headers.get(b'x-nightops-token', b''))
        cookie = headers.get(b'cookie', b'')
        cookies = dict(x.strip().split(b'=', 1) for x in cookie.split(b';') if b'=' in x)
        browser = token_matches(cookies.get(COOKIE.encode(), b''))
        forbidden = host != self.origin.removeprefix('http://') or bool(origin and origin != self.origin)
        if scope['path'].startswith('/api/'):
            forbidden |= not (native or browser)
            if browser and not native:
                forbidden |= headers.get(b'sec-fetch-site', b'same-origin') not in (b'same-origin', b'none')
                if scope['method'] not in ('GET', 'HEAD'):
                    forbidden |= origin != self.origin
            if scope['path'].startswith('/api/internal/') or scope['path'] == '/api/shutdown/drain':
                forbidden |= not native
        if forbidden:
            return await JSONResponse({'detail': 'Unauthorized origin or session'}, status_code=403)(scope, receive, send)
        try:
            length = int(headers.get(b'content-length', b'0'))
        except ValueError:
            length = -1
        is_import = scope['path'] in ('/api/media/import', '/api/upload')
        limit = MAX_BATCH + 1024 * 1024 if is_import else (8192 if scope['path'] == '/api/media/import-link' else 1024 * 1024)
        if length < 0 or length > limit:
            return await JSONResponse({'detail': 'Request exceeds the size limit'}, status_code=413)(scope, receive, send)
        received = 0
        async def bounded_receive():
            nonlocal received
            event = await receive()
            received += len(event.get('body', b''))
            if received > limit:
                raise MultiPartException('Request exceeds the size limit')
            return event
        async def secure_send(event):
            if event['type'] == 'http.response.start':
                event['headers'] = list(event['headers']) + [
                    (b'content-security-policy', CSP.encode()), (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'), (b'cross-origin-resource-policy', b'same-origin'),
                    (b'cache-control', b'no-store')]
            await send(event)
        await self.app(scope, bounded_receive, secure_send)


class LimitedMultipart(MultiPartParser):
    def on_part_begin(self):
        super().on_part_begin()
        self._part_bytes = 0

    def on_part_data(self, data, start, end):
        self._part_bytes += end - start
        self._total_bytes = getattr(self, '_total_bytes', 0) + end - start
        if self._part_bytes > MAX_FILE or self._total_bytes > MAX_BATCH:
            raise MultiPartException('Audio import exceeds the file or batch limit')
        super().on_part_data(data, start, end)
