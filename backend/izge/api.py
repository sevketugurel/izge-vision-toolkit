"""HTTP application. No raw filesystem paths or RTSP credentials cross the API boundary."""
import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, field_validator
from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from . import schema as s
from .config import APP_NAME, MAX_UPLOAD_BYTES, PILOT_CONFIG
from .db import session
from .domain import validate_polygon
from .analysis import get_scoped, create_run
from .adapters import ANALYZERS
from .video import LocalStorage, probe, derivative
from .reporting import daily_report

app = FastAPI(title=APP_NAME, version='0.1.0')

class UploadLimit:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        limited = scope['type'] == 'http' and scope.get('method') == 'POST' and scope.get('path', '').endswith('/media')
        if not limited:
            return await self.app(scope, receive, send)
        limit, received = MAX_UPLOAD_BYTES + 1024 * 1024, 0
        headers = dict(scope.get('headers', []))
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse(status_code=400, content={'detail':'Geçersiz içerik uzunluğu'})(scope, receive, send)
        if declared > limit:
            return await JSONResponse(status_code=413, content={'detail':'Dosya boyut sınırı aşıldı'})(scope, receive, send)
        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get('body', b''))
            if received > limit:
                raise HTTPException(413, 'Dosya boyut sınırı aşıldı')
            return message
        await self.app(scope, bounded_receive, send)

app.add_middleware(UploadLimit)

@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(status_code=409,content={'detail':'İlişki, benzersizlik veya tarihsel çakışma kısıtı ihlal edildi'})

@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse(status_code=422,content={'detail':str(exc)})

@app.exception_handler(LookupError)
async def missing(request, exc):
    return JSONResponse(status_code=404,content={'detail':str(exc)})

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
class FarmInput(Input):
    name: str = Field(min_length=1,max_length=120)
    timezone: str = 'Europe/Istanbul'
    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls,v):
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError('Geçersiz saat dilimi')
        return v
class Named(Input):
    name: str = Field(min_length=1,max_length=120)
class CameraInput(Named):
    barn_id: uuid.UUID
    secret_ref: str | None = Field(default=None,pattern=r'^IZGE_RTSP_[A-Z0-9_]+$')
class StallInput(Named):
    barn_id: uuid.UUID
class AnimalInput(Input):
    number: str = Field(min_length=1,max_length=80)
    name: str = Field(default='',max_length=120)
class OccupancyInput(Input):
    animal_id: uuid.UUID
    stall_id: uuid.UUID
    start: AwareDatetime
    end: AwareDatetime | None = None
class MoveInput(Input):
    animal_id: uuid.UUID
    stall_id: uuid.UUID
    at: AwareDatetime
class NeighborInput(Input):
    barn_id: uuid.UUID
    left_id: uuid.UUID
    right_id: uuid.UUID
class ZoneInput(Input):
    camera_id: uuid.UUID
    stall_id: uuid.UUID
    role: Literal['body','head','feeder']
    polygon: list[list[float]]
    width: int = Field(gt=0,le=8192)
    height: int = Field(gt=0,le=8192)
class CameraValidation(Input):
    width: int = Field(gt=0,le=8192)
    height: int = Field(gt=0,le=8192)
class ClockInput(Input):
    verified: bool
class TimeInput(Input):
    recorded_at: AwareDatetime
    note: str = Field(min_length=1,max_length=2000)
class RunInput(Input):
    media_id: uuid.UUID
    analyzer: str = Field(default='unavailable-1', min_length=1, max_length=80)
    start_offset: float = Field(default=0,ge=0)
    end_offset: float | None = Field(default=None,gt=0)
class AnnotationInput(Input):
    media_id: uuid.UUID
    animal_id: uuid.UUID | None = None
    start_offset: float = Field(ge=0)
    end_offset: float = Field(gt=0)
    label: str = Field(min_length=1,max_length=100)
    note: str = Field(default='',max_length=5000)
    supersedes_id: uuid.UUID | None = None
class ReviewInput(Input):
    verdict: Literal['confirmed','rejected','needs_evidence']
    note: str = Field(min_length=1,max_length=5000)
