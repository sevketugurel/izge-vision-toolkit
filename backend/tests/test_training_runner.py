import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time

from fastapi.testclient import TestClient
import pytest

pytest.importorskip("torch")
pytest.importorskip("mlflow")
pytest.importorskip("psutil")

from izge.training.config import approved_config
from izge.training.runner import create_app, read_token


@pytest.fixture(autouse=True)
def isolated_tracking(tmp_path, monkeypatch):
    monkeypatch.setenv('MLFLOW_TRACKING_URI', (tmp_path / 'runs/mlruns').as_uri())
    monkeypatch.setenv('MLFLOW_ALLOW_FILE_STORE', 'true')


def assert_killed_artifacts(root, job_id):
    from mlflow import MlflowClient
    directory = root / job_id
    status = json.loads((directory / 'status.json').read_text())
    client = MlflowClient(tracking_uri=(root / 'mlruns').as_uri())
    run_id = status['mlflow_run_id']
    assert client.get_run(run_id).info.status == 'KILLED'
    artifact = client.download_artifacts(run_id, 'status.json')
    assert json.loads(Path(artifact).read_text()) == status
    artifacts = {item.path for item in client.list_artifacts(run_id)}
    for name in ('metrics.jsonl', 'resources.jsonl', 'train.log', 'process.log'):
        if (directory / name).exists():
            assert name in artifacts
    assert client.list_artifacts(run_id, 'checkpoints')
    assert client.list_artifacts(run_id, 'rfdetr')
    return run_id


