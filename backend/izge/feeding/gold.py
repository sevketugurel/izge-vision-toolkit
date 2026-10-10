"""Gold sets in the database: BORIS import, training exclusion and evaluation inputs."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .. import schema as s
from ..analysis import get_scoped
from . import boris, camera_config
from .camera_config import CameraConfig
from .evaluation import evaluate, markdown

STALL_ANALYZERS = ('feeding-model-1', 'feeding-demo-1')


def _recorded_at(db, media) -> datetime:
    tm = db.execute(select(s.time_maps).where(s.time_maps.c.media_id == media['id'],
        s.time_maps.c.version == media['current_time_version'])).mappings().first()
    if not tm:
        raise ValueError('Altın setin kaydı için kayıt başlangıç zamanı yok')
    return tm['recorded_at']


def import_gold(db, farm_id, media_id, text: str, source_name: str, mapping: boris.BorisMapping,
                synthetic: bool = False, note: str = '') -> dict:
    media = get_scoped(db, s.media, farm_id, media_id)
    camera = get_scoped(db, s.cameras, farm_id, media['camera_id'])
    if not camera['config_key']:
        raise ValueError('Altın set yalnız kesit config\'i bağlı kameranın kaydına eklenebilir')
    config = camera_config.load(camera['config_key'])
    _recorded_at(db, media)
    events = boris.parse(text, mapping, {st.id for st in config.stalls}, media['metadata']['duration'])
    checksum = hashlib.sha256(text.encode()).hexdigest()
    if db.execute(select(s.gold_sets.c.id).where(s.gold_sets.c.media_id == media_id,
                                                 s.gold_sets.c.checksum == checksum)).first():
        raise ValueError('Bu BORIS dosyası bu kayıt için zaten içe aktarıldı')
    gold_id = uuid.uuid4()
    db.execute(s.gold_sets.insert().values(id=gold_id, farm_id=farm_id, media_id=media_id, camera_id=camera['id'],
        source_name=source_name[:200], checksum=checksum, mapping=mapping.model_dump(mode='json'),
        synthetic=synthetic, note=note))
    db.execute(s.gold_events.insert(), [dict(farm_id=farm_id, gold_set_id=gold_id, stall_key=e.stall, state=e.state,
                                             start_offset=e.start, end_offset=e.end) for e in events])
    return dict(id=gold_id, events=len(events), stalls=sorted({e.stall for e in events}))


def gold_exclusions(db, farm_id) -> tuple[set[str], set]:
    """Local days touched by any gold set, and the gold recordings themselves (Decision 7)."""
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    zone = ZoneInfo(farm['timezone'])
    days, media_ids = set(), set()
    for gs in db.execute(select(s.gold_sets).where(s.gold_sets.c.farm_id == farm_id)).mappings():
        media = get_scoped(db, s.media, farm_id, gs['media_id'])
        media_ids.add(gs['media_id'])
        base = _recorded_at(db, media)
        for start, end in db.execute(select(s.gold_events.c.start_offset, s.gold_events.c.end_offset)
                                     .where(s.gold_events.c.gold_set_id == gs['id'])):
            day = (base + timedelta(seconds=start)).astimezone(zone).date()
            last = (base + timedelta(seconds=end)).astimezone(zone).date()
            while day <= last:
                days.add(day.isoformat())
                day += timedelta(days=1)
    return days, media_ids


def _run_for(db, gold, run_id=None, allow_demo=False):
    query = select(s.runs).where(s.runs.c.media_id == gold['media_id'], s.runs.c.status == 'succeeded',
                                 s.runs.c.analyzer.in_(STALL_ANALYZERS))
    if run_id:
        query = query.where(s.runs.c.id == run_id)
    if not allow_demo:
        query = query.where(s.runs.c.origin == 'model')
    run = db.execute(query.order_by(s.runs.c.published_at.desc())).mappings().first()
    if not run:
        raise ValueError(f"Altın set {gold['id']}: kaydı için tamamlanmış model analizi yok")
    return run


def evaluation_report(db, farm_id, gold_ids=None, threshold_minutes=3.0, allow_demo=False) -> dict:
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    query = select(s.gold_sets).where(s.gold_sets.c.farm_id == farm_id)
    if gold_ids:
        query = query.where(s.gold_sets.c.id.in_(gold_ids))
    golds = db.execute(query.order_by(s.gold_sets.c.created_at)).mappings().all()
    if not golds:
        raise ValueError('Değerlendirilecek altın set yok')
    gold_iv, pred_iv, mesafe, sources, configs = [], [], {}, [], []
    for gold in golds:
        run = _run_for(db, gold, allow_demo=allow_demo)
        ctx = run['context']
        config = CameraConfig.model_validate(ctx['camera_config'])
        configs.append(config)
        base = datetime.fromisoformat(ctx['recorded_at']).timestamp()
        prefix = ctx['camera_key'] + '/'
        mesafe.update({prefix + st.id: st.mesafe for st in config.stalls})
        for e in db.execute(select(s.gold_events).where(s.gold_events.c.gold_set_id == gold['id'])).mappings():
            gold_iv.append((prefix + e['stall_key'], e['state'], base + e['start_offset'], base + e['end_offset']))
        for seg in db.execute(select(s.feeding_segments).where(s.feeding_segments.c.run_id == run['id'])).mappings():
            pred_iv.append((prefix + seg['stall_key'], seg['state'], seg['start_at'].timestamp(),
                            seg['end_at'].timestamp(), seg['confidence']))
        media = get_scoped(db, s.media, farm_id, gold['media_id'])
        model = ctx.get('model') or {}
        sources.append(dict(id=str(gold['id']), source_name=gold['source_name'], synthetic=gold['synthetic'],
            media_filename=media['filename'], run_id=str(run['id']), origin=run['origin'],
            model_version=model.get('version'), backbone=model.get('backbone'),
            time_version_match=ctx['time_version'] == media['current_time_version']))
    synthetic = any(g['synthetic'] for g in golds) or any(x['origin'] != 'model' for x in sources)
    report = evaluate(gold_iv, pred_iv, farm['timezone'], mesafe, configs[0].is_daytime, threshold_minutes, synthetic)
    report.update(created_at=datetime.now(timezone.utc).isoformat(), farm_id=str(farm_id), gold_sets=sources,
                  timezone=farm['timezone'])
    return report


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{report['created_at'][:10]}"
    n, name = 1, stem
    while (out_dir / f'{name}.md').exists() or (out_dir / f'{name}.json').exists():
        n += 1
        name = f'{stem}-{n}'
    md, js = out_dir / f'{name}.md', out_dir / f'{name}.json'
    md.write_text(markdown(report), encoding='utf-8')
    js.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return md, js