class FeedingWindowInput(Input):
    barn_id: uuid.UUID
    start: AwareDatetime
    end: AwareDatetime
class FeedPresenceInput(Input):
    stall_id: uuid.UUID
    start: AwareDatetime
    end: AwareDatetime
    present: bool | None

DB = Depends(session)
BASE = '/api/v1/farms/{farm_id}'
RESOURCES = {'barns':s.barns,'cameras':s.cameras,'stalls':s.stalls,'animals':s.animals,
    'occupancies':s.occupancies,'neighbors':s.neighbors,'zones':s.zones,'media':s.media,'runs':s.runs,
    'jobs':s.jobs,'events':s.events,'annotations':s.annotations,'reviews':s.reviews,
    'feeding-windows':s.feeding_windows,'feed-presence':s.feed_presence,'notifications':s.notifications,'system-events':s.system_events}


def ensure_farm(db,farm_id):
    farm = db.execute(select(s.farms).where(s.farms.c.id==farm_id)).mappings().first()
    if not farm:
        raise LookupError('İşletme bulunamadı')
    return farm


def public(row):
    d = dict(row)
    for private in ('storage_key','secret_ref','lease_token'):
        d.pop(private,None)
    return d


def add(db,table,farm_id,values):
    ensure_farm(db,farm_id)
    row = db.execute(table.insert().values(farm_id=farm_id,**values).returning(table)).mappings().one()
    db.commit()
    return public(row)

@app.get('/api/config')
def config():
    model_ids = sorted(key for key, spec in ANALYZERS.items() if spec.origin == 'model')
    return dict(app_name=APP_NAME,model_status='Model hazır' if model_ids else 'Model bağlı değil',
                default_analyzer=model_ids[0] if model_ids else 'unavailable-1',
                local_only=True,pilot_config=PILOT_CONFIG,max_upload_bytes=MAX_UPLOAD_BYTES)

@app.get('/api/health')
def health(db: DBSession=DB):
    db.execute(select(func.now()))
    return {'status':'ok'}

@app.get('/api/v1/farms')
def farms(db: DBSession=DB):
    return db.execute(select(s.farms).order_by(s.farms.c.created_at)).mappings().all()

@app.post('/api/v1/farms',status_code=201)
def new_farm(data: FarmInput,db: DBSession=DB):
    row = db.execute(s.farms.insert().values(**data.model_dump(),is_demo=False).returning(s.farms)).mappings().one()
    db.commit()
    return row

@app.patch('/api/v1/farms/{farm_id}')
def rename_farm(farm_id: uuid.UUID, data: Named, db: DBSession=DB):
    ensure_farm(db, farm_id)
    db.execute(update(s.farms).where(s.farms.c.id == farm_id).values(name=data.name))
    db.commit()
    return {'status':'updated'}

@app.delete('/api/v1/farms/{farm_id}', status_code=204)
def remove_empty_farm(farm_id: uuid.UUID, db: DBSession=DB):
    ensure_farm(db, farm_id)
    db.execute(delete(s.farms).where(s.farms.c.id == farm_id))
    db.commit()

@app.get(BASE+'/report')
def report(farm_id: uuid.UUID,day: date,animal_id: uuid.UUID | None=None,db: DBSession=DB):
    return daily_report(db,farm_id,day,animal_id)

@app.post(BASE+'/barns',status_code=201)
def new_barn(farm_id: uuid.UUID,data: Named,db: DBSession=DB):
    return add(db,s.barns,farm_id,data.model_dump())

@app.post(BASE+'/cameras',status_code=201)
def new_camera(farm_id: uuid.UUID,data: CameraInput,db: DBSession=DB):
    return add(db,s.cameras,farm_id,data.model_dump())

@app.post(BASE+'/stalls',status_code=201)
def new_stall(farm_id: uuid.UUID,data: StallInput,db: DBSession=DB):
    return add(db,s.stalls,farm_id,data.model_dump())

@app.post(BASE+'/animals',status_code=201)
def new_animal(farm_id: uuid.UUID,data: AnimalInput,db: DBSession=DB):
    return add(db,s.animals,farm_id,data.model_dump())