@pytest.mark.parametrize('ending', ['cancel', 'timeout', 'shutdown'])
def test_killed_training_finalizes_mlflow(tmp_path, monkeypatch, ending):
    # Run real smoke training, then block after the first durable epoch so every
    # kill path has a live MLflow run and genuine artifacts to recover.
    monkeypatch.setenv('MLFLOW_ALLOW_FILE_STORE', 'true')
    original_popen = subprocess.Popen
    script = '''
import time
from izge.training import run
original = run.smoke
def paused(config, directory, device, resume, emit):
    def pause_after_epoch(row):
        emit(row)
        (directory / 'rfdetr').mkdir(exist_ok=True)
        (directory / 'rfdetr/checkpoint_0.ckpt').write_bytes(b'native-checkpoint-fixture')
        (directory / 'ready').touch()
        while True:
            time.sleep(1)
    original(config, directory, device, resume, pause_after_epoch)
run.smoke = paused
run.main()
'''
    def launch(command, **kwargs):
        if command[1:3] == ['-m', 'izge.training.run']:
            command = [command[0], '-c', script, *command[3:]]
        return original_popen(command, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    root, token, registry = tmp_path / 'runs', tmp_path / 'token', tmp_path / 'registry'
    registry.mkdir()
    config = approved_config('smoke', {'device': 'cpu'}).model_dump()
    config['budget']['max_wall_minutes'] = 0.3 if ending == 'timeout' else 3
    (registry / 'smoke.yaml').write_text(json.dumps(config))
    with TestClient(create_app(root, token, registry)) as client:
        client.headers['Authorization'] = f'Bearer {token.read_text()}'
        job_id = client.post('/jobs', json={'config_name': 'smoke'}).json()['id']
        deadline = time.monotonic() + 15
        while not (root / job_id / 'ready').exists():
            assert time.monotonic() < deadline, (root / job_id / 'process.log').read_text()
            time.sleep(0.02)
        if ending == 'cancel':
            client.post(f'/jobs/{job_id}/cancel')
        if ending != 'shutdown':
            wait(client, job_id, {'cancelled' if ending == 'cancel' else 'timed_out'})
    assert_killed_artifacts(root, job_id)


def test_docs_routes_are_disabled(runner):
    client, _, _, _ = runner
    client.headers.pop('Authorization')
    for path in ('/docs', '/redoc', '/openapi.json'):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize('native_checkpoint', [True, False])
def test_recovered_rfdetr_uses_native_checkpoint(tmp_path, monkeypatch, native_checkpoint, save_epoch_checkpoint):
    from mlflow import MlflowClient
    from izge.training.run import write_json
    monkeypatch.setenv('MLFLOW_ALLOW_FILE_STORE', 'true')
    root, token = tmp_path / 'runs', tmp_path / 'token'
    with TestClient(create_app(root, token)):
        pass
    directory = root / 'orphan'
    (directory / 'rfdetr').mkdir(parents=True)
    (directory / 'checkpoints').mkdir()
    (directory / 'rfdetr/checkpoint_1.ckpt').write_bytes(b'incomplete')
    if native_checkpoint:
        save_epoch_checkpoint(directory / 'rfdetr/checkpoint_0.ckpt', 0)
        (directory / 'checkpoints/best.pt').write_bytes(b'inference-weights-fixture')
    else:
        # A normalized copy alone cannot resume the RF-DETR adapter.
        (directory / 'checkpoints/last.pt').write_bytes(b'stale-copy-fixture')
    client = MlflowClient(tracking_uri=(root / 'mlruns').as_uri())
    experiment = client.create_experiment('recovery')
    run_id = client.create_run(experiment).info.run_id
    write_json(directory / 'status.json', {'status': 'running', 'mlflow_run_id': run_id})
    config = approved_config('rfdetr_detect', {})
    (directory / 'config.yaml').write_text(config.model_dump_json())
    with sqlite3.connect(root / 'jobs.sqlite3') as db:
        db.execute('INSERT INTO jobs (id,state,config,created) VALUES (?,?,?,?)',
                   ('orphan', 'running', config.model_dump_json(), time.time()))
    with TestClient(create_app(root, token)) as api:
        api.headers['Authorization'] = f'Bearer {token.read_text()}'
        job = api.get('/jobs/orphan').json()
        assert job['status'] == 'interrupted'
        assert job['resume_available'] is native_checkpoint
        if native_checkpoint:
            assert_killed_artifacts(root, 'orphan')
        else:
            assert client.get_run(run_id).info.status == 'KILLED'
        response = api.post('/jobs/orphan/resume')
        assert response.status_code == (200 if native_checkpoint else 409)


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


@pytest.mark.parametrize('after_cancel', ['wait', 'shutdown'])
def test_cancel_signals_the_process_group_once(tmp_path, monkeypatch, after_cancel):
    # macOS answers killpg with EPERM while the group's only member is exiting or a zombie, so a
    # second SIGKILL to a dying group crashed the worker now and then. Emulate that window: the
    # first signal lands after a delay and any repeat to the same group raises EPERM.
    real_killpg, calls = os.killpg, []

    def killpg(pgid, sig):
        calls.append(pgid)
        if calls.count(pgid) > 1:
            raise PermissionError(errno.EPERM, 'Operation not permitted')
        threading.Timer(0.3, real_killpg, (pgid, sig)).start()

    monkeypatch.setattr(os, 'killpg', killpg)
    registry = tmp_path / 'approved'
    registry.mkdir()
    config = approved_config('smoke', {'device': 'cpu'}).model_dump()
    config.update(name='long')
    config['params']['epochs'] = 100
    config['budget']['max_wall_minutes'] = 3
    (registry / 'long.yaml').write_text(json.dumps(config))
    root, token = tmp_path / 'runs', tmp_path / 'token'
    with TestClient(create_app(root, token, registry)) as client:
        client.headers['Authorization'] = f'Bearer {token.read_text()}'
        job_id = client.post('/jobs', json={'config_name': 'long'}).json()['id']
        wait(client, job_id, {'running'})
        client.post(f'/jobs/{job_id}/cancel')
        if after_cancel == 'wait':
            assert wait(client, job_id, {'cancelled'}, seconds=10)['status'] == 'cancelled'
    assert len(calls) == 1


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


def test_catalog_uses_runner_allowlist_and_requires_auth(runner):
    client, _, _, registry = runner
    (registry / 'invalid.yaml').write_text('command: sh')
    (registry / 'malformed.yaml').write_text('invalid: [')
    assert client.get('/configs', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    rows = client.get('/configs').json()
    assert {r['name'] for r in rows} == {'smoke', 'long', 'timeout'}
    assert all(r['eval']['split'] == 'val' for r in rows)
    assert all(r['budget']['max_epochs'] >= r['params']['epochs'] for r in rows)
