"""Read-only quality audit of COCO-style detection annotations (human, pseudo-label or model output).

Reports problems instead of raising so one pass lists every defect. Origins (`manual`, `model`,
`demo`, `unavailable`) are counted, never rewritten. This is a quality report, not the training gate:
`validate_training_dataset` still decides whether human labels may train.
"""
import json
from collections import Counter
from pathlib import Path

import numpy as np

from .tracking import iou_matrix

ORIGINS = ('manual', 'model', 'demo', 'unavailable')


def audit_coco(data: dict, image_root: Path | None = None, tiny_px: float = 8.0,
               duplicate_iou: float = 0.9, max_instances_per_image: int = 60) -> dict:
    problems: list[dict] = []

    def add(code, **detail):
        problems.append({'code': code, **detail})

    images = {}
    for im in data.get('images', []):
        if im['id'] in images:
            add('duplicate_image_id', image_id=im['id'])
        images[im['id']] = im
        if image_root is not None and not (image_root / im['file_name']).is_file():
            add('image_file_missing', image_id=im['id'], file=im['file_name'])
    categories = {c['id']: c['name'] for c in data.get('categories', [])}
    by_image: dict = {}
    origins, per_class = Counter(), Counter()
    areas = []
    for ann in data.get('annotations', []):
        aid, im = ann.get('id'), images.get(ann.get('image_id'))
        origins[ann.get('origin', 'unspecified')] += 1
        per_class[categories.get(ann.get('category_id'), 'unknown')] += 1
        if im is None:
            add('annotation_unknown_image', annotation_id=aid)
            continue
        if ann.get('category_id') not in categories:
            add('annotation_unknown_category', annotation_id=aid)
        if ann.get('origin') not in ORIGINS:
            add('origin_missing_or_invalid', annotation_id=aid)
        x, y, w, h = (float(v) for v in ann['bbox'])
        if not all(np.isfinite([x, y, w, h])) or w <= 0 or h <= 0:
            add('bbox_degenerate', annotation_id=aid)
            continue
        if x < 0 or y < 0 or x + w > im['width'] or y + h > im['height']:
            add('bbox_out_of_image', annotation_id=aid, image_id=im['id'])
        if min(w, h) < tiny_px:
            add('bbox_tiny', annotation_id=aid, image_id=im['id'], size_px=[w, h])
        if ann.get('score') is not None and not 0 <= float(ann['score']) <= 1:
            add('score_out_of_range', annotation_id=aid)
        areas.append(w * h / (im['width'] * im['height']))
        by_image.setdefault(im['id'], []).append((aid, [x, y, x + w, y + h]))
    for image_id, rows in by_image.items():
        if len(rows) > max_instances_per_image:
            add('too_many_instances', image_id=image_id, count=len(rows))
        iou = iou_matrix(np.array([b for _, b in rows]), np.array([b for _, b in rows]))
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if iou[i, j] >= duplicate_iou:
                    add('duplicate_box', image_id=image_id, annotation_ids=[rows[i][0], rows[j][0]])
    groups: dict = {}
    for im in images.values():
        if im.get('group_id') is not None:
            groups.setdefault(im['group_id'], set()).add(im.get('split'))
    for group, splits in groups.items():
        if len(splits - {None}) > 1:
            add('group_leaks_across_splits', group_id=group, splits=sorted(s for s in splits if s))
    empty = sum(1 for i in images if i not in by_image)
    return {'schema_version': 'annotation-audit-1', 'images': len(images),
            'annotations': len(data.get('annotations', [])), 'images_without_boxes': empty,
            'origins': dict(origins), 'per_class': dict(per_class),
            'box_area_fraction': None if not areas else {
                'min': float(np.min(areas)), 'median': float(np.median(areas)), 'max': float(np.max(areas))},
            'problem_counts': dict(Counter(p['code'] for p in problems)),
            'problems': problems[:200], 'problems_truncated': len(problems) > 200, 'ok': not problems}


def audit_file(path: Path, image_root: Path | None = None, **kwargs) -> dict:
    return audit_coco(json.loads(path.read_text()), image_root, **kwargs)
