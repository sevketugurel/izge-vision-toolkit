import json

from izge.training import client


def test_client_submit_and_cancel(monkeypatch, capsys):
    calls = []
    def request(method, path, data=None):
        calls.append((method, path, data))
        return {'status': 'ok'}
    monkeypatch.setattr(client, 'request', request)
    monkeypatch.setattr('sys.argv', ['client', 'submit', 'smoke', '--overrides', '{"device":"cpu"}'])
    client.main()
    assert calls[-1] == ('POST', '/jobs', {'config_name': 'smoke', 'overrides': {'device': 'cpu'}})
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'
    monkeypatch.setattr('sys.argv', ['client', 'cancel', 'abc'])
    client.main()
    assert calls[-1][:2] == ('POST', '/jobs/abc/cancel')


def test_bearer_is_not_forwarded_on_redirect(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from urllib.error import HTTPError
    import pytest

    reached = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/steal')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = tmp_path / 'token'
    token.write_text('unit-test-token-not-a-real-credential')
    monkeypatch.setenv('IZGE_RUNNER_TOKEN_FILE', str(token))
    monkeypatch.setenv('IZGE_RUNNER_URL', f'http://127.0.0.1:{server.server_port}')
    try:
        with pytest.raises(HTTPError, match='302'):
            client.request('GET', '/health')
        assert reached == ['/health']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