@app.post(BASE+'/occupancies',status_code=201)
def new_occupancy(farm_id: uuid.UUID,data: OccupancyInput,db: DBSession=DB):
    return add(db,s.occupancies,farm_id,data.model_dump())

@app.post(BASE+'/move')
def move(farm_id: uuid.UUID,data: MoveInput,db: DBSession=DB):
    get_scoped(db,s.animals,farm_id,data.animal_id,lock=True)
    get_scoped(db,s.stalls,farm_id,data.stall_id,lock=True)
    current = db.execute(select(s.occupancies).where(s.occupancies.c.farm_id==farm_id,
        s.occupancies.c.animal_id==data.animal_id,s.occupancies.c.start<=data.at,
        (s.occupancies.c.end.is_(None) | (s.occupancies.c.end>data.at))).with_for_update()).mappings().first()
    end = None
    if current:
        if current['start']==data.at:
            raise ValueError('Yer değişikliği mevcut başlangıçtan sonra olmalı')
        end = current['end']
        db.execute(update(s.occupancies).where(s.occupancies.c.id==current['id']).values(end=data.at))
    return add(db,s.occupancies,farm_id,dict(animal_id=data.animal_id,stall_id=data.stall_id,start=data.at,end=end))

@app.post(BASE+'/neighbors',status_code=201)
def new_neighbor(farm_id: uuid.UUID,data: NeighborInput,db: DBSession=DB):
    values = data.model_dump()
    values['left_id'],values['right_id'] = sorted([data.left_id,data.right_id])
    return add(db,s.neighbors,farm_id,values)

@app.post(BASE+'/cameras/{camera_id}/moved')
def camera_moved(farm_id: uuid.UUID,camera_id: uuid.UUID,db: DBSession=DB):
    camera = get_scoped(db,s.cameras,farm_id,camera_id,lock=True)
    db.execute(update(s.cameras).where(s.cameras.c.id==camera_id).values(geometry_valid=False,geometry_version=camera['geometry_version']+1))
    db.commit()
    return {'status':'validation_required'}

@app.post(BASE+'/cameras/{camera_id}/validate')
def camera_validate(farm_id: uuid.UUID,camera_id: uuid.UUID,data: CameraValidation,db: DBSession=DB):
    camera = get_scoped(db,s.cameras,farm_id,camera_id,lock=True)
    rows = db.execute(select(s.zones).where(s.zones.c.camera_id==camera_id,s.zones.c.geometry_version==camera['geometry_version'])).mappings().all()
    if not rows or any(r['width']!=data.width or r['height']!=data.height for r in rows):
        raise ValueError('Bu sürüm ve çözünürlük için bölgeleri yeniden çizin')
    db.execute(update(s.cameras).where(s.cameras.c.id==camera_id).values(geometry_valid=True,width=data.width,height=data.height))
    db.commit()
    return {'status':'validated'}

@app.post(BASE+'/cameras/{camera_id}/clock')
def camera_clock(farm_id: uuid.UUID,camera_id: uuid.UUID,data: ClockInput,db: DBSession=DB):
    get_scoped(db,s.cameras,farm_id,camera_id,lock=True)
    db.execute(update(s.cameras).where(s.cameras.c.id==camera_id).values(clock_verified=data.verified))
    db.commit()
    return {'clock_verified':data.verified}

@app.post(BASE+'/zones',status_code=201)
def new_zone(farm_id: uuid.UUID,data: ZoneInput,db: DBSession=DB):
    validate_polygon(data.polygon)
    camera = get_scoped(db,s.cameras,farm_id,data.camera_id,lock=True)
    stall = get_scoped(db,s.stalls,farm_id,data.stall_id)
    if stall['barn_id']!=camera['barn_id']:
        raise ValueError('Bölge ve kamera aynı ahırda olmalı')
    version = camera['geometry_version']
    existing = db.execute(select(s.zones.c.id).where(s.zones.c.camera_id == data.camera_id,
        s.zones.c.geometry_version == version, s.zones.c.stall_id == data.stall_id, s.zones.c.role == data.role)).first()
    if camera['geometry_valid'] or existing:
        version += 1
        # Copy the other zones into a new immutable geometry set.
        rows = db.execute(select(s.zones).where(s.zones.c.camera_id==data.camera_id,s.zones.c.geometry_version==camera['geometry_version'])).mappings().all()
        for row in rows:
            if row['stall_id']==data.stall_id and row['role']==data.role:
                continue
            values = {k:v for k,v in row.items() if k not in ('id','created_at')}
            db.execute(s.zones.insert().values(**(values | {'geometry_version':version})))
    db.execute(update(s.cameras).where(s.cameras.c.id==data.camera_id).values(geometry_version=version,geometry_valid=False))
    return add(db,s.zones,farm_id,data.model_dump() | {'geometry_version':version})

