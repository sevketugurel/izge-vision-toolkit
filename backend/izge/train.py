"""Train the stall-state classifier: `python -m izge.train --farm <farm-uuid>`.

Requires the DINOv3 backbone (HF_TOKEN). Embeddings are cached; reruns only embed new crops.
"""
import argparse
import json
import uuid
from .db import Session
from .storage import object_store
from .feeding.backbone import DINOV3_ID, load_backbone
from .feeding.training import train


def main():
    parser = argparse.ArgumentParser(description='Bölme durum sınıflandırıcısını eğitir')
    parser.add_argument('--farm', required=True, type=uuid.UUID)
    parser.add_argument('--camera', type=uuid.UUID, help='Yalnız bu kameranın etiketleri')
    parser.add_argument('--c', type=float, default=1.0, help='Lojistik regresyon ters düzenlileştirme gücü')
    args = parser.parse_args()
    backbone = load_backbone(DINOV3_ID)
    with Session.begin() as db:
        record = train(db, args.farm, backbone, object_store(), c=args.c, camera_id=args.camera)
    print(json.dumps({k: record[k] for k in ('version', 'backbone', 'label_count', 'class_counts', 'train_days')},
                     ensure_ascii=False, indent=2, default=str))
    print(json.dumps(record['metrics']['validation'].get('overall') or record['metrics']['validation'],
                     ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
