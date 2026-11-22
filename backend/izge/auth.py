"""Optional bearer-token guard for the HTTP API.

Off by default so local development keeps working. When IZGE_API_TOKEN is set, every request under
/api/ except /api/health must carry `Authorization: Bearer <token>`. The bundled web UI does not send
this header yet, so the guard is meant for scripted/API use or for deployments behind a reverse proxy
that injects it. This is a single shared secret, not user accounts or authorization.
"""
import hmac
import os

from starlette.responses import JSONResponse

OPEN_PATHS = frozenset({'/api/health'})


def configured_token() -> str:
    return os.getenv('IZGE_API_TOKEN', '').strip()


class ApiToken:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = configured_token()
        path = scope.get('path', '')
        if scope['type'] != 'http' or not token or not path.startswith('/api/') or path in OPEN_PATHS:
            return await self.app(scope, receive, send)
        supplied = dict(scope.get('headers', [])).get(b'authorization', b'').decode('latin-1')
        scheme, _, value = supplied.partition(' ')
        if scheme.lower() == 'bearer' and hmac.compare_digest(value.encode(), token.encode()):
            return await self.app(scope, receive, send)
        response = JSONResponse(status_code=401, content={'detail': 'Unauthorized'},
                                headers={'WWW-Authenticate': 'Bearer'})
        return await response(scope, receive, send)
