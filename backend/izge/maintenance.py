"""Remove only expired unretained evidence; original media is never collected."""
from datetime import datetime, timezone
from sqlalchemy import select, delete
from .db import Session
from . import schema as s
from .storage import object_store


def cleanup():
    storage = object_store()
    with Session.begin() as db:
        rows = db.execute(select(s.evidence).where(s.evidence.c.expires_at<datetime.now(timezone.utc),
            s.evidence.c.retained.is_(False)).with_for_update(skip_locked=True)).mappings().all()
        for row in rows:
            storage.delete(row['storage_key'])
            db.execute(delete(s.evidence).where(s.evidence.c.id==row['id']))
    return len(rows)

if __name__=='__main__':
    print(f'{cleanup()} süresi dolmuş kanıt temizlendi.')