@app.post(BASE+'/feeding-windows',status_code=201)
def new_window(farm_id: uuid.UUID,data: FeedingWindowInput,db: DBSession=DB):
    return add(db,s.feeding_windows,farm_id,data.model_dump())

@app.post(BASE+'/feed-presence',status_code=201)
def new_feed(farm_id: uuid.UUID,data: FeedPresenceInput,db: DBSession=DB):
    return add(db,s.feed_presence,farm_id,data.model_dump() | {'origin':'manual'})

@app.post(BASE+'/media',status_code=201)
def upload(farm_id: uuid.UUID,camera_id: uuid.UUID,file: UploadFile=File(...),db: DBSession=DB):
    if ensure_farm(db, farm_id)['is_demo']:
        raise ValueError('Gerçek dosya için ayrı bir işletme oluşturun; demo işletmesine dosya yüklenemez')
    camera = get_scoped(db,s.cameras,farm_id,camera_id,lock=True)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.mp4','.mov','.mkv','.avi'):
        raise ValueError('MP4, MOV, MKV veya AVI yükleyin')
    storage, key = LocalStorage(), uuid.uuid4().hex+suffix
    path = storage.path(key)
    digest, size, created = hashlib.sha256(), 0, []
    try:
        with path.open('xb') as out:
            created.append(path)
            while chunk := file.file.read(1024*1024):
                size += len(chunk)
                if size>MAX_UPLOAD_BYTES:
                    raise HTTPException(413,'Dosya boyut sınırı aşıldı')
                digest.update(chunk)
                out.write(chunk)
        meta = probe(path)
        if (camera['width'],camera['height'])!=(meta['width'],meta['height']):
            db.execute(update(s.cameras).where(s.cameras.c.id==camera_id).values(width=meta['width'],height=meta['height'],
                geometry_valid=False,geometry_version=camera['geometry_version']+1))
        row = db.execute(s.media.insert().values(farm_id=farm_id,camera_id=camera_id,storage_key=key,
            checksum=digest.hexdigest(),filename=Path(file.filename or 'video').name[:200],metadata=meta,origin='unavailable')
            .returning(s.media)).mappings().one()
        for kind,ext in (('thumbnail','.jpg'),('preview','.mp4')):
            child_key = uuid.uuid4().hex+ext
            child = storage.path(child_key)
            created.append(child)
            derivative(path,child,kind)
            db.execute(s.evidence.insert().values(farm_id=farm_id,media_id=row['id'],kind=kind,storage_key=child_key,
                start_offset=0,end_offset=meta['duration']))
        db.commit()
        return public(row)
    except Exception:
        db.rollback()
        for child in created:
            child.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()

@app.get(BASE+'/media/{media_id}/file')
def media_file(farm_id: uuid.UUID,media_id: uuid.UUID,kind: Literal['original','thumbnail','preview']='preview',db: DBSession=DB):
    asset = get_scoped(db,s.media,farm_id,media_id)
    key = asset['storage_key']
    if kind!='original':
        child = db.execute(select(s.evidence).where(s.evidence.c.farm_id==farm_id,s.evidence.c.media_id==media_id,s.evidence.c.kind==kind)).mappings().first()
        if not child:
            raise LookupError('Önizleme bulunamadı')
        key = child['storage_key']
    path = LocalStorage().path(key)
    if not path.exists():
        raise LookupError('Dosya bulunamadı')
    return FileResponse(path,media_type='image/jpeg' if kind=='thumbnail' else 'video/mp4' if kind=='preview' else None)

