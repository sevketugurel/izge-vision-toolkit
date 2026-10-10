"""Explicit demo seed. Never injects synthetic results into a real farm."""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .db import Session
from . import schema as s
from .video import LocalStorage, run_tool, probe, derivative
from .analysis import create_run
import hashlib


def seed():
    storage = LocalStorage()
    with Session.begin() as db:
        # Serialize seed calls; this is a local administrative command.
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(7340291)"))
        existing = db.execute(select(s.farms).where(s.farms.c.is_demo.is_(True))).mappings().first()
        if existing:
            print(f"Demo hazır: {existing['id']}")
            return existing['id']
        farm_id, barn_id = uuid.uuid4(),uuid.uuid4()
        db.execute(s.farms.insert().values(id=farm_id,name='Örnek Çiftlik — SENTETİK DEMO',timezone='Europe/Istanbul',is_demo=True))
        db.execute(s.barns.insert().values(id=barn_id,farm_id=farm_id,name='Kuzey Ahırı'))
        recorded_at = datetime.now(timezone.utc)-timedelta(minutes=2)
        stalls = []
        for i in range(3):
            animal, stall = uuid.uuid4(),uuid.uuid4()
            stalls.append(stall)
            db.execute(s.animals.insert().values(id=animal,farm_id=farm_id,number=str(101+i),name=['Ada','Duru','Ekin'][i]))
            db.execute(s.stalls.insert().values(id=stall,farm_id=farm_id,barn_id=barn_id,name=f'A-{i+1:02}'))
            db.execute(s.occupancies.insert().values(farm_id=farm_id,animal_id=animal,stall_id=stall,start=recorded_at-timedelta(hours=8)))
            db.execute(s.feed_presence.insert().values(farm_id=farm_id,stall_id=stall,start=recorded_at,end=recorded_at+timedelta(hours=1),present=True,origin='demo'))
        for left,right in zip(stalls,stalls[1:]):
            left,right = sorted([left,right])
            db.execute(s.neighbors.insert().values(farm_id=farm_id,barn_id=barn_id,left_id=left,right_id=right))
        for i in range(2):
            camera_id, media_id = uuid.uuid4(),uuid.uuid4()
            db.execute(s.cameras.insert().values(id=camera_id,farm_id=farm_id,barn_id=barn_id,name=f'Demo Kamera {i+1}',
                width=640,height=360,geometry_version=1,geometry_valid=True,clock_verified=True))
            for j,stall in enumerate(stalls):
                for role,y in (('body',.15),('head',.55),('feeder',.75)):
                    x = .02+j*.32
                    db.execute(s.zones.insert().values(farm_id=farm_id,camera_id=camera_id,stall_id=stall,role=role,
                        polygon=[[x,y],[x+.29,y],[x+.29,y+.15],[x,y+.15]],width=640,height=360,geometry_version=1))
            key = uuid.uuid4().hex+'.mp4'
            path = storage.path(key)
            run_tool(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i',
                'testsrc2=size=640x360:rate=10:duration=30','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(path)])
            db.execute(s.media.insert().values(id=media_id,farm_id=farm_id,camera_id=camera_id,storage_key=key,
                checksum=hashlib.sha256(path.read_bytes()).hexdigest(),filename=f'SENTETIK-DEMO-{i+1}.mp4',
                metadata=probe(path),origin='demo',current_time_version=1))
            db.execute(s.time_maps.insert().values(farm_id=farm_id,media_id=media_id,version=1,recorded_at=recorded_at,note='Sentetik demo zaman eşlemesi'))
            for kind,ext in (('thumbnail','.jpg'),('preview','.mp4')):
                child = uuid.uuid4().hex+ext
                derivative(path,storage.path(child),kind)
                db.execute(s.evidence.insert().values(farm_id=farm_id,media_id=media_id,kind=kind,storage_key=child,start_offset=0,end_offset=30))
            create_run(db,farm_id,media_id,'demo-1')
        print(f'Demo oluşturuldu: {farm_id}. İşleri worker işleyecek.')
        return farm_id

if __name__=='__main__':
    seed()
