from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from starlette.applications import Starlette
from starlette.routing import Route

from izge.auth import ApiToken


def make_client():
    async def ok(request):
        return PlainTextResponse('ok')
    app = Starlette(routes=[Route('/api/health', ok), Route('/api/v1/farms', ok), Route('/index.html', ok)])
    return TestClient(ApiToken(app))


def test_guard_is_off_without_token(monkeypatch):
    monkeypatch.delenv('IZGE_API_TOKEN', raising=False)
    assert make_client().get('/api/v1/farms').status_code == 200


def test_guard_rejects_missing_wrong_and_malformed_credentials(monkeypatch):
    monkeypatch.setenv('IZGE_API_TOKEN', 'correct-horse-test-token')
    c = make_client()
    assert c.get('/api/v1/farms').status_code == 401
    assert c.get('/api/v1/farms').headers['www-authenticate'] == 'Bearer'
    assert c.get('/api/v1/farms', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert c.get('/api/v1/farms', headers={'Authorization': 'Basic correct-horse-test-token'}).status_code == 401
    assert c.get('/api/v1/farms', headers={'Authorization': 'correct-horse-test-token'}).status_code == 401


def test_guard_accepts_correct_token_and_leaves_health_and_static_open(monkeypatch):
    monkeypatch.setenv('IZGE_API_TOKEN', 'correct-horse-test-token')
    c = make_client()
    assert c.get('/api/v1/farms', headers={'Authorization': 'Bearer correct-horse-test-token'}).status_code == 200
    assert c.get('/api/health').status_code == 200
    assert c.get('/index.html').status_code == 200


def test_real_app_is_guarded_when_token_is_set(monkeypatch):
    from izge.api import app
    monkeypatch.setenv('IZGE_API_TOKEN', 'correct-horse-test-token')
    c = TestClient(app)
    assert c.get('/api/config').status_code == 401
    assert c.get('/api/config', headers={'Authorization': 'Bearer correct-horse-test-token'}).status_code == 200
    monkeypatch.delenv('IZGE_API_TOKEN')
    assert c.get('/api/config').status_code == 200
