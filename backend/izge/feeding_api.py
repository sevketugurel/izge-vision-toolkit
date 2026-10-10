"""HTTP routes for the stall feeding pipeline. No storage keys or disk paths are returned."""
import csv
import io
import random
import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, false
from sqlalchemy.orm import Session as DBSession
from . import schema as s
from .analysis import get_scoped, create_run
from .db import session
from .feeding import camera_config
from .feeding.states import STATES
from .feeding.training import latest_labels
from .storage import object_store

router = APIRouter()
DB = Depends(session)
BASE = '/api/v1/farms/{farm_id}'
STALL_ANALYZERS = ('feeding-model-1', 'feeding-demo-1')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SampleInput(Input):
    media_id: uuid.UUID


class LabelInput(Input):
    sample_id: uuid.UUID
    label: Literal['yemde', 'yemde_degil', 'gorunmuyor', 'skipped']
    labeler: str = Field(min_length=1, max_length=80)


class UndoInput(Input):
    labeler: str = Field(min_length=1, max_length=80)


def config_summary(key: str, found) -> dict:
    if isinstance(found, str):
        return dict(key=key, valid=False, error=found)
    return dict(key=key, valid=True, name=found.name, barn=found.barn, placeholder=found.placeholder,
                stalls=len(found.stalls), reference_resolution=found.reference_resolution.model_dump())


@router.get('/api/camera-configs')
def camera_configs():
    return [config_summary(k, v) for k, v in sorted(camera_config.load_all().items())]


@router.get('/api/camera-configs/{key}')
def camera_config_detail(key: str):
    config = camera_config.load(key)
    return config.model_dump(mode='json') | dict(stalls=[st.model_dump(mode='json') | {'points': st.points()}
                                                       for st in config.stalls])


@router.get(BASE + '/cameras/{camera_id}/filename-time')
def filename_time(farm_id: uuid.UUID, camera_id: uuid.UUID, name: str, db: DBSession = DB):
    camera = get_scoped(db, s.cameras, farm_id, camera_id)
    if not camera['config_key']:
        return dict(recorded_at=None, supported=False)
    found = camera_config.load(camera['config_key']).recorded_at_from_filename(name)
    return dict(recorded_at=found.isoformat() if found else None, supported=True)


def _sample_public(row, labeled_by=None) -> dict:
    return {k: row[k] for k in ('id', 'media_id', 'camera_id', 'stall_key', 'offset', 'captured_at',
                                'local_hour', 'daynight')} | dict(label=labeled_by)


@router.post(BASE + '/labeling/samples', status_code=201)
def new_samples(farm_id: uuid.UUID, data: SampleInput, db: DBSession = DB):
    run = create_run(db, farm_id, data.media_id, 'crops-1')
    db.commit()
    return run


@router.get(BASE + '/labeling/next')
def next_sample(farm_id: uuid.UUID, camera_id: uuid.UUID | None = None, db: DBSession = DB):
    query = select(s.crop_samples).where(s.crop_samples.c.farm_id == farm_id)
    if camera_id:
        query = query.where(s.crop_samples.c.camera_id == camera_id)
    samples = [dict(r) for r in db.execute(query).mappings()]
    latest = latest_labels(db, farm_id)
    labeled = {sid: label for sid, label in latest.items() if label in STATES}
    strata: dict[tuple, dict] = {}
    for sample in samples:
        key = (sample['stall_key'], sample['local_hour'], sample['daynight'])
        stratum = strata.setdefault(key, dict(labeled=0, open=[]))
        if sample['id'] in labeled:
            stratum['labeled'] += 1
        elif sample['id'] not in latest:
            stratum['open'].append(sample)
    ids = {x['id'] for x in samples}
    labeled = {k: v for k, v in labeled.items() if k in ids}
    progress = dict(total=len(samples), labeled=len(labeled), open=sum(len(v['open']) for v in strata.values()),
                    counts={state: sum(1 for v in labeled.values() if v == state) for state in STATES})
    candidates = [v for v in strata.values() if v['open']]
    if not candidates:
        return dict(sample=None, progress=progress)
    # Stratified: the stall × hour × day/night stratum with the fewest labels goes next.
    fewest = min(v['labeled'] for v in candidates)
    pool = [x for v in candidates if v['labeled'] == fewest for x in v['open']]
    return dict(sample=_sample_public(random.choice(pool)), progress=progress)


