"""Start the spot GPU worker VM while jobs are queued; stop it after N idle minutes.

Runs on the always-on app VM: `python -m izge.gpu_controller`. Uses the Compute Engine REST API
with the VM service account token from the metadata server (no extra dependencies).
Env: GCP_PROJECT, GCP_ZONE, IZGE_GPU_INSTANCE, IZGE_GPU_IDLE_MINUTES (15), IZGE_GPU_POLL_SECONDS (60).
A preempted spot VM becomes TERMINATED; its job lease expires and the job is retried after restart.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from . import schema as s

log = logging.getLogger('izge.gpu_controller')
STOPPED = {'TERMINATED', 'STOPPED', 'SUSPENDED'}
METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'


def decide(status: str, pending: int, last_work: datetime, now: datetime, idle: timedelta) -> str | None:
    if pending > 0:
        return 'start' if status in STOPPED else None
    if status == 'RUNNING' and now - last_work >= idle:
        return 'stop'
    return None


def pending_jobs(db) -> int:
    now = db.scalar(select(func.now()))
    return db.scalar(select(func.count()).select_from(s.jobs).where(
        ((s.jobs.c.status == 'queued') & (s.jobs.c.available_at <= now + timedelta(minutes=1))) |
        (s.jobs.c.status == 'running')))


class ComputeInstance:
    def __init__(self, project: str, zone: str, name: str):
        self.url = f'https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances/{name}'

    def _token(self) -> str:
        req = urllib.request.Request(METADATA, headers={'Metadata-Flavor': 'Google'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)['access_token']

    def _call(self, url: str, method: str = 'GET') -> dict:
        req = urllib.request.Request(url, method=method, data=b'' if method == 'POST' else None,
                                     headers={'Authorization': f'Bearer {self._token()}'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def status(self) -> str:
        return self._call(self.url)['status']

    def start(self):
        self._call(self.url + '/start', 'POST')

    def stop(self):
        self._call(self.url + '/stop', 'POST')


@dataclass
class Controller:
    instance: ComputeInstance
    idle: timedelta
    last_work: datetime

    def step(self, pending: int, now: datetime) -> str | None:
        if pending:
            self.last_work = now
        status = self.instance.status()
        action = decide(status, pending, self.last_work, now, self.idle)
        if action == 'start':
            self.instance.start()
        elif action == 'stop':
            self.instance.stop()
        if action:
            log.info('GPU worker %s: %s (pending=%s)', status, action, pending)
        return action


def main():
    from .db import Session
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    controller = Controller(ComputeInstance(os.environ['GCP_PROJECT'], os.environ['GCP_ZONE'],
                                            os.environ['IZGE_GPU_INSTANCE']),
                            timedelta(minutes=float(os.getenv('IZGE_GPU_IDLE_MINUTES', '15'))),
                            datetime.now(timezone.utc))
    poll = float(os.getenv('IZGE_GPU_POLL_SECONDS', '60'))
    while True:
        try:
            with Session() as db:
                pending = pending_jobs(db)
            controller.step(pending, datetime.now(timezone.utc))
        except (urllib.error.URLError, OSError, KeyError) as exc:
            # Spot capacity or API errors: log the type only and retry on the next poll.
            log.warning('GPU denetleyici adımı başarısız: %s', type(exc).__name__)
        time.sleep(poll)


if __name__ == '__main__':
    main()
