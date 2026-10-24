import json
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

pytest.importorskip("torch")
pytest.importorskip("mlflow")
pytest.importorskip("psutil")

from izge.training.config import approved_config
from izge.training.runner import create_app, read_token


def wait(client, job_id, states, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = client.get(f'/jobs/{job_id}').json()
        if job['status'] in states:
            return job
        time.sleep(0.05)
    pytest.fail(f'job did not reach {states}: {job}')


@pytest.fixture
def runner(tmp_path):
    registry = tmp_path / 'approved'
    registry.mkdir()
    for name, minutes, epochs in [('smoke', 3, 3), ('timeout', 0.001, 100), ('long', 3, 100)]:
        config = approved_config('smoke', {}).model_dump()
        config.update(name=name)
        config['params']['epochs'] = epochs
        config['budget']['max_wall_minutes'] = minutes
        (registry / f'{name}.yaml').write_text(json.dumps(config))
    token = tmp_path / 'token'
    app = create_app(tmp_path / 'runs', token, registry)
    with TestClient(app) as client:
        client.headers['Authorization'] = f'Bearer {token.read_text()}'
        yield client, tmp_path / 'runs', token, registry


def test_auth_config_and_real_smoke(runner):
    client, root, token, _ = runner
    assert token.stat().st_mode & 0o777 == 0o600
    assert client.get('/health', headers={'Authorization': 'Bearer bad'}).status_code == 401
    assert client.post('/jobs', json={'config_name': 'smoke', 'overrides': {'command': 'sh'}}).status_code == 422
    response = client.post('/jobs', json={'config_name': 'smoke', 'overrides': {'device': 'cpu'}})
    assert response.status_code == 201
    job_id = response.json()['id']
    assert wait(client, job_id, {'completed', 'failed'})['status'] == 'completed'
    directory = root / job_id
    status = json.loads((directory / 'status.json').read_text())
    assert status['infrastructure_smoke'] and status['mlflow_run_id']
    assert len(client.get(f'/jobs/{job_id}/metrics').json()) == 3
    assert client.get(f'/jobs/{job_id}/logs?tail=1').json()['lines']
    assert all((directory / path).is_file() for path in ['resources.jsonl', 'checkpoints/last.pt', 'checkpoints/best.pt'])
    assert client.get('/health').json()['running_jobs'] == 0
    assert client.post(f'/jobs/{job_id}/resume').status_code == 409


def test_cancel_timeout_and_queue(runner):
    client, root, _, _ = runner
    first = client.post('/jobs', json={'config_name': 'long'}).json()['id']
    wait(client, first, {'running'})
    second = client.post('/jobs', json={'config_name': 'smoke'}).json()['id']
    assert client.get(f'/jobs/{second}').json()['status'] == 'queued'
    assert client.post(f'/jobs/{second}/cancel').json()['status'] == 'cancelled'
    client.post(f'/jobs/{first}/cancel')
    assert wait(client, first, {'cancelled'})['status'] == 'cancelled'
    assert json.loads((root / first / 'status.json').read_text())['status'] == 'cancelled'
    timeout = client.post('/jobs', json={'config_name': 'timeout'}).json()['id']
    assert wait(client, timeout, {'timed_out'})['status'] == 'timed_out'
    print(f'cancel transcript: {first}=cancelled; queued {second}=cancelled; {timeout}=timed_out')


def test_interrupted_recovery_and_exclusive_lock(tmp_path):
    root, token = tmp_path / 'runs', tmp_path / 'token'
    with TestClient(create_app(root, token)):
        with pytest.raises(BlockingIOError):
            with TestClient(create_app(root, token)):
                pass
    job_id = 'orphan'
    (root / job_id).mkdir()
    with sqlite3.connect(root / 'jobs.sqlite3') as db:
        db.execute('INSERT INTO jobs (id,state,config,created) VALUES (?,?,?,?)',
                   (job_id, 'running', approved_config('smoke', {}).model_dump_json(), time.time()))
    with TestClient(create_app(root, token)) as client:
        client.headers['Authorization'] = f'Bearer {token.read_text()}'
        assert client.get(f'/jobs/{job_id}').json()['status'] == 'interrupted'
        assert client.post(f'/jobs/{job_id}/resume').status_code == 409


def test_token_permissions(tmp_path):
    path = tmp_path / 'token'
    read_token(path)
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match='0600'):
        read_token(path)


def test_recovered_checkpoint_can_resume(tmp_path):
    root, token = tmp_path / 'runs', tmp_path / 'token'
    with TestClient(create_app(root, token)) as client:
        client.headers['Authorization'] = f'Bearer {token.read_text()}'
        job_id = client.post('/jobs', json={'config_name': 'smoke', 'overrides': {'device': 'cpu'}}).json()['id']
        assert wait(client, job_id, {'completed', 'failed'})['status'] == 'completed'
    # Simulate a crash after checkpoint creation and before the queue completion commit.
    with sqlite3.connect(root / 'jobs.sqlite3') as db:
        db.execute("UPDATE jobs SET state='running',pid=NULL,process_started=NULL WHERE id=?", (job_id,))
    original_id = json.loads((root / job_id / 'status.json').read_text())['mlflow_run_id']
    with TestClient(create_app(root, token)) as client:
        client.headers['Authorization'] = f'Bearer {token.read_text()}'
        assert client.get(f'/jobs/{job_id}').json()['resume_available']
        assert client.post(f'/jobs/{job_id}/resume').status_code == 200
        assert wait(client, job_id, {'completed', 'failed'})['status'] == 'completed'
    assert json.loads((root / job_id / 'status.json').read_text())['mlflow_run_id'] == original_id