@app.get(BASE+'/evidence/{evidence_id}/file')
def evidence_file(farm_id: uuid.UUID,evidence_id: uuid.UUID,db: DBSession=DB):
    row = get_scoped(db,s.evidence,farm_id,evidence_id)
    path = LocalStorage().path(row['storage_key'])
    if not path.exists():
        raise LookupError('Kanıt saklama süresi dolmuş')
    return FileResponse(path,media_type='video/mp4')

@app.post(BASE+'/media/{media_id}/time')
def confirm_time(farm_id: uuid.UUID,media_id: uuid.UUID,data: TimeInput,db: DBSession=DB):
    media = get_scoped(db,s.media,farm_id,media_id,lock=True)
    version = media['current_time_version']+1
    db.execute(s.time_maps.insert().values(farm_id=farm_id,media_id=media_id,version=version,**data.model_dump()))
    db.execute(update(s.media).where(s.media.c.id==media_id).values(current_time_version=version))
    db.commit()
    return {'version':version,'message':'Önceki analizler korunur; yeni saat için yeniden analiz başlatın'}

@app.post(BASE+'/runs',status_code=201)
def new_run(farm_id: uuid.UUID,data: RunInput,db: DBSession=DB):
    run = create_run(db,farm_id,data.media_id,data.analyzer,data.start_offset,data.end_offset)
    db.commit()
    return run

@app.get(BASE+'/runs/{run_id}')
def run_detail(farm_id: uuid.UUID,run_id: uuid.UUID,db: DBSession=DB):
    run = get_scoped(db,s.runs,farm_id,run_id)
    run['job'] = public(db.execute(select(s.jobs).where(s.jobs.c.run_id==run_id)).mappings().one())
    run['observation_count'] = db.scalar(select(func.count()).select_from(s.observations).where(s.observations.c.run_id==run_id))
    return run

@app.get(BASE+'/animals/{animal_id}')
def animal_detail(farm_id: uuid.UUID, animal_id: uuid.UUID, db: DBSession=DB):
    row = get_scoped(db, s.animals, farm_id, animal_id)
    row['occupancies'] = list(db.execute(select(s.occupancies).where(s.occupancies.c.farm_id == farm_id,
        s.occupancies.c.animal_id == animal_id).order_by(s.occupancies.c.start)).mappings())
    row['events'] = list(db.execute(select(s.events).join(s.participants, s.participants.c.event_id == s.events.c.id)
        .where(s.events.c.farm_id == farm_id, s.participants.c.animal_id == animal_id)
        .order_by(s.events.c.created_at).distinct()).mappings())
    return row

@app.get(BASE+'/events/{event_id}')
def event_detail(farm_id: uuid.UUID,event_id: uuid.UUID,db: DBSession=DB):
    row = get_scoped(db,s.events,farm_id,event_id)
    run = get_scoped(db, s.runs, farm_id, row['run_id'])
    row['source'] = public(get_scoped(db, s.media, farm_id, run['media_id'])) | {
        'time_version':run['context']['time_version'], 'recorded_at':run['context']['recorded_at'], 'analyzer':run['analyzer']}
    for name,table in (('participants',s.participants),('evidence',s.evidence),('reviews',s.reviews)):
        row[name] = [public(r) for r in db.execute(select(table).where(table.c.event_id==event_id).order_by(table.c.created_at,table.c.id)).mappings()]
    row['review_status'] = row['reviews'][-1]['verdict'] if row['reviews'] else 'pending'
    return row

@app.post(BASE+'/events/{event_id}/reviews',status_code=201)
def new_review(farm_id: uuid.UUID,event_id: uuid.UUID,data: ReviewInput,db: DBSession=DB):
    get_scoped(db,s.events,farm_id,event_id)
    db.execute(update(s.evidence).where(s.evidence.c.event_id==event_id).values(retained=True))
    return add(db,s.reviews,farm_id,dict(event_id=event_id,origin='manual',**data.model_dump()))

