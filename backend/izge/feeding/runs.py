"""Run contexts for stall runs. The context snapshots everything a run depends on."""
import json
from sqlalchemy import select
from .. import schema as s
from . import camera_config
from .crops import scale_factor


def latest_model(db, farm_id):
    return db.execute(select(s.feeding_models).where(s.feeding_models.c.farm_id == farm_id)
                      .order_by(s.feeding_models.c.created_at.desc(), s.feeding_models.c.id.desc())).mappings().first()


def stall_context(db, farm, media, camera, analyzer, spec, model_id=None) -> dict:
    if not camera['config_key']:
        raise ValueError('Bu kameraya kesit config\'i bağlı değil')
    config = camera_config.load(camera['config_key'])
    if config.placeholder:
        raise ValueError(f'{config.camera_id}: kesit koordinatları doldurulmadı (placeholder: true). '
                         'Config dosyasını gerçek koordinatlarla güncelleyin.')
    scale_factor(config, media['metadata']['width'], media['metadata']['height'])
    time_map = db.execute(select(s.time_maps).where(s.time_maps.c.media_id == media['id'],
        s.time_maps.c.version == media['current_time_version'])).mappings().first()
    if not time_map:
        raise ValueError('Kayıt başlangıç zamanı yok; yemde süresi tarihsiz hesaplanamaz')
    model = None
    if spec.kind == 'feeding' and spec.origin == 'model':
        if model_id:
            model = db.execute(select(s.feeding_models).where(s.feeding_models.c.farm_id == farm['id'],
                s.feeding_models.c.id == model_id)).mappings().first()
        else:
            model = latest_model(db, farm['id'])
        if not model:
            raise ValueError('Eğitilmiş model yok. Önce kesitleri etiketleyip modeli eğitin; '
                             'model olmadan yemde sonucu üretilmez.')
        model = {k: model[k] for k in ('id', 'version', 'backbone', 'classifier', 'storage_key', 'label_count')}
    return json.loads(json.dumps(dict(
        kind=spec.kind, camera_id=camera['id'], camera_key=config.camera_id,
        camera_config=config.model_dump(mode='json'), config_fingerprint=config.fingerprint(),
        timezone=farm['timezone'], time_version=media['current_time_version'],
        recorded_at=time_map['recorded_at'], model=model), default=str))
