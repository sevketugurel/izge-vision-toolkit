"""Single-process native runner. Start with python -m izge.training.runner."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, contextmanager
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

import yaml

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from .config import ROOT, StrictModel, approved_config
from .run import log_run_artifacts, pick_device, tracking_uri, write_json


class Submission(StrictModel):
    config_name: str
    overrides: dict = Field(default_factory=dict)


def read_token(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(32))
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError('runner token must be a regular 0600 file')
    token = path.read_text().strip()
    if len(token) < 32:
        raise RuntimeError('runner token too short')
    return token


class JobStore:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (root / 'runner.lock').open('w')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, '
                       'config TEXT NOT NULL, created REAL NOT NULL, pid INTEGER, process_started REAL, '
                       'resume INTEGER NOT NULL DEFAULT 0)')
            # Stored process creation time prevents killing an unrelated reused PID.
            import psutil
            for row in db.execute("SELECT * FROM jobs WHERE state='running'").fetchall():
                try:
                    process = psutil.Process(row['pid'])
                    if process.create_time() == row['process_started'] and os.getpgid(process.pid) == process.pid:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                except (psutil.Error, ProcessLookupError, TypeError):
                    pass
                self.terminal_file(row['id'], 'interrupted')
            db.execute("UPDATE jobs SET state='interrupted' WHERE state='running'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'jobs.sqlite3')
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY created')]

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'job not found')
        return dict(row)

    def update(self, job_id, state):
        with self.connect() as db:
            db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job_id))

    def directory(self, job_id):
        self.get(job_id)  # IDs must come from the database, never arbitrary request paths.
        return self.root / job_id

    def terminal_file(self, job_id, state):
        path = self.root / job_id / 'status.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        write_json(path, {**data, 'status': state})
        if state in ('cancelled', 'timed_out', 'interrupted') and data.get('mlflow_run_id'):
            from mlflow import MlflowClient
            client = MlflowClient(tracking_uri=tracking_uri(self.root))
            run_id = data['mlflow_run_id']
            try:
                log_run_artifacts(client, run_id, path.parent)
            finally:
                client.set_terminated(run_id, status='KILLED')


def create_app(root=None, token_path=None, registry=None):
    root = Path(root or os.getenv('IZGE_RUNS_ROOT', Path.home() / '.izge-runner/runs')).resolve()
    token_path = Path(token_path or os.getenv('IZGE_RUNNER_TOKEN_FILE', Path.home() / '.izge-runner/token'))
    bearer = HTTPBearer(auto_error=False)
    process = None
    current_id = None
    stop_reason = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal process, stop_reason
        app.state.token = read_token(token_path)
        app.state.store = JobStore(root)
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            stop_reason = 'interrupted'
            # The worker owns process-group termination and reaping on cancellation.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if current_id:
                app.state.store.terminal_file(current_id, 'interrupted')
                app.state.store.update(current_id, 'interrupted')
            app.state.store.lock.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not hmac.compare_digest(credentials.credentials, app.state.token):
            raise HTTPException(401, 'invalid bearer token', headers={'WWW-Authenticate': 'Bearer'})

    def kill(child):
        # Cancel, the worker loop and shutdown can each ask for the same child. SIGKILL cannot be
        # caught, so one per group is enough, and a repeat can fail: macOS answers killpg with EPERM
        # while the group's only member is exiting or a zombie.
        if getattr(child, 'sigkill_sent', False):
            return
        child.sigkill_sent = True
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def worker():
        nonlocal process, current_id, stop_reason
        import psutil
        store = app.state.store
        while True:
            queued = next((row for row in store.rows() if row['state'] == 'queued'), None)
            if not queued:
                await asyncio.sleep(0.05)
                continue
            current_id, stop_reason = queued['id'], None
            directory = store.directory(current_id)
            config = json.loads(queued['config'])
            command = [sys.executable, '-m', 'izge.training.run', '--config', str(directory / 'config.yaml'),
                       '--run-dir', str(directory), '--wait-for-runner']
            if queued['resume']:
                command.append('--resume')
            env = os.environ.copy()
            env['PYTHONPATH'] = str(ROOT / 'backend')
            env['IZGE_RUNS_ROOT'] = str(root)
            # The child does not need runner/client or repository credentials.
            for key in list(env):
                if any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY')):
                    env.pop(key)
            try:
                with (directory / 'process.log').open('a') as log:
                    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                                               start_new_session=True, stdin=subprocess.PIPE, text=True)
                    started = psutil.Process(process.pid).create_time()
                    with store.connect() as db:
                        db.execute("UPDATE jobs SET state='running',pid=?,process_started=? WHERE id=?",
                                   (process.pid, started, current_id))
                    process.stdin.write('start\n')
                    process.stdin.close()
                    deadline = time.monotonic() + config['budget']['max_wall_minutes'] * 60
                    while process.poll() is None:
                        if stop_reason or time.monotonic() >= deadline:
                            stop_reason = stop_reason or 'timed_out'
                            kill(process)
                            break
                        await asyncio.sleep(0.05)
                    await asyncio.to_thread(process.wait)
                    state = stop_reason or ('completed' if process.returncode == 0 else 'failed')
                    store.terminal_file(current_id, state)
                    store.update(current_id, state)
            except asyncio.CancelledError:
                if process and process.poll() is None:
                    kill(process)
                    await asyncio.to_thread(process.wait)
                store.terminal_file(current_id, 'interrupted')
                store.update(current_id, 'interrupted')
                raise
            except (OSError, psutil.Error):
                if process and process.poll() is None:
                    kill(process)
                    await asyncio.to_thread(process.wait)
                store.update(current_id, 'failed')
                store.terminal_file(current_id, 'failed')
            finally:
                process, current_id = None, None

    @app.get('/health', dependencies=[Depends(auth)])
    def health():
        import torch
        return dict(device=pick_device(), mps_available=torch.backends.mps.is_available(),
                    free_disk_bytes=shutil.disk_usage(root).free,
                    running_jobs=sum(row['state'] == 'running' for row in app.state.store.rows()))

    @app.get('/configs', dependencies=[Depends(auth)])
    def configs():
        directory = registry or Path(os.getenv('IZGE_TRAINING_CONFIG_ROOT', ROOT / 'config/training/approved'))
        values = []
        for path in sorted(directory.glob('*.yaml')):
            try:
                values.append(approved_config(path.stem, {}, registry).model_dump())
            except (OSError, ValueError, yaml.YAMLError):
                continue
        return values

    @app.post('/jobs', status_code=201, dependencies=[Depends(auth)])
    def submit(body: Submission):
        try:
            config = approved_config(body.config_name, body.overrides, registry)
        except (ValueError, OSError):
            raise HTTPException(422, 'invalid approved config or override') from None
        store = app.state.store
        job_id = uuid.uuid4().hex
        directory = root / job_id
        directory.mkdir(mode=0o700)
        (directory / 'config.yaml').write_text(config.model_dump_json())
        with store.connect() as db:
            db.execute('INSERT INTO jobs (id,state,config,created) VALUES (?,?,?,?)',
                       (job_id, 'queued', config.model_dump_json(), time.time()))
        return public(store.get(job_id))

    def public(row):
        config = json.loads(row['config'])
        directory = root / row['id']
        resumable = row['state'] in ('interrupted', 'cancelled', 'timed_out', 'failed')
        if resumable and config['kind'] == 'rfdetr_detect':
            from .adapters import rfdetr_checkpoint
            checkpoint = rfdetr_checkpoint(directory)
        else:
            checkpoint = directory / 'checkpoints/last.pt'
        return dict(id=row['id'], status=row['state'], config=config,
                    created=row['created'], resume_available=resumable
                    and checkpoint is not None and checkpoint.is_file())

    @app.get('/jobs', dependencies=[Depends(auth)])
    def listing():
        return [public(row) for row in app.state.store.rows()]

    @app.get('/jobs/{job_id}', dependencies=[Depends(auth)])
    def detail(job_id: str):
        return public(app.state.store.get(job_id))

    @app.get('/jobs/{job_id}/logs', dependencies=[Depends(auth)])
    def logs(job_id: str, tail: int = Query(default=100, ge=1, le=10000)):
        path = app.state.store.directory(job_id) / 'train.log'
        if not path.exists():
            return {'lines': []}
        with path.open() as stream:
            return {'lines': list(deque(stream, maxlen=tail))}

    @app.get('/jobs/{job_id}/metrics', dependencies=[Depends(auth)])
    def metrics(job_id: str):
        path = app.state.store.directory(job_id) / 'metrics.jsonl'
        if not path.exists():
            return []
        # A writer may currently be appending the final line.
        lines = path.read_text().splitlines(keepends=True)
        return [json.loads(line) for line in lines if line.endswith('\n')]

    @app.post('/jobs/{job_id}/cancel', dependencies=[Depends(auth)])
    async def cancel(job_id: str):
        nonlocal stop_reason
        store = app.state.store
        row = store.get(job_id)
        if row['state'] == 'queued':
            store.update(job_id, 'cancelled')
            store.terminal_file(job_id, 'cancelled')
        elif job_id == current_id and row['state'] == 'running':
            stop_reason = 'cancelled'
            kill(process)
        return public(store.get(job_id))

    @app.post('/jobs/{job_id}/resume', dependencies=[Depends(auth)])
    async def resume(job_id: str):
        store = app.state.store
        row = store.get(job_id)
        if not public(row)['resume_available']:
            raise HTTPException(409, 'job has no resumable checkpoint')
        with store.connect() as db:
            db.execute("UPDATE jobs SET state='queued',resume=1 WHERE id=?", (job_id,))
        return public(store.get(job_id))

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1', '0.0.0.0'])
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
