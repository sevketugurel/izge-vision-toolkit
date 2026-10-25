from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from fastapi import HTTPException
import pytest

from izge import admin_swarm as swarm


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/stolen")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            if self.path == "/malformed":
                self.wfile.write(b"not-json PASSWORD")
            else:
                self.wfile.write(
                    json.dumps(
                        {"tasks": [{"id": "a", "task": "izge", "credentialKeySuffix": "PRIVATE"}], "total": 1}
                    ).encode()
                )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("IZGE_SWARM_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("IZGE_SWARM_TOKEN", "unit-fixture-credential")
    try:
        yield seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_adapter_real_http_contract_redaction_and_no_redirect(upstream):
    value = swarm.tasks(100)
    assert value["tasks"] == [{"id": "a", "task": "izge"}]
    assert "offset=100" in upstream[0][0]
    assert upstream[0][1] == "Bearer unit-fixture-credential"
    with pytest.raises(HTTPException):
        swarm.request("GET", "/redirect")
    assert not any(path == "/stolen" for path, _ in upstream)
    with pytest.raises(HTTPException) as error:
        swarm.request("GET", "/malformed")
    assert error.value.status_code == 503 and "PASSWORD" not in error.value.detail
    assert "unit-fixture-credential" not in json.dumps(
        swarm.clean({"text": "unit-fixture-credential token=OTHER rtsp://x:y@camera"})
    )
    assert "OTHER" not in swarm.clean("token=OTHER")


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example",
        "http://user:secret@localhost:3013",
        "http://localhost:3013/path",
        "http://localhost:3013?token=secret",
    ],
)
def test_external_and_credential_urls_rejected(upstream, monkeypatch, url):
    monkeypatch.setenv("IZGE_SWARM_URL", url)
    with pytest.raises(HTTPException) as error:
        swarm.request("GET", "/api/tasks")
    assert error.value.status_code == 503 and upstream == []


def test_missing_credentials_and_resource_failures_are_explicit(monkeypatch):
    monkeypatch.delenv("IZGE_SWARM_TOKEN", raising=False)
    monkeypatch.delenv("IZGE_SWARM_TOKEN_FILE", raising=False)
    with pytest.raises(HTTPException, match="503"):
        swarm.tasks()
    assert all(r["status"] == "unavailable" for r in swarm.operations().values())
