import hashlib
import json
import uuid
from datetime import timedelta
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from . import schema as s
from .adapters import ANALYZERS
from .config import PILOT_CONFIG, LEASE_SECONDS, MAX_ATTEMPTS

class LeaseLost(Exception):
    pass


def get_scoped(db, table, farm_id, ident, lock=False):
    q = select(table).where(table.c.farm_id == farm_id, table.c.id == ident)
    if lock:
        q = q.with_for_update()
    row = db.execute(q).mappings().first()
    if row is None:
        raise LookupError("Kayıt bulunamadı")
    return dict(row)


def create_run(db, farm_id, media_id, analyzer='unavailable-1', start=0., end=None, model_id=None):
    media = get_scoped(db, s.media, farm_id, media_id, lock=True)
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    spec = ANALYZERS.get(analyzer)
    if spec is None:
        raise ValueError("Bilinmeyen analiz türü")
    if spec.demo_only and (not farm['is_demo'] or media['origin'] != 'demo'):
        raise ValueError("Demo analiz yalnızca sentetik demo kaynağında kullanılabilir")
    camera = get_scoped(db,s.cameras,farm_id,media['camera_id'])
    end = media['metadata']['duration'] if end is None else end
    if not 0 <= start < end <= media['metadata']['duration']:
        raise ValueError("Analiz aralığı video sınırları dışında")
    if spec.kind != 'observation':
        from .feeding.runs import stall_context
        context = stall_context(db, farm, media, camera, analyzer, spec, model_id)
        return _enqueue(db, farm_id, media, analyzer, spec, start, end, context)
    time_map = db.execute(select(s.time_maps).where(s.time_maps.c.media_id==media_id,
        s.time_maps.c.version==media['current_time_version'])).mappings().first()
    geometry_valid = camera['geometry_valid'] and (camera['width'], camera['height']) == (media['metadata']['width'], media['metadata']['height'])
    zone_rows = list(db.execute(select(s.zones).where(s.zones.c.camera_id==camera['id'],
        s.zones.c.geometry_version==camera['geometry_version']).order_by(s.zones.c.id)).mappings()) if geometry_valid else []
    # Snapshot placement history: future corrections never rewrite a completed run's identity.
    occupancy = list(db.execute(select(s.occupancies).join(s.stalls,s.stalls.c.id==s.occupancies.c.stall_id).where(s.occupancies.c.farm_id==farm_id,s.stalls.c.barn_id==camera['barn_id']).order_by(s.occupancies.c.start,s.occupancies.c.id)).mappings())
    context = json.loads(json.dumps(dict(config=PILOT_CONFIG, time_version=media['current_time_version'],
        recorded_at=time_map['recorded_at'] if time_map else None, camera_id=camera['id'],
        clock_verified=camera['clock_verified'], barn_id=camera['barn_id'], geometry_version=camera['geometry_version'],
        geometry_valid=geometry_valid, zones=[dict(r) for r in zone_rows],
        occupancies=[dict(r) for r in occupancy]),default=str))
    return _enqueue(db, farm_id, media, analyzer, spec, start, end, context)


def _enqueue(db, farm_id, media, analyzer, spec, start, end, context):
    media_id = media['id']
    canonical = dict(media=str(media_id), checksum=media['checksum'], start=start, end=end, analyzer=analyzer, context=context)
    fingerprint = hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()
    existing = db.execute(select(s.runs).where(s.runs.c.fingerprint==fingerprint)).mappings().first()
    if existing:
        return dict(existing)
    run = dict(id=uuid.uuid4(),farm_id=farm_id,media_id=media_id, fingerprint=fingerprint, analyzer=analyzer,
               origin=spec.origin,context=context,start_offset=start,end_offset=end,status='queued')
    db.execute(s.runs.insert().values(**run))
    db.execute(s.jobs.insert().values(farm_id=farm_id,run_id=run['id']))
    return run


def claim(db):
    now = db.scalar(select(func.clock_timestamp()))
    # Expired final attempts become terminal rather than remaining permanently leased.
    dead = db.execute(update(s.jobs).where(s.jobs.c.status=='running',s.jobs.c.lease_until<=now,
        s.jobs.c.attempts>=MAX_ATTEMPTS).values(status='failed',error='Worker lease süresi doldu',lease_token=None)
        .returning(s.jobs.c.run_id)).scalars().all()
    if dead:
        db.execute(update(s.runs).where(s.runs.c.id.in_(dead)).values(status='failed'))
    # Apply backoff to abandoned leases too.
    expired = list(db.execute(select(s.jobs).where(s.jobs.c.status=='running', s.jobs.c.lease_until<=now,
        s.jobs.c.attempts<MAX_ATTEMPTS).with_for_update(skip_locked=True)).mappings())
    for row in expired:
        db.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(status='queued',lease_token=None,
            available_at=now+timedelta(seconds=5 if row['attempts']==1 else 20),error='Worker yeniden denenecek'))
    row = db.execute(select(s.jobs).where(s.jobs.c.status=='queued', s.jobs.c.available_at<=now,
        s.jobs.c.attempts<MAX_ATTEMPTS).order_by(s.jobs.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
    if not row:
        return None
    token = uuid.uuid4()
    db.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(status='running', attempts=row['attempts']+1,
        lease_token=token,lease_until=now+timedelta(seconds=LEASE_SECONDS),error=None))
    db.execute(update(s.runs).where(s.runs.c.id==row['run_id']).values(status='running'))
    return dict(row) | dict(lease_token=token,attempts=row['attempts']+1)


def fence(db, job_id, token):
    job = db.execute(select(s.jobs).where(s.jobs.c.id==job_id).with_for_update()).mappings().one()
    now = db.scalar(select(func.clock_timestamp()))
    if job['status']!='running' or job['lease_token']!=token or job['lease_until']<=now:
        raise LeaseLost()
    return job


def heartbeat(db, job_id, token):
    fence(db,job_id,token)
    db.execute(update(s.jobs).where(s.jobs.c.id==job_id).values(lease_until=func.clock_timestamp()+timedelta(seconds=LEASE_SECONDS)))


def save_batch(db, job, observations, checkpoint, progress):
    fence(db,job['id'],job['lease_token'])
    for o in observations:
        key = f"{o['pts']}:{o['time_base']}:{o['track_id']}"
        db.execute(insert(s.observations).values(farm_id=job['farm_id'],run_id=job['run_id'],sample_key=key,
            offset=o['offset'],animal_id=o['animal_id'],stall_id=o['stall_id'],payload=o)
            .on_conflict_do_nothing(index_elements=['run_id','sample_key']))
    db.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(checkpoint=checkpoint,progress=progress))


def fail(db, job):
    fence(db,job['id'],job['lease_token'])
    terminal = job['attempts']>=MAX_ATTEMPTS
    db.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(status='failed' if terminal else 'queued',
        error='Analiz tamamlanamadı; video ve sistem olaylarını kontrol edin',lease_token=None,
        available_at=func.clock_timestamp()+timedelta(seconds=5 if job['attempts']==1 else 20)))
    db.execute(update(s.runs).where(s.runs.c.id==job['run_id']).values(status='failed' if terminal else 'queued'))
