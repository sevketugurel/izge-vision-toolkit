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