@router.post(BASE + '/labels', status_code=201)
def new_label(farm_id: uuid.UUID, data: LabelInput, db: DBSession = DB):
    get_scoped(db, s.crop_samples, farm_id, data.sample_id)
    previous = db.execute(select(s.crop_labels.c.id).where(s.crop_labels.c.sample_id == data.sample_id)
                          .order_by(s.crop_labels.c.created_at.desc(), s.crop_labels.c.id.desc()).limit(1)).scalar()
    row = db.execute(s.crop_labels.insert().values(farm_id=farm_id, sample_id=data.sample_id, label=data.label,
        labeler=data.labeler, supersedes_id=previous, origin='manual').returning(s.crop_labels)).mappings().one()
    db.commit()
    return dict(row)


@router.post(BASE + '/labels/{label_id}/undo', status_code=201)
def undo_label(farm_id: uuid.UUID, label_id: uuid.UUID, data: UndoInput, db: DBSession = DB):
    label = get_scoped(db, s.crop_labels, farm_id, label_id)
    if label['label'] == 'retracted':
        raise ValueError('Geri alma kaydı yeniden geri alınamaz')
    row = db.execute(s.crop_labels.insert().values(farm_id=farm_id, sample_id=label['sample_id'], label='retracted',
        labeler=data.labeler, supersedes_id=label_id, origin='manual').returning(s.crop_labels)).mappings().one()
    sample = get_scoped(db, s.crop_samples, farm_id, label['sample_id'])
    db.commit()
    return dict(retraction=dict(row), sample=_sample_public(sample))


@router.get(BASE + '/crops/{sample_id}/image')
def crop_image(farm_id: uuid.UUID, sample_id: uuid.UUID, kind: Literal['crop', 'context'] = 'crop', db: DBSession = DB):
    sample = get_scoped(db, s.crop_samples, farm_id, sample_id)
    path = object_store().path(sample['crop_key' if kind == 'crop' else 'context_key'])
    if not path.exists():
        raise LookupError('Kesit görüntüsü bulunamadı')
    return FileResponse(path, media_type='image/png' if kind == 'crop' else 'image/jpeg')


def _model_public(row) -> dict:
    return {k: row[k] for k in ('id', 'version', 'backbone', 'classifier', 'label_count', 'class_counts',
                                'metrics', 'train_days', 'excluded_days', 'created_at')}


@router.get(BASE + '/feeding/models')
def feeding_models(farm_id: uuid.UUID, db: DBSession = DB):
    rows = db.execute(select(s.feeding_models).where(s.feeding_models.c.farm_id == farm_id)
                      .order_by(s.feeding_models.c.created_at.desc())).mappings().all()
    return dict(models=[_model_public(r) for r in rows], has_model=bool(rows),
                message=None if rows else 'Eğitilmiş model yok. Kesitleri etiketleyip '
                '`python -m izge.train --farm <işletme>` ile eğitin; model olmadan yemde sonucu üretilmez.')


def _selected_runs(db, farm_id, camera_id):
    """Latest successful stall-feeding run per recording of this camera."""
    rows = db.execute(select(s.runs).where(s.runs.c.farm_id == farm_id, s.runs.c.status == 'succeeded',
        s.runs.c.analyzer.in_(STALL_ANALYZERS)).order_by(s.runs.c.published_at.desc(), s.runs.c.id)).mappings().all()
    chosen = {}
    for run in rows:
        if run['context']['camera_id'] == str(camera_id):
            chosen.setdefault(run['media_id'], run)
    return list(chosen.values())


def _run_public(run) -> dict:
    model = run['context'].get('model')
    return dict(run_id=run['id'], media_id=run['media_id'], origin=run['origin'], analyzer=run['analyzer'],
                model_version=model['version'] if model else None, backbone=model['backbone'] if model else None,
                published_at=run['published_at'])


def _stalls(camera) -> list[dict]:
    if not camera['config_key']:
        return []
    found = camera_config.load_all().get(camera['config_key'])
    if not found or isinstance(found, str):
        return []
    return [dict(id=st.id, sira=st.sira, mesafe=st.mesafe) for st in found.stalls]


