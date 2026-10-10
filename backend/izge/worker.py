"""Durable PostgreSQL file worker. Every result transaction is fenced by the lease."""
import argparse
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from sqlalchemy import select, update, func
from . import schema as s
from .db import Session
from .analysis import claim, fence, heartbeat, save_batch, fail, LeaseLost
from .config import HEARTBEAT_SECONDS, EVIDENCE_RETENTION_DAYS
from .domain import Observation, observation_dict, derive_intervals, FeedingAccessRule
from .video import FileVideoSource, derivative
from .storage import object_store
from .adapters import ANALYZERS
from .notifications import DatabaseNotificationSink


def process(job):
    stopped, lost = threading.Event(), threading.Event()
    def pulse():
        while not stopped.wait(HEARTBEAT_SECONDS):
            try:
                with Session.begin() as db:
                    heartbeat(db,job['id'],job['lease_token'])
            except Exception:
                lost.set()
                return
    thread = threading.Thread(target=pulse,daemon=True)
    thread.start()
    try:
        with Session() as db:
            run = dict(db.execute(select(s.runs).where(s.runs.c.id==job['run_id'])).mappings().one())
            media = dict(db.execute(select(s.media).where(s.media.c.id==run['media_id'])).mappings().one())
        context = run['context']
        spec = ANALYZERS[run['analyzer']]
        if spec.kind != 'observation':
            from .feeding.pipeline import process_stall_run
            process_stall_run(job, run, media, spec, lost)
            return
        analyzer = spec.factory(context)
        recorded_at = datetime.fromisoformat(context['recorded_at']) if context['recorded_at'] else None
        storage = object_store()
        batch, last_sample, checkpoint = [], float('-inf'), job['checkpoint']
        for frame in FileVideoSource(storage.local_path(media['storage_key'])).frames(max(run['start_offset'],checkpoint-2)):
            if lost.is_set():
                raise LeaseLost()
            if frame.offset>=run['end_offset']:
                break
            # Sample deterministically on PTS buckets, independent of checkpoint restart position.
            bucket = int((frame.offset+1e-7)*context['config']['interaction_hz'])
            if bucket == last_sample:
                continue
            last_sample = bucket
            if frame.offset<=checkpoint:
                continue
            frame = replace(frame, recorded_at=recorded_at+timedelta(seconds=frame.offset) if recorded_at else None)
            observations = analyzer.observe(frame)
            batch.extend(observation_dict(o) for o in observations)
            if len(batch)>=100:
                with Session.begin() as db:
                    save_batch(db,job,batch,frame.offset,(frame.offset-run['start_offset'])/(run['end_offset']-run['start_offset']))
                batch = []
        if batch:
            with Session.begin() as db:
                save_batch(db,job,batch,batch[-1]['offset'],.95)
        with Session() as db:
            rows = db.execute(select(s.observations.c.payload).where(s.observations.c.run_id==run['id']).order_by(s.observations.c.offset)).scalars().all()
        obs = [Observation(**r) for r in rows]
        derived = derive_intervals(obs,context['config']['max_gap'],context['config']['posture_transition'],context['config']['posture_hz'])
        candidates = FeedingAccessRule().evaluate(obs,context['config'])
        # Generate clips outside the result transaction; only publish links after fencing.
        clips = []
        for event in candidates:
            key = uuid.uuid4().hex+'.mp4'
            start = max(0.,event['start_offset']-context['config']['evidence_padding'])
            end = min(media['metadata']['duration'],event['end_offset']+context['config']['evidence_padding'])
            derivative(storage.local_path(media['storage_key']),storage.path(key),'clip',start,end)
            storage.commit(key)
            clips.append((key,start,end))
        with Session.begin() as db:
            fence(db,job['id'],job['lease_token'])
            for interval in derived:
                db.execute(s.intervals.insert().values(farm_id=job['farm_id'],run_id=run['id'],**interval))
            for event,clip in zip(candidates,clips):
                victim,actor = event.pop('victim'),event.pop('actor')
                event_id = uuid.uuid4()
                db.execute(s.events.insert().values(id=event_id,farm_id=job['farm_id'],run_id=run['id'],**event))
                for animal,role in ((victim,'affected'),(actor,'intervening')):
                    if animal:
                        db.execute(s.participants.insert().values(farm_id=job['farm_id'],event_id=event_id,animal_id=animal,role=role))
                key,start,end = clip
                db.execute(s.evidence.insert().values(farm_id=job['farm_id'],media_id=media['id'],event_id=event_id,
                    kind='clip',storage_key=key,start_offset=start,end_offset=end,retained=not event['sufficient'],
                    expires_at=func.now()+timedelta(days=EVIDENCE_RETENTION_DAYS)))
                if event['sufficient'] and event['details']['notify']:
                    # In-app notification only; file replay is never a live alert timing claim.
                    DatabaseNotificationSink(db, job['farm_id']).notify(str(event_id),
                        'SENTETİK DEMO — muhtemel yem erişimi engellenmesi' if run['origin']=='demo' else 'Muhtemel yem erişimi engellenmesi')
            fence(db,job['id'],job['lease_token'])
            db.execute(update(s.runs).where(s.runs.c.id==run['id']).values(status='succeeded',published_at=func.now()))
            db.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(status='succeeded',progress=1,lease_token=None,lease_until=None))
    finally:
        stopped.set()
        thread.join(timeout=2)


def once():
    with Session.begin() as db:
        job = claim(db)
    if not job:
        return False
    try:
        process(job)
    except LeaseLost:
        pass
    except Exception as exc:
        with Session.begin() as db:
            try:
                fail(db,job)
                db.execute(s.system_events.insert().values(farm_id=job['farm_id'],kind='analysis_failed',
                    details={'run_id':str(job['run_id']),'error_type':type(exc).__name__}))
            except LeaseLost:
                pass
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    if args.once:
        once()
    else:
        from .worker_health import PATH
        def service_pulse():
            while True:
                try:
                    with Session() as db:
                        db.execute(select(func.now()))
                    PATH.touch()
                except Exception:
                    PATH.unlink(missing_ok=True)
                time.sleep(10)
        threading.Thread(target=service_pulse, daemon=True).start()
        while True:
            if not once():
                time.sleep(1)

if __name__ == '__main__':
    main()
