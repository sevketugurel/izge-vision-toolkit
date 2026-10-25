"""Append-only local comments and audit; never stores or overrides Swarm tasks."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .admin_swarm import clean


@contextmanager
def connection():
    path = Path(os.getenv("IZGE_ADMIN_OVERLAY", Path.home() / ".izge-admin/comments.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=5)
    db.execute(
        "CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
        "created_at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL)"
    )
    try:
        with db:
            yield db
    finally:
        db.close()


def append(task_id, kind, body):
    record = dict(
        id=str(uuid.uuid4()),
        task_id=task_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        kind=kind,
        body=clean(body),
    )
    with connection() as db:
        db.execute(
            "INSERT INTO entries VALUES (?, ?, ?, ?, ?)",
            (record["id"], task_id, record["created_at"], kind, json.dumps(record["body"])),
        )
    return record


def entries(task_id):
    with connection() as db:
        rows = db.execute(
            "SELECT id, created_at, kind, body FROM entries WHERE task_id=? ORDER BY rowid DESC LIMIT 100",
            (task_id,),
        ).fetchall()
    return [dict(id=r[0], created_at=r[1], kind=r[2], body=json.loads(r[3])) for r in rows]
