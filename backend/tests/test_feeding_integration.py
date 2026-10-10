"""Stall feeding pipeline end to end on a disposable PostgreSQL 18 database (IZGE_TEST_DATABASE=1).

Synthetic video + stub backbone; labels below come from the synthetic ground truth and are
test data only, never presented as real labels."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, text
from alembic.config import Config
from alembic import command
from izge.db import engine, Session
from izge import schema as s
from izge.api import app
from izge.analysis import claim
from izge.worker import process
from izge.storage import object_store
from izge.feeding import pipeline
from izge.feeding.training import train
from izge.video import run_tool
from synthetic import stall_video, truth, StubBackbone

pytestmark = pytest.mark.skipif(os.getenv('IZGE_TEST_DATABASE') != '1', reason='Disposable PostgreSQL database required')
IST = ZoneInfo('Europe/Istanbul')
NAME = 'SENTETIK_20261010_143000.mp4'


@pytest.fixture(scope='module', autouse=True)
def migrated():
    with engine.connect() as db:
        assert 180000 <= int(db.scalar(text('SHOW server_version_num'))) < 190000
    command.upgrade(Config('alembic.ini'), 'head')


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    return stall_video(tmp_path_factory.mktemp('feeding') / NAME)


@pytest.fixture
def env():
    client = TestClient(app)

    def post(path, body, status=(200, 201)):
        r = client.post(path, json=body)
        assert r.status_code in status, r.text
        return r.json()
    farm = post('/api/v1/farms', {'name': 'Yemde ' + uuid.uuid4().hex})['id']
    base = f'/api/v1/farms/{farm}'
    barn = post(base + '/barns', {'name': 'Ahır'})['id']
    camera = post(base + '/cameras', {'name': 'Sentetik', 'barn_id': barn, 'config_key': 'sentetik_test'})['id']
    return client, post, farm, base, barn, camera


def upload(client, base, camera, path, name=None, **form):
    with path.open('rb') as f:
        return client.post(base + '/media', params={'camera_id': camera}, data=form,
                           files={'file': (name or path.name, f, 'video/mp4')})


def work():
    """Run queued jobs of this test only (others are pushed into the future first)."""
    with Session.begin() as db:
        job = claim(db)
    assert job, 'no job claimed'
    process(job)
    return job


def isolate(run_id):
    with Session.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.status == 'queued', s.jobs.c.run_id != uuid.UUID(str(run_id)))
                   .values(available_at=datetime.now(timezone.utc) + timedelta(days=1)))


def test_camera_config_api_and_validation(env):
    client, post, farm, base, barn, camera = env
    configs = {c['key']: c for c in client.get('/api/camera-configs').json()}
    assert configs['synthetic_feeding']['placeholder'] is True and configs['sentetik_test']['stalls'] == 2
    detail = client.get('/api/camera-configs/sentetik_test').json()
    assert detail['stalls'][0]['points'] == [[0, 0], [160, 0], [160, 180], [0, 180]]
    assert client.post(base + '/cameras', json={'name': 'x', 'barn_id': barn, 'config_key': 'yok'}).status_code in (404, 422)
    parsed = client.get(base + f'/cameras/{camera}/filename-time', params={'name': NAME}).json()
    assert parsed['recorded_at'] == '2026-10-10T14:30:00+03:00'
    assert client.get(base + f'/cameras/{camera}/filename-time', params={'name': 'kayit.mp4'}).json()['recorded_at'] is None


def test_upload_rules(env, video, tmp_path):
    client, post, farm, base, barn, camera = env
    r = upload(client, base, camera, video, name='kayit.mkv')
    assert r.status_code == 422 and 'H.264 MP4' in r.json()['detail']
    r = upload(client, base, camera, video, name='kayit.mp4')
    assert r.status_code == 422 and 'dosya adından okunamadı' in r.json()['detail']
    mpeg4 = tmp_path / 'SENTETIK_20261010_150000.mp4'
    run_tool(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=5:duration=2',
              '-c:v', 'mpeg4', str(mpeg4)])
    r = upload(client, base, camera, mpeg4)
    assert r.status_code == 422 and 'mpeg4' in r.json()['detail']
    r = upload(client, base, camera, video, name='kayit.mp4', recorded_at='2026-10-11T08:00:00')
    assert r.status_code == 201, r.text
    maps = client.get(base + '/media').json()
    assert maps[-1]['current_time_version'] == 1
    with Session() as db:
        tm = db.execute(select(s.time_maps).where(s.time_maps.c.media_id == uuid.UUID(r.json()['id']))).mappings().one()
    assert tm['recorded_at'] == datetime(2026, 10, 11, 8, tzinfo=IST) and 'form' in tm['note']


def test_placeholder_config_and_missing_model_are_refused(env, video):
    client, post, farm, base, barn, camera = env
    placeholder = post(base + '/cameras', {'name': 'BA2', 'barn_id': barn, 'config_key': 'synthetic_feeding'})['id']
    media = upload(client, base, placeholder, video, name='synthetic_feeding_20261010_143000.mp4').json()
    r = client.post(base + '/labeling/samples', json={'media_id': media['id']})
    assert r.status_code == 422 and 'placeholder' in r.json()['detail']
    media = upload(client, base, camera, video).json()
    r = client.post(base + '/runs', json={'media_id': media['id'], 'analyzer': 'feeding-model-1'})
    assert r.status_code == 422 and 'Eğitilmiş model yok' in r.json()['detail']
    assert client.get(base + '/feeding/models').json()['has_model'] is False
    r = client.post(base + '/runs', json={'media_id': media['id'], 'analyzer': 'feeding-demo-1'})
    assert r.status_code == 422 and 'Demo' in r.json()['detail']


def test_label_train_analyze_end_to_end(env, video, monkeypatch):
    client, post, farm, base, barn, camera = env
    media = upload(client, base, camera, video)
    assert media.status_code == 201, media.text
    media = media.json()
    run = post(base + '/labeling/samples', {'media_id': media['id']})
    isolate(run['id'])
    work()
    first = client.get(base + '/labeling/next', params={'camera_id': camera}).json()
    assert first['progress']['total'] == 60 and first['progress']['labeled'] == 0
    sample = first['sample']
    assert sample['daynight'] == 'gunduz' and sample['local_hour'] == 14
    assert client.get(base + f"/crops/{sample['id']}/image").headers['content-type'] == 'image/png'
    assert client.get(base + f"/crops/{sample['id']}/image?kind=context").content[:2] == b'\xff\xd8'
    assert 'crop_key' not in str(first)

    # Undo restores the previous state; skip removes the sample from the queue without a label.
    label = post(base + '/labels', {'sample_id': sample['id'], 'label': 'gorunmuyor', 'labeler': 'test'})
    undone = post(base + f"/labels/{label['id']}/undo", {'labeler': 'test'})
    assert undone['sample']['id'] == sample['id']
    after = client.get(base + '/labeling/next', params={'camera_id': camera}).json()['progress']
    assert after['labeled'] == 0 and after['open'] == 60
    skipped = post(base + '/labels', {'sample_id': sample['id'], 'label': 'skipped', 'labeler': 'test'})
    assert client.get(base + '/labeling/next', params={'camera_id': camera}).json()['progress']['open'] == 59
    post(base + f"/labels/{skipped['id']}/undo", {'labeler': 'test'})

    # Stratified queue: label everything from the synthetic ground truth (test data).
    seen = set()
    while True:
        nxt = client.get(base + '/labeling/next', params={'camera_id': camera}).json()
        if not nxt['sample']:
            break
        s_ = nxt['sample']
        assert s_['id'] not in seen
        seen.add(s_['id'])
        post(base + '/labels', {'sample_id': s_['id'], 'label': truth(s_['stall_key'], s_['offset']), 'labeler': 'test'})
    assert len(seen) == 60
    with Session() as db:
        with pytest.raises(Exception):
            with Session.begin() as tx:
                tx.execute(update(s.crop_labels).where(s.crop_labels.c.farm_id == uuid.UUID(farm)).values(label='yemde'))

    with Session.begin() as db:
        record = train(db, uuid.UUID(farm), StubBackbone(), object_store())
    assert record['label_count'] == 60 and record['backbone'] == StubBackbone.id
    assert record['metrics']['validation']['status'] == 'insufficient_days'
    cached = set(object_store().list('embeddings/test-stub-mean-color'))
    assert all(f'embeddings/test-stub-mean-color/{uuid.UUID(x).hex}.npy' in cached for x in seen)

    class Counting(StubBackbone):
        calls = 0
        def embed(self, crops):
            Counting.calls += len(crops)
            return super().embed(crops)
    with Session.begin() as db:
        retrained = train(db, uuid.UUID(farm), Counting(), object_store(), c=.5)
    assert Counting.calls == 0 and retrained['version'] != record['version']   # embeddings come from the cache
    with Session.begin() as db:
        db.execute(s.feeding_models.delete().where(s.feeding_models.c.id == retrained['id']))
    models = client.get(base + '/feeding/models').json()
    assert models['has_model'] and models['models'][0]['version'] == record['version']

    monkeypatch.setattr(pipeline, 'load_backbone', lambda backbone_id: StubBackbone())
    monkeypatch.setattr(pipeline, 'CHUNK_SECONDS', 30)   # exercise resumable Parquet chunks
    run = post(base + '/runs', {'media_id': media['id'], 'analyzer': 'feeding-model-1'})
    assert run['origin'] == 'model' and run['context']['model']['version'] == record['version']
    isolate(run['id'])
    work()
    assert client.get(base + f"/runs/{run['id']}").json()['status'] == 'succeeded'
    assert object_store().exists(pipeline.probs_key(run['id']))
    assert not object_store().list(f"probs/{uuid.UUID(run['id']).hex}/")

    timeline = client.get(base + '/feeding/timeline', params={'camera_id': camera, 'day': '2026-10-10'}).json()
    assert [st['id'] for st in timeline['stalls']] == ['A', 'B']
    assert timeline['runs'][0]['model_version'] == record['version']
    # Truth after Decision 3: B's 5 s gap inside [20, 80) counts as feeding.
    for stall, expected in {'A': 40, 'B': 60}.items():
        got = sum((datetime.fromisoformat(x['end']) - datetime.fromisoformat(x['start'])).total_seconds()
                  for x in timeline['segments'] if x['stall'] == stall and x['state'] == 'yemde')
        assert abs(got - expected) <= 3, (stall, got, expected)
    # B's 5 s blue gap between two red runs is merged into one bout (Decision 3).
    b_bouts = [x for x in timeline['segments'] if x['stall'] == 'B' and x['state'] == 'yemde']
    assert len(b_bouts) == 1

    daily = client.get(base + '/feeding/daily', params={'camera_id': camera}).json()
    rows = {r['stall']: r for r in daily['rows']}
    assert rows['A']['bout_count'] == 1 and rows['B']['bout_count'] == 1
    assert abs(rows['A']['observed_seconds'] - 90) < 1e-6
    assert abs(rows['A']['not_visible_seconds'] - 20) <= 3
    assert rows['A']['mesafe'] == 'yakin' and rows['B']['sira'] == 'sag'
    csv = client.get(base + '/feeding/daily.csv', params={'camera_id': camera})
    lines = csv.text.lstrip('﻿').splitlines()
    assert lines[0].startswith('gun,saat_dilimi,kamera,bolme') and len(lines) == 3
    assert record['version'] in csv.text

    # Re-analysis of the same media replaces it in reports, never double counts.
    monkeypatch.setattr(pipeline, 'CHUNK_SECONDS', 300)
    with Session.begin() as db:
        db.execute(update(s.runs).where(s.runs.c.id == uuid.UUID(run['id'])).values(published_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    again = client.get(base + '/feeding/daily', params={'camera_id': camera}).json()
    assert {r['stall']: r['feeding_seconds'] for r in again['rows']} == {k: v['feeding_seconds'] for k, v in rows.items()}


def test_demo_feeding_is_marked_demo_and_needs_no_model():
    from izge.demo import seed
    farm = seed()
    client = TestClient(app)
    base = f'/api/v1/farms/{farm}'
    with Session() as db:
        run = db.execute(select(s.runs).where(s.runs.c.farm_id == farm, s.runs.c.analyzer == 'feeding-demo-1')).mappings().one()
    if run['status'] != 'succeeded':
        with Session.begin() as db:
            db.execute(update(s.jobs).where(s.jobs.c.run_id == run['id']).values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        isolate(run['id'])
        work()
    camera = run['context']['camera_id']
    day = datetime.fromisoformat(run['context']['recorded_at']).astimezone(IST).date()
    timeline = client.get(base + '/feeding/timeline', params={'camera_id': camera, 'day': str(day)}).json()
    assert timeline['is_demo'] and {r['origin'] for r in timeline['runs']} == {'demo'}
    assert {x['state'] for x in timeline['segments']} >= {'yemde', 'yemde_degil'}
    csv = client.get(base + '/feeding/daily.csv', params={'camera_id': camera}).text
    assert 'DEMO' in csv and 'feeding-demo-1' in csv


def label_all(client, post, base, camera, media_id):
    """Label every crop sample of one recording from the synthetic ground truth (test data)."""
    run = post(base + '/labeling/samples', {'media_id': media_id})
    isolate(run['id'])
    work()
    with Session() as db:
        samples = db.execute(select(s.crop_samples).where(s.crop_samples.c.run_id == uuid.UUID(run['id']))).mappings().all()
    for x in samples:
        post(base + '/labels', {'sample_id': str(x['id']), 'label': truth(x['stall_key'], x['offset']), 'labeler': 'test'})
    return len(samples)


def test_gold_set_exclusion_and_evaluation_report(env, video, monkeypatch, tmp_path):
    from izge.feeding import boris
    from izge.feeding.gold import import_gold, evaluation_report, write_report, gold_exclusions
    from synthetic import boris_tsv
    client, post, farm, base, barn, camera = env
    gold_media = upload(client, base, camera, video).json()                       # 2026-10-10 from filename
    train_media = upload(client, base, camera, video, name='kayit.mp4', recorded_at='2026-10-11T08:00:00').json()
    assert label_all(client, post, base, camera, gold_media['id']) == 60
    assert label_all(client, post, base, camera, train_media['id']) == 60

    with Session.begin() as db:
        result = import_gold(db, uuid.UUID(farm), uuid.UUID(gold_media['id']), boris_tsv(), 'sentetik.tsv',
                             boris.load_mapping(), synthetic=True, note='test')
    assert result['events'] == 8 and result['stalls'] == ['A', 'B']
    with Session.begin() as db, pytest.raises(ValueError, match='zaten'):
        import_gold(db, uuid.UUID(farm), uuid.UUID(gold_media['id']), boris_tsv(), 'again.tsv', boris.load_mapping())
    with pytest.raises(Exception):
        with Session.begin() as db:
            db.execute(update(s.gold_events).where(s.gold_events.c.gold_set_id == result['id']).values(state='yemde'))
    with Session() as db:
        days, media = gold_exclusions(db, uuid.UUID(farm))
    assert days == {'2026-10-10'} and media == {uuid.UUID(gold_media['id'])}

    # Gold days/recordings are excluded by the training code itself, whatever the caller passes.
    with Session.begin() as db:
        record = train(db, uuid.UUID(farm), StubBackbone(), object_store(), excluded_days=set())
    assert record['label_count'] == 60 and record['train_days'] == ['2026-10-11']
    assert record['excluded_days'] == ['2026-10-10'] and record['metrics']['excluded_samples'] == 60

    monkeypatch.setattr(pipeline, 'load_backbone', lambda backbone_id: StubBackbone())
    with Session() as db, pytest.raises(ValueError, match='model analizi yok'):
        evaluation_report(db, uuid.UUID(farm))
    run = post(base + '/runs', {'media_id': gold_media['id'], 'analyzer': 'feeding-model-1'})
    isolate(run['id'])
    work()
    with Session() as db:
        report = evaluation_report(db, uuid.UUID(farm))
    assert report['compared_seconds'] == 180
    assert report['gold_sets'][0]['model_version'] == record['version']
    # Stall A is exact up to boundary blur; B's merged 5 s gap counts as feeding in the prediction.
    assert report['mae_minutes']['near_mid'] < .1 and report['mae_minutes']['by_mesafe']['uzak'] < .2
    assert report['confusion']['yemde']['yemde'] >= 90
    assert report['acceptance']['accepted'] is False and report['acceptance']['synthetic'] is True
    md, js = write_report(report, tmp_path)
    assert md.name == f"eval_{report['created_at'][:10]}.md" and js.exists()
    assert 'SENTETİK ALTIN SET' in md.read_text()
    md2, _ = write_report(report, tmp_path)
    assert md2.name.endswith('-2.md')
