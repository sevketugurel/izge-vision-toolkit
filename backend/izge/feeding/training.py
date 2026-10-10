"""Train the stall-state classifier from labeled crops.

Embeddings are cached per backbone in object storage, so retraining never recomputes them.
Validation is always grouped by local recording day (never a random frame split).
"""
import io
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np
from PIL import Image
from sqlalchemy import select
from .. import schema as s
from .backbone import Backbone, slug
from .classifier import fit
from .states import STATES

LABELS = set(STATES)


def latest_labels(db, farm_id) -> dict:
    """Latest label per sample from the append-only history (including 'skipped'). A 'retracted'
    record voids the label it supersedes, so undo restores the previous state."""
    rows = db.execute(select(s.crop_labels).where(s.crop_labels.c.farm_id == farm_id)
                      .order_by(s.crop_labels.c.created_at, s.crop_labels.c.id)).mappings().all()
    voided = {r['supersedes_id'] for r in rows if r['label'] == 'retracted'}
    latest: dict = {}
    for r in rows:
        if r['label'] != 'retracted' and r['id'] not in voided:
            latest[r['sample_id']] = r['label']
    return latest


def effective_labels(db, farm_id, camera_id=None) -> list[tuple[dict, str]]:
    """(sample, label) pairs usable for training; skipped samples carry no label."""
    latest = latest_labels(db, farm_id)
    query = select(s.crop_samples).where(s.crop_samples.c.farm_id == farm_id)
    if camera_id:
        query = query.where(s.crop_samples.c.camera_id == camera_id)
    return [(dict(r), latest[r['id']]) for r in db.execute(query).mappings() if latest.get(r['id']) in LABELS]


def embeddings_for(samples: list[dict], backbone: Backbone, storage) -> np.ndarray:
    folder = f'embeddings/{slug(backbone.id)}'
    result: list[np.ndarray | None] = []
    missing = []
    for i, sample in enumerate(samples):
        key = f'{folder}/{uuid.UUID(str(sample["id"])).hex}.npy'
        if storage.exists(key):
            result.append(np.load(io.BytesIO(storage.get_bytes(key)), allow_pickle=False))
        else:
            result.append(None)
            missing.append((i, key))
    for start in range(0, len(missing), 64):
        batch = missing[start:start + 64]
        images = [np.asarray(Image.open(io.BytesIO(storage.get_bytes(samples[i]['crop_key']))).convert('RGB'))
                  for i, _ in batch]
        for (i, key), vector in zip(batch, backbone.embed(images)):
            buffer = io.BytesIO()
            np.save(buffer, vector.astype(np.float32), allow_pickle=False)
            storage.put_bytes(key, buffer.getvalue())
            result[i] = vector
    return np.stack(result) if result else np.zeros((0, backbone.dim), dtype=np.float32)


def confusion(true: list[int], pred: list[int]) -> list[list[int]]:
    m = [[0] * 3 for _ in range(3)]
    for t, p in zip(true, pred):
        m[t][p] += 1
    return m


def scores(m: list[list[int]]) -> dict:
    total = sum(map(sum, m))
    per_class = {}
    for k, state in enumerate(STATES):
        tp = m[k][k]
        precision = tp / sum(m[r][k] for r in range(3)) if sum(m[r][k] for r in range(3)) else None
        recall = tp / sum(m[k]) if sum(m[k]) else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0. if precision is not None and recall is not None else None)
        per_class[state] = dict(precision=precision, recall=recall, f1=f1, support=sum(m[k]))
    f1s = [v['f1'] for v in per_class.values() if v['f1'] is not None]
    return dict(accuracy=sum(m[k][k] for k in range(3)) / total if total else None,
                macro_f1=sum(f1s) / len(f1s) if f1s else None, per_class=per_class, confusion=m, n=total)


def day_validation(x: np.ndarray, labels: list[str], days: list[str], backbone_id: str, c: float) -> dict:
    """Leave-one-day-out: each fold trains on the other days and tests on one whole day."""
    unique = sorted(set(days))
    if len(unique) < 2:
        return dict(status='insufficient_days', message='Gün bazlı doğrulama için en az 2 farklı gün gerekli',
                    days=unique, folds=[])
    folds, all_true, all_pred = [], [], []
    for day in unique:
        train = [i for i, d in enumerate(days) if d != day]
        test = [i for i, d in enumerate(days) if d == day]
        train_labels = [labels[i] for i in train]
        if set(train_labels) != set(STATES):
            folds.append(dict(day=day, status='skipped', reason='eğitim katında her sınıf yok', n=len(test)))
            continue
        model = fit(x[train], train_labels, backbone_id, c)
        pred = model.predict_proba(x[test]).argmax(axis=1).tolist()
        true = [STATES.index(labels[i]) for i in test]
        all_true += true
        all_pred += pred
        folds.append(dict(day=day, status='ok', **scores(confusion(true, pred))))
    return dict(status='ok' if all_true else 'no_valid_fold', scheme='leave-one-day-out',
                days=unique, folds=folds, overall=scores(confusion(all_true, all_pred)) if all_true else None)


def train(db, farm_id, backbone: Backbone, storage, excluded_days: set[str] | None = None,
          c: float = 1.0, camera_id=None) -> dict:
    farm = db.execute(select(s.farms).where(s.farms.c.id == farm_id)).mappings().one()
    zone = ZoneInfo(farm['timezone'])
    excluded_days = set(excluded_days or ())
    pairs = effective_labels(db, farm_id, camera_id)
    used = [(sample, label) for sample, label in pairs
            if sample['captured_at'].astimezone(zone).date().isoformat() not in excluded_days]
    if not used:
        raise ValueError('Eğitim için etiketli kesit yok')
    samples = [sample for sample, _ in used]
    labels = [label for _, label in used]
    days = [sample['captured_at'].astimezone(zone).date().isoformat() for sample in samples]
    x = embeddings_for(samples, backbone, storage)
    validation = day_validation(x, labels, days, backbone.id, c)
    model = fit(x, labels, backbone.id, c)
    model_id = uuid.uuid4()
    key = f'models/{model_id.hex}.npz'
    storage.put_bytes(key, model.to_bytes())
    now = datetime.now(timezone.utc)
    record = dict(id=model_id, farm_id=farm_id, version=f'feeding-{now:%Y%m%d-%H%M%S}-{model_id.hex[:6]}',
        backbone=backbone.id, classifier='logistic-regression-l2', label_count=len(labels),
        class_counts={state: labels.count(state) for state in STATES},
        metrics=dict(validation=validation, c=c, trained_at=now.isoformat()),
        train_days=sorted(set(days)), excluded_days=sorted(excluded_days), storage_key=key)
    db.execute(s.feeding_models.insert().values(**record))
    return record