@app.post(BASE+'/annotations',status_code=201)
def new_annotation(farm_id: uuid.UUID,data: AnnotationInput,db: DBSession=DB):
    media = get_scoped(db,s.media,farm_id,data.media_id)
    if data.end_offset>media['metadata']['duration'] or data.end_offset<=data.start_offset:
        raise ValueError('Etiket aralığı video sınırları dışında')
    if data.supersedes_id:
        previous = get_scoped(db,s.annotations,farm_id,data.supersedes_id)
        if previous['media_id']!=data.media_id:
            raise ValueError('Düzeltme aynı medyaya ait olmalı')
    return add(db,s.annotations,farm_id,data.model_dump() | {'time_version':media['current_time_version'],'origin':'manual'})

@app.get(BASE+'/export')
def export(farm_id: uuid.UUID,format: Literal['json','csv']='json',db: DBSession=DB):
    farm = ensure_farm(db,farm_id)
    data = {'schema_version':'1','farm':dict(farm),'exported_at':datetime.now(timezone.utc).isoformat()}
    for name,table in (('media',s.media),('time_maps',s.time_maps),('runs',s.runs),('observations',s.observations),
            ('intervals',s.intervals),('events',s.events),('participants',s.participants),('evidence',s.evidence),
            ('annotations',s.annotations),('reviews',s.reviews)):
        data[name] = [public(r) for r in db.execute(select(table).where(table.c.farm_id==farm_id).order_by(table.c.created_at,table.c.id)).mappings()]
    if format=='json':
        return Response(json.dumps(data,default=str,ensure_ascii=False),media_type='application/json',
                        headers={'Content-Disposition':'attachment; filename="izge-export.json"'})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['schema_version','is_demo','record_type','id','origin','data_json'])
    # JSON payload preserves all version/time/relationship fields without lossy flattening.
    for name,rows in data.items():
        if not isinstance(rows,list):
            continue
        for row in rows:
            writer.writerow(['1',farm['is_demo'],name,str(row['id']),row.get('origin',row.get('payload',{}).get('origin','')),
                             json.dumps(row,default=str,ensure_ascii=False)])
    return Response(out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="izge-export.csv"'})

@app.patch(BASE+'/{resource}/{record_id}')
def rename(farm_id: uuid.UUID,resource: str,record_id: uuid.UUID,data: Named,db: DBSession=DB):
    if resource not in ('barns','cameras','stalls','animals'):
        raise HTTPException(405,'Bu kayıt değiştirilemez')
    table = RESOURCES[resource]
    get_scoped(db,table,farm_id,record_id)
    db.execute(update(table).where(table.c.id==record_id).values(name=data.name))
    db.commit()
    return {'status':'updated'}

@app.delete(BASE+'/{resource}/{record_id}',status_code=204)
def remove(farm_id: uuid.UUID,resource: str,record_id: uuid.UUID,db: DBSession=DB):
    if resource not in ('barns','cameras','stalls','animals','neighbors'):
        raise HTTPException(405,'Geçmiş kayıtları silinemez')
    table = RESOURCES[resource]
    get_scoped(db,table,farm_id,record_id)
    db.execute(delete(table).where(table.c.id==record_id))
    db.commit()

@app.get(BASE+'/{resource}')
def list_resource(farm_id: uuid.UUID,resource: str,db: DBSession=DB):
    ensure_farm(db,farm_id)
    table = RESOURCES.get(resource)
    if table is None:
        raise LookupError('Kaynak bulunamadı')
    rows = [public(r) for r in db.execute(select(table).where(table.c.farm_id==farm_id).order_by(table.c.created_at,table.c.id)).mappings()]
    if resource == 'events':
        latest = {}
        for review in db.execute(select(s.reviews).where(s.reviews.c.farm_id == farm_id)
                                 .order_by(s.reviews.c.created_at.desc(), s.reviews.c.id.desc())).mappings():
            latest.setdefault(review['event_id'], review['verdict'])
        for row in rows:
            row['review_status'] = latest.get(row['id'], 'pending')
    return rows

static = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if static.exists():
    app.mount('/',StaticFiles(directory=static,html=True),name='frontend')
