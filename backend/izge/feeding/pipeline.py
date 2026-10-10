"""Worker side of stall runs. Every DB write is fenced by the job lease (see analysis.fence).

feeding: 1 Hz PTS sampling → stall crops → analyzer → per-second probabilities (Parquet in object
storage, written in resumable chunks) → smoothing → segments + daily summaries (DB, one transaction).
crops: stratified stall × hour × day/night samples for the labeling screen.
"""
import io
import random
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image
from sqlalchemy import update, func
from .. import schema as s
from ..analysis import fence, save_batch, LeaseLost
from ..db import Session
from ..storage import object_store
from ..video import FileVideoSource
from .analyzer import DemoStallAnalyzer, EmbeddingClassifierAnalyzer, StallCrop
from .backbone import load_backbone
from .camera_config import CameraConfig
from .classifier import LinearClassifier
from .crops import geometries, crop, context_image
from .smoothing import SmoothingParams, smooth, daily_summaries
from .states import STATES

CHUNK_SECONDS = 300
BATCH = 64
PROB_SCHEMA = pa.schema([('stall_id', pa.string()), ('second', pa.int32()), ('frame_offset', pa.float64()),
                         ('pts', pa.int64()), *[(f'p_{state}', pa.float32()) for state in STATES]])


def probs_key(run_id) -> str:
    return f'probs/{uuid.UUID(str(run_id)).hex}.parquet'


def _part_key(run_id, start: int) -> str:
    return f'probs/{uuid.UUID(str(run_id)).hex}/part-{start:07d}.parquet'


def build_analyzer(spec, context, storage):
    if spec.origin == 'demo':
        return DemoStallAnalyzer()
    model = context['model']
    classifier = LinearClassifier.from_bytes(storage.get_bytes(model['storage_key']))
    return EmbeddingClassifierAnalyzer(load_backbone(model['backbone']), classifier, model['version'])


def process_stall_run(job, run, media, spec, lost):
    if spec.kind == 'crops':
        return sample_crops(job, run, media, lost)
    return analyze_feeding(job, run, media, spec, lost)


def _frames(path, start, end, lost):
    """First decoded frame of each whole second (PTS based), as (second, frame)."""
    last = None
    for frame in FileVideoSource(path).frames(start):
        if lost.is_set():
            raise LeaseLost()
        if frame.offset >= end:
            break
        second = int(frame.offset + 1e-7)
        if second == last or second < start:
            continue
        last = second
        yield second, frame


def _table(rows) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=PROB_SCHEMA)


def analyze_feeding(job, run, media, spec, lost):
    ctx = run['context']
    config = CameraConfig.model_validate(ctx['camera_config'])
    storage = object_store()
    path = storage.path(media['storage_key'])
    analyzer = build_analyzer(spec, ctx, storage)
    geoms = geometries(config, media['metadata']['width'], media['metadata']['height'])
    start = int(run['start_offset'])
    resume = int(job['checkpoint']) if job['checkpoint'] > start else start
    end = run['end_offset']
    rows, pending, chunk_start = [], [], resume

    def predict():
        probs = analyzer.predict([c for c, _ in pending])
        for (c, frame), p in zip(pending, probs):
            rows.append(dict(stall_id=c.stall_id, second=int(c.offset), frame_offset=frame.offset, pts=frame.pts,
                             **{f'p_{state}': float(p[k]) for k, state in enumerate(STATES)}))
        pending.clear()

    def flush(next_second):
        nonlocal rows, chunk_start
        predict()
        buffer = io.BytesIO()
        pq.write_table(_table(rows), buffer)
        storage.put_bytes(_part_key(run['id'], chunk_start), buffer.getvalue())
        with Session.begin() as db:
            save_batch(db, job, [], float(next_second), min(.95, (next_second - start) / max(1e-9, end - start)))
        rows, chunk_start = [], next_second

    for second, frame in _frames(path, resume, end, lost):
        if second - chunk_start >= CHUNK_SECONDS:
            flush(second)
        image = frame.image.to_ndarray(format='rgb24')
        pending.extend((StallCrop(g.stall_id, second, crop(image, g)), frame) for g in geoms)
        if len(pending) >= BATCH:
            predict()
    flush(int(np.ceil(end)))

    # Parts before the resume point come from earlier attempts; later parts are rewritten above.
    prefix = f'probs/{uuid.UUID(str(run["id"])).hex}/'
    parts = [k for k in storage.list(prefix) if k.endswith('.parquet')]
    table = pa.concat_tables([pq.read_table(io.BytesIO(storage.get_bytes(k))) for k in parts]) if parts else _table([])
    table = table.sort_by([('stall_id', 'ascending'), ('second', 'ascending')])
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    storage.put_bytes(probs_key(run['id']), buffer.getvalue())

    params = SmoothingParams(**config.smoothing.model_dump())
    recorded_at = datetime.fromisoformat(ctx['recorded_at'])
    data = table.to_pydict()
    by_stall: dict[str, tuple[list, list]] = {}
    for i, stall in enumerate(data['stall_id']):
        offsets, probs = by_stall.setdefault(stall, ([], []))
        offsets.append(float(data['second'][i]))
        probs.append(tuple(float(data[f'p_{state}'][i]) for state in STATES))
    results = {stall: smooth(o, p, end, params) for stall, (o, p) in by_stall.items()}

    with Session.begin() as db:
        fence(db, job['id'], job['lease_token'])
        for stall, segments in results.items():
            for seg in segments:
                db.execute(s.feeding_segments.insert().values(farm_id=job['farm_id'], run_id=run['id'],
                    camera_id=ctx['camera_id'], stall_key=stall, state=seg.state, start_offset=seg.start,
                    end_offset=seg.end, start_at=recorded_at + timedelta(seconds=seg.start),
                    end_at=recorded_at + timedelta(seconds=seg.end), confidence=seg.confidence, origin=run['origin']))
            for day in daily_summaries(segments, recorded_at, ctx['timezone']):
                db.execute(s.feeding_daily.insert().values(farm_id=job['farm_id'], run_id=run['id'],
                    camera_id=ctx['camera_id'], stall_key=stall, day=datetime.fromisoformat(day.day).date(),
                    feeding_seconds=day.feeding_seconds, not_feeding_seconds=day.not_feeding_seconds,
                    not_visible_seconds=day.not_visible_seconds, observed_seconds=day.observed_seconds,
                    bout_count=day.bout_count, mean_confidence=day.mean_confidence, origin=run['origin']))
        _publish(db, job, run)
    for key in parts:
        storage.delete(key)


