from datetime import datetime, timezone
from sqlalchemy import select
from . import schema as s
from .domain import day_bounds, fuse, subtract_spans


def daily_report(db, farm_id, day, animal_id=None, now=None):
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().first()
    if not farm:
        raise LookupError('İşletme bulunamadı')
    start, end = day_bounds(day, farm['timezone'], now or datetime.now(timezone.utc))
    query = select(s.animals).where(s.animals.c.farm_id == farm_id)
    if animal_id:
        query = query.where(s.animals.c.id == animal_id)
    animals = db.execute(query).mappings().all()
    published = db.execute(select(s.runs).where(s.runs.c.farm_id == farm_id, s.runs.c.status == 'succeeded')
                           .order_by(s.runs.c.published_at.desc(), s.runs.c.id)).mappings().all()
    # The latest successful context of a source selects a time/geometry/model version.
    # Within that context, a newer range only replaces the range it actually analyzed.
    chosen, versions, claimed = [], {}, {}
    for run in published:
        key = run['media_id']
        signature = (run['context'], run['analyzer'])
        versions.setdefault(key, signature)
        if versions[key] != signature:
            continue
        available = subtract_spans((run['start_offset'], run['end_offset']), claimed.get(key, []))
        claimed.setdefault(key, []).append((run['start_offset'], run['end_offset']))
        if available:
            chosen.append((run, available))
    result = []
    for animal in animals:
        placements = db.execute(select(s.occupancies).where(s.occupancies.c.farm_id == farm_id,
            s.occupancies.c.animal_id == animal['id'], s.occupancies.c.start < end)).mappings().all()
        scopes = [(max(start, r['start']).timestamp(), min(end, r['end'] or end).timestamp()) for r in placements
                  if (r['end'] or end) > start and min(end, r['end'] or end) > max(start, r['start'])]
        dimensions, sources, timeline = {}, set(), []
        for dim in ('posture', 'activity'):
            spans = []
            for run, available in chosen:
                ctx = run['context']
                if not ctx['recorded_at']:
                    continue
                base = datetime.fromisoformat(ctx['recorded_at']).timestamp()
                rows = list(db.execute(select(s.intervals).where(s.intervals.c.run_id == run['id'],
                    s.intervals.c.animal_id == animal['id'], s.intervals.c.dimension == dim)).mappings())
                if run['origin'] == 'unavailable' and any(p['animal_id'] == str(animal['id']) for p in ctx['occupancies']):
                    # No animal is assigned to unavailable observations. They can explain why
                    # a recorded camera period has no behavior evidence, not add measured time.
                    offsets = db.execute(select(s.observations.c.offset).where(s.observations.c.run_id == run['id'])
                                         .order_by(s.observations.c.offset)).scalars().all()
                    for a, b in zip(offsets, offsets[1:]):
                        if not 0 < b-a <= ctx['config']['max_gap']:
                            continue
                        if rows and rows[-1]['end_offset'] == a:
                            rows[-1]['end_offset'] = b
                        else:
                            rows.append(dict(start_offset=a, end_offset=b, label='unknown', quality=0,
                                             origin='unavailable', reason='model_unavailable'))
                for row in rows:
                    for left, right in available:
                        a = max(start.timestamp(), base+row['start_offset'], base+left)
                        b = min(end.timestamp(), base+row['end_offset'], base+right)
                        if a >= b:
                            continue
                        spans.append(dict(start=a, end=b, label=row['label'], quality=row['quality'],
                            camera_id=ctx['camera_id'], clock_verified=ctx['clock_verified'], reason=row.get('reason')))
                        timeline.append(dict(dimension=dim, label=row['label'], start=a, end=b,
                                             origin=row['origin'], run_id=str(run['id'])))
                        sources.add(run['origin'])
            dimensions[dim] = fuse(spans, scopes)
        result.append(dict(animal_id=str(animal['id']), number=animal['number'], name=animal['name'],
                           dimensions=dimensions, origins=sorted(sources), timeline=timeline))
    return dict(day=str(day), timezone=farm['timezone'], is_demo=farm['is_demo'], animals=result,
        selected_run_ids=[str(r['id']) for r, _ in chosen],
        undated_runs_excluded=sum(1 for r, _ in chosen if not r['context']['recorded_at']),
        policy='Otomatik süreler; manuel etiket ve incelemeler dahil değildir.',
        status='insufficient_data' if not any(a['dimensions']['posture']['measured_seconds'] for a in result) else 'measured')