@router.get(BASE + '/feeding/timeline')
def feeding_timeline(farm_id: uuid.UUID, camera_id: uuid.UUID, day: date, db: DBSession = DB):
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    camera = get_scoped(db, s.cameras, farm_id, camera_id)
    zone = ZoneInfo(farm['timezone'])
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    runs = _selected_runs(db, farm_id, camera_id)
    segments = []
    used = []
    for run in runs:
        rows = db.execute(select(s.feeding_segments).where(s.feeding_segments.c.run_id == run['id'],
            s.feeding_segments.c.start_at < end, s.feeding_segments.c.end_at > start)
            .order_by(s.feeding_segments.c.start_at)).mappings().all()
        if rows:
            used.append(_run_public(run))
        for r in rows:
            segments.append(dict(stall=r['stall_key'], state=r['state'], start=max(r['start_at'], start),
                                 end=min(r['end_at'], end), confidence=r['confidence'], origin=r['origin']))
    return dict(day=str(day), timezone=farm['timezone'], start=start, end=end, stalls=_stalls(camera),
                segments=segments, runs=used, is_demo=farm['is_demo'])


def _daily_rows(db, farm_id, camera_id, start: date | None, end: date | None) -> list[dict]:
    runs = _selected_runs(db, farm_id, camera_id)
    by_run = {run['id']: run for run in runs}
    query = select(s.feeding_daily).where(s.feeding_daily.c.run_id.in_(list(by_run)) if by_run else false())
    if start:
        query = query.where(s.feeding_daily.c.day >= start)
    if end:
        query = query.where(s.feeding_daily.c.day <= end)
    totals: dict[tuple, dict] = {}
    for r in db.execute(query).mappings():
        t = totals.setdefault((r['day'], r['stall_key']), dict(day=r['day'], stall=r['stall_key'], feeding_seconds=0.,
            not_feeding_seconds=0., not_visible_seconds=0., observed_seconds=0., bout_count=0, weighted=0.,
            origins=set(), model_versions=set()))
        for key in ('feeding_seconds', 'not_feeding_seconds', 'not_visible_seconds', 'observed_seconds', 'bout_count'):
            t[key] += r[key]
        t['weighted'] += (r['mean_confidence'] or 0) * r['observed_seconds']
        t['origins'].add(r['origin'])
        model = by_run[r['run_id']]['context'].get('model')
        t['model_versions'].add(model['version'] if model else by_run[r['run_id']]['analyzer'])
    meta = {st['id']: st for st in _stalls(get_scoped(db, s.cameras, farm_id, camera_id))}
    result = []
    for (day, stall), t in sorted(totals.items()):
        weighted = t.pop('weighted')
        t['mean_confidence'] = weighted / t['observed_seconds'] if t['observed_seconds'] else None
        t['origins'], t['model_versions'] = sorted(t['origins']), sorted(t['model_versions'])
        t['sira'], t['mesafe'] = meta.get(stall, {}).get('sira'), meta.get(stall, {}).get('mesafe')
        result.append(t)
    return result


@router.get(BASE + '/feeding/daily')
def feeding_daily(farm_id: uuid.UUID, camera_id: uuid.UUID, start: date | None = None, end: date | None = None,
                  db: DBSession = DB):
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    return dict(rows=_daily_rows(db, farm_id, camera_id, start, end), timezone=farm['timezone'],
                is_demo=farm['is_demo'], runs=[_run_public(r) for r in _selected_runs(db, farm_id, camera_id)])


@router.get(BASE + '/feeding/daily.csv')
def feeding_daily_csv(farm_id: uuid.UUID, camera_id: uuid.UUID, start: date | None = None, end: date | None = None,
                      db: DBSession = DB):
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    camera = get_scoped(db, s.cameras, farm_id, camera_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['gun', 'saat_dilimi', 'kamera', 'bolme', 'sira', 'mesafe', 'yemde_dk', 'beslenme_periyodu',
                     'yemde_degil_dk', 'gorunmuyor_dk', 'gozlenen_dk', 'ortalama_guven', 'koken', 'model_surumu', 'demo'])
    for r in _daily_rows(db, farm_id, camera_id, start, end):
        writer.writerow([r['day'], farm['timezone'], camera['name'], r['stall'], r['sira'] or '', r['mesafe'] or '',
            f"{r['feeding_seconds'] / 60:.2f}", r['bout_count'], f"{r['not_feeding_seconds'] / 60:.2f}",
            f"{r['not_visible_seconds'] / 60:.2f}", f"{r['observed_seconds'] / 60:.2f}",
            '' if r['mean_confidence'] is None else f"{r['mean_confidence']:.3f}", '|'.join(r['origins']),
            '|'.join(r['model_versions']), 'DEMO' if farm['is_demo'] else ''])
    return Response('﻿' + out.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="izge-yemde-gunluk.csv"'})