def _publish(db, job, run):
    fence(db, job['id'], job['lease_token'])
    db.execute(update(s.runs).where(s.runs.c.id == run['id']).values(status='succeeded', published_at=func.now()))
    db.execute(update(s.jobs).where(s.jobs.c.id == job['id']).values(status='succeeded', progress=1,
                                                                    lease_token=None, lease_until=None))


def sample_times(config: CameraConfig, recorded_at: datetime, start: float, end: float, seed: str) -> list[int]:
    """`per_stratum` seconds per local clock hour; every stall is cropped at each chosen second,
    so each stall × hour × day/night stratum receives the same number of samples."""
    zone = ZoneInfo(config.timezone)
    hours: dict[datetime, list[int]] = {}
    for second in range(int(np.ceil(start)), int(np.floor(end))):
        local = (recorded_at + timedelta(seconds=second)).astimezone(zone)
        hours.setdefault(local.replace(minute=0, second=0, microsecond=0), []).append(second)
    rng = random.Random(seed)
    chosen = []
    for hour in sorted(hours):
        candidates = hours[hour]
        chosen.extend(rng.sample(candidates, min(config.sampling.per_stratum, len(candidates))))
    return sorted(chosen)


def sample_crops(job, run, media, lost):
    ctx = run['context']
    config = CameraConfig.model_validate(ctx['camera_config'])
    storage = object_store()
    path = storage.path(media['storage_key'])
    recorded_at = datetime.fromisoformat(ctx['recorded_at'])
    zone = ZoneInfo(config.timezone)
    geoms = geometries(config, media['metadata']['width'], media['metadata']['height'])
    times = sample_times(config, recorded_at, run['start_offset'], run['end_offset'], media['checksum'])
    folder = uuid.UUID(str(run['id'])).hex
    samples = []
    for i, target in enumerate(times):
        found = next(_frames(path, target, run['end_offset'], lost), None)
        if found is None:
            continue
        second, frame = found
        image = frame.image.to_ndarray(format='rgb24')
        captured = recorded_at + timedelta(seconds=second)
        local = captured.astimezone(zone)
        for g in geoms:
            sample_id = uuid.uuid4()
            crop_key, context_key = f'crops/{folder}/{sample_id.hex}.png', f'crops/{folder}/{sample_id.hex}-ctx.jpg'
            buffer = io.BytesIO()
            Image.fromarray(crop(image, g)).save(buffer, format='PNG')
            storage.put_bytes(crop_key, buffer.getvalue())
            buffer = io.BytesIO()
            context_image(image, g).save(buffer, format='JPEG', quality=80)
            storage.put_bytes(context_key, buffer.getvalue())
            samples.append(dict(id=sample_id, farm_id=job['farm_id'], media_id=media['id'], camera_id=ctx['camera_id'],
                run_id=run['id'], stall_key=g.stall_id, offset=float(second), captured_at=captured,
                local_hour=local.hour, daynight='gunduz' if config.is_daytime(local) else 'gece',
                crop_key=crop_key, context_key=context_key))
        if i % 5 == 4:
            with Session.begin() as db:
                save_batch(db, job, [], float(second), min(.95, (i + 1) / len(times)))
    with Session.begin() as db:
        fence(db, job['id'], job['lease_token'])
        if samples:
            db.execute(s.crop_samples.insert(), samples)
        _publish(db, job, run)
