"""Run against a disposable PostgreSQL 18 database with DATABASE_URL set.

Never point these tests at a real farm database. pytest requires IZGE_TEST_DATABASE=1.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, text
from sqlalchemy.exc import DBAPIError
from alembic.config import Config
from alembic import command
from izge.db import engine, Session
from izge import schema as s
from izge.api import app
from izge.analysis import claim, save_batch, LeaseLost
from izge.worker import process
from izge.video import FileVideoSource, LocalStorage, run_tool, RTSPVideoSource

pytestmark = pytest.mark.skipif(os.getenv('IZGE_TEST_DATABASE')!='1',reason='Disposable PostgreSQL database required')

@pytest.fixture(scope='module',autouse=True)
def migrated():
    with engine.connect() as db:
        version = int(db.scalar(text('SHOW server_version_num')))
        assert 180000<=version<190000, 'Integration suite requires PostgreSQL 18; PostgreSQL 14 is not a substitute'
    command.upgrade(Config('alembic.ini'),'head')

@pytest.fixture
def setup():
    with Session.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.status=='queued').values(available_at=datetime.now(timezone.utc)+timedelta(days=1)))
    client=TestClient(app)
    def post(path,body):
        response=client.post(path,json=body)
        assert response.status_code in (200,201),response.text
        return response.json()
    farm=post('/api/v1/farms',{'name':'Test '+uuid.uuid4().hex})['id']
    base=f'/api/v1/farms/{farm}'
    barn=post(base+'/barns',{'name':'Ahır'})['id']
    camera=post(base+'/cameras',{'name':'Kamera','barn_id':barn})['id']
    stalls=[post(base+'/stalls',{'name':str(i),'barn_id':barn})['id'] for i in range(2)]
    animal=post(base+'/animals',{'number':'101'})['id']
    return client,post,farm,base,barn,camera,stalls,animal

@pytest.fixture
def video(tmp_path):
    path=tmp_path/'real.mp4'
    run_tool(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','testsrc2=size=160x120:rate=10:duration=6',
              '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)])
    return path


def upload(client,base,camera,video):
    with video.open('rb') as file:
        response=client.post(base+'/media',params={'camera_id':camera},files={'file':('record.mp4',file,'video/mp4')})
    assert response.status_code==201,response.text
    return response.json()


def test_historical_overlap_and_move(setup):
    client,post,farm,base,barn,camera,stalls,animal=setup
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    post(base+'/occupancies',{'animal_id':animal,'stall_id':stalls[0],'start':start.isoformat()})
    bad=client.post(base+'/occupancies',json={'animal_id':animal,'stall_id':stalls[1],'start':(start+timedelta(days=1)).isoformat(),'end':(start+timedelta(days=2)).isoformat()})
    assert bad.status_code==409
    post(base+'/move',{'animal_id':animal,'stall_id':stalls[1],'at':(start+timedelta(days=3)).isoformat()})
    rows=client.get(base+'/occupancies').json()
    assert len(rows)==2 and sum(r['end'] is None for r in rows)==1
    assert client.delete(base+'/occupancies/'+rows[0]['id']).status_code==405


def test_cross_farm_foreign_keys_and_media(setup,video):
    client,post,farm,base,barn,camera,stalls,animal=setup
    other=post('/api/v1/farms',{'name':'Other'})['id']
    wrong=f'/api/v1/farms/{other}'
    assert client.post(wrong+'/stalls',json={'name':'Wrong','barn_id':barn}).status_code==409
    asset=upload(client,base,camera,video)
    assert client.get(wrong+f"/media/{asset['id']}/file").status_code==404
    # Traversal is never served: 404 via the static mount, 405 via API routing when no build exists.
    traversal=client.get(base+'/media/../../etc/passwd/file')
    assert traversal.status_code in (404,405) and 'root:' not in traversal.text
    assert client.post(base+'/runs',json={'media_id':asset['id'],'analyzer':'demo-1'}).status_code==422


def test_real_video_end_to_end_and_manual_report_independence(setup,video):
    client,post,farm,base,barn,camera,stalls,animal=setup
    now=datetime.now(timezone.utc)-timedelta(days=2)
    post(base+'/occupancies',{'animal_id':animal,'stall_id':stalls[0],'start':(now-timedelta(hours=1)).isoformat()})
    asset=upload(client,base,camera,video)
    assert asset['metadata']['width']==160
    assert asset['current_time_version']==0
    frames=list(FileVideoSource(video).frames())
    assert len(frames)==60 and frames[0].offset==0 and frames[-1].offset<6
    post(base+f"/media/{asset['id']}/time",{'recorded_at':now.isoformat(),'note':'Verified fixture'})
    run=post(base+'/runs',{'media_id':asset['id']})
    duplicate=post(base+'/runs',{'media_id':asset['id']})
    assert run['id']==duplicate['id']
    with Session.begin() as db:
        job=claim(db)
    assert job
    process(job)
    detail=client.get(base+f"/runs/{run['id']}").json()
    assert detail['status']=='succeeded' and detail['observation_count']>0
    day=now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).date()
    before=client.get(base+'/report',params={'day':str(day)}).json()
    annotation=post(base+'/annotations',{'media_id':asset['id'],'animal_id':animal,'start_offset':0,'end_offset':2,'label':'feeding'})
    post(base+'/annotations',{'media_id':asset['id'],'start_offset':0,'end_offset':2,'label':'lying','supersedes_id':annotation['id']})
    assert before==client.get(base+'/report',params={'day':str(day)}).json()
    assert before['animals'][0]['dimensions']['posture']['measured_seconds']==0
    exported=client.get(base+'/export').json()
    assert len(exported['annotations'])==2
    assert all(a['origin']=='manual' and a['time_version']==1 for a in exported['annotations'])
    assert all(o['payload']['origin']=='unavailable' and o['animal_id'] is None for o in exported['observations'])
    assert 'schema_version' in client.get(base+'/export?format=csv').text
    assert 'storage_key' not in client.get(base+'/export').text
    post(base+f"/media/{asset['id']}/time",{'recorded_at':(now-timedelta(days=1)).isoformat(),'note':'Correction'})
    second=post(base+'/runs',{'media_id':asset['id']})
    assert second['id']!=run['id'] and second['context']['time_version']==2
    assert client.get(base+f"/runs/{run['id']}").json()['context']['time_version']==1


def test_lease_handoff_stale_write_and_attempt_limit(setup,video):
    client,post,farm,base,barn,camera,stalls,animal=setup
    asset=upload(client,base,camera,video)
    run=post(base+'/runs',{'media_id':asset['id']})
    with Session.begin() as db:
        # Isolate from pending work in other tests.
        db.execute(update(s.jobs).where(s.jobs.c.run_id!=uuid.UUID(run['id']),s.jobs.c.status=='queued').values(available_at=datetime.now(timezone.utc)+timedelta(days=1)))
        job=claim(db)
    with Session.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(lease_until=datetime.now(timezone.utc)-timedelta(seconds=1)))
    with Session.begin() as db:
        assert claim(db) is None  # backoff, not immediate re-claim
    with Session.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(available_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        replacement=claim(db)
    assert replacement['lease_token']!=job['lease_token']
    with Session.begin() as db:
        with pytest.raises(LeaseLost):
            save_batch(db,job,[],0,.1)
    with Session.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(attempts=3,lease_until=datetime.now(timezone.utc)-timedelta(seconds=1)))
        assert claim(db) is None
        assert db.scalar(select(s.jobs.c.status).where(s.jobs.c.id==job['id']))=='failed'


def test_append_only_history_and_secret_masking(setup,video):
    client,post,farm,base,barn,camera,stalls,animal=setup
    post(base+'/cameras',{'name':'Secret camera','barn_id':barn,'secret_ref':'IZGE_RTSP_CAMERA_1'})
    assert 'secret_ref' not in client.get(base+'/cameras').text
    asset=upload(client,base,camera,video)
    annotation=post(base+'/annotations',{'media_id':asset['id'],'start_offset':0,'end_offset':1,'label':'unknown'})
    with pytest.raises(DBAPIError) as failure:
        with Session.begin() as db:
            db.execute(update(s.annotations).where(s.annotations.c.id==uuid.UUID(annotation['id'])).values(label='rewritten'))
    assert failure.value.orig.sqlstate == 'P0001'
    with pytest.raises(ValueError):
        LocalStorage().path('../../etc/passwd')
    with pytest.raises(ValueError):
        RTSPVideoSource('rtsp://user:password@host')


def test_demo_worker_events_reviews_and_single_notification(setup):
    from izge.demo import seed
    from izge.reporting import daily_report
    farm_id=seed()
    # The demo farm may already exist (other suites); make its two observation demo jobs claimable.
    with Session.begin() as db:
        demo_runs=select(s.runs.c.id).where(s.runs.c.farm_id==farm_id,s.runs.c.analyzer=='demo-1')
        db.execute(update(s.jobs).where(s.jobs.c.status=='queued',s.jobs.c.run_id.in_(demo_runs))
                   .values(available_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        # Hold back the stall feeding demo job (same farm, same created_at) so only demo-1 runs here.
        db.execute(update(s.jobs).where(s.jobs.c.status=='queued',s.jobs.c.farm_id==farm_id,
                   s.jobs.c.run_id.not_in(demo_runs)).values(available_at=datetime.now(timezone.utc)+timedelta(days=1)))
    client=TestClient(app)
    base=f'/api/v1/farms/{farm_id}'
    for _ in range(2):
        with Session.begin() as db:
            job=claim(db)
        if job:
            process(job)
    events=client.get(base+'/events').json()
    assert len(events)==2
    assert all(e['origin']=='demo' and e['sufficient'] for e in events)
    notifications=client.get(base+'/notifications').json()
    assert len(notifications)==2 and len({n['event_id'] for n in notifications})==2
    now=datetime.now(timezone.utc)
    day=now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).date()
    with Session() as db:
        before=daily_report(db,farm_id,day,now=now)
    event=events[0]
    for verdict in ('rejected','confirmed'):
        response=client.post(base+f"/events/{event['id']}/reviews",json={'verdict':verdict,'note':'Test review'})
        assert response.status_code==201
    detail=client.get(base+f"/events/{event['id']}").json()
    assert detail['review_status']=='confirmed' and len(detail['reviews'])==2
    assert all(e['retained'] for e in detail['evidence'])
    assert detail['evidence'][0]['start_offset']>=0 and detail['evidence'][0]['end_offset']<=30
    with Session() as db:
        assert before==daily_report(db,farm_id,day,now=now)
    # Two synchronized cameras must not double the animal's measured duration.
    assert all(a['dimensions']['posture']['measured_seconds']<=30 for a in before['animals'])
    exported=client.get(base+'/export').json()
    assert exported['farm']['is_demo'] and len(exported['reviews'])==2


def test_restart_checkpoint_deduplicates_observations(setup,video):
    client,post,farm,base,barn,camera,stalls,animal=setup
    asset=upload(client,base,camera,video)
    run=post(base+'/runs',{'media_id':asset['id']})
    with Session.begin() as db:
        job=claim(db)
    from izge.domain import observation_dict
    from izge.video import UnavailableAnalyzer
    rows=[observation_dict(o) for frame in FileVideoSource(video).frames() if frame.offset<1
          for o in UnavailableAnalyzer().observe(frame)]
    with Session.begin() as db:
        save_batch(db,job,rows,.9,.1)
    with Session.begin() as db:
        save_batch(db,job,rows,.9,.1)
        count=db.scalar(select(__import__('sqlalchemy').func.count()).select_from(s.observations).where(s.observations.c.run_id==uuid.UUID(run['id'])))
        assert count==len(rows)
    process(dict(job,checkpoint=.9))
    detail=client.get(base+f"/runs/{run['id']}").json()
    assert detail['status']=='succeeded'
    with Session() as db:
        keys=db.execute(select(s.observations.c.sample_key).where(s.observations.c.run_id==uuid.UUID(run['id']))).scalars().all()
        assert len(keys)==len(set(keys))
