"""Human annotation gates and source/day grouping for RF-DETR COCO exports."""
import json
import math
import shutil
from datetime import date, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .artifact_io import checksum, prepare_output, write_json

SPLITS = {'train', 'valid', 'test'}


def leakage_components(sources: list[dict]) -> dict[str, str]:
    parent = {r['id']: r['id'] for r in sources}
    if len(parent) != len(sources):
        raise ValueError('Duplicate source ID')

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    keys = {}
    for source in sources:
        tags = [('family', source['family_id']), ('session', source['session_id']),
                ('checksum', source['sha256'])]
        if source.get('recording_day_verified') and source.get('recording_day'):
            days = source.get('recording_days') or [source['recording_day']]
            if source['recording_day'] not in days:
                raise ValueError('Verified start day must belong to all touched recording days')
            for day in days:
                date.fromisoformat(day)
                tags.append(('day_across_cameras', day))
        else:
            tags.append(('unknown_day', 'all_unverified_sources'))
        tags += [('overlap', group) for group in source.get('overlap_groups', [])]
        for tag in tags:
            if tag in keys:
                a, b = find(source['id']), find(keys[tag])
                parent[max(a, b)] = min(a, b)
            keys[tag] = source['id']
    return {key: find(key) for key in parent}


def validate_splits(sources: list[dict], assignments: dict[str, str]) -> dict:
    if set(assignments) != {r['id'] for r in sources}:
        raise ValueError('Every source, including derivatives, requires one split assignment')
    components = leakage_components(sources)
    seen = {}
    for source in sources:
        split = assignments[source['id']]
        if split not in SPLITS:
            raise ValueError('Split unassigned or invalid')
        if not source.get('recording_day_verified') or not source.get('recording_day'):
            raise ValueError('Recording day requires explicit human verification; filename is insufficient')
        verification = source.get('recording_day_verification') or {}
        if not verification.get('verified_by') or not verification.get('evidence'):
            raise ValueError('Recording day verification needs a real person and source evidence')
        verified_at = datetime.fromisoformat(verification.get('verified_at') or '')
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError('Recording day verification time needs timezone')
        if source.get('reserved_test_candidate') and split != 'test':
            raise ValueError('Locked camera generalisation test candidate cannot enter train/valid')
        component = components[source['id']]
        if component in seen and seen[component] != split:
            raise ValueError('Source/session/day/derivative overlap leaks across splits')
        seen[component] = split
    if set(assignments.values()) != SPLITS:
        raise ValueError('Independent train/valid/test groups are incomplete')
    return components


def safe_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError('Dataset file missing or outside package')
    return candidate


def human_record(row: dict):
    if row.get('status') != 'reviewed' or row.get('origin') != 'manual' or not str(row.get('annotator') or '').strip():
        raise ValueError('Human-reviewed manual annotation required; proposals/unlabelled frames are not labels')
    at = datetime.fromisoformat(row.get('annotated_at') or '')
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Timezone-aware annotation time required')


def validate_object(obj: dict, width: int, height: int, segmentation: bool, root: Path) -> dict:
    if obj.get('class_name') != 'cow':
        raise ValueError('Only human cow labels are supported by this export')
    box = obj.get('bbox_xyxy', [])
    if len(box) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box):
        raise ValueError('Finite xyxy bbox required')
    x0, y0, x1, y1 = box
    if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
        raise ValueError('BBox outside original image or empty')
    for key, choices in {'posture': {'standing', 'lying', 'unknown'},
                         'body_visibility': {'visible', 'partial', 'not_visible', 'uncertain'},
                         'head_visibility': {'visible', 'partial', 'not_visible', 'uncertain'}}.items():
        if obj.get(key) is not None and obj[key] not in choices:
            raise ValueError(f'Invalid {key}')
    result = {'bbox': [x0, y0, x1-x0, y1-y0], 'area': (x1-x0)*(y1-y0)}
    if obj.get('mask_file'):
        with Image.open(safe_file(root, obj['mask_file'])) as image:
            mask = np.asarray(image)
        if mask.shape != (height, width) or not set(np.unique(mask)).issubset({0, 255}) or not mask.any():
            raise ValueError('Instance mask must be nonempty binary 0/255 at original image size')
        ys, xs = np.nonzero(mask)
        if xs.min() < x0 or xs.max()+1 > x1 or ys.min() < y0 or ys.max()+1 > y1:
            raise ValueError('Mask extends beyond its human instance bbox')
        flat = (mask > 0).ravel(order='F')
        counts, last, run = [], False, 0
        for pixel in flat:
            if pixel == last:
                run += 1
            else:
                counts.append(run)
                last, run = bool(pixel), 1
        counts.append(run)
        result.update(segmentation={'size': [height, width], 'counts': counts}, area=int(flat.sum()))
    elif obj.get('segmentation'):
        polygons = obj['segmentation']
        area = 0.
        for polygon in polygons:
            if len(polygon) < 6 or len(polygon) % 2 or not all(math.isfinite(x) for x in polygon):
                raise ValueError('Invalid COCO polygon')
            if any(not 0 <= x <= width for x in polygon[::2]) or any(not 0 <= y <= height for y in polygon[1::2]):
                raise ValueError('Polygon outside original image')
            if any(not x0 <= x <= x1 for x in polygon[::2]) or any(not y0 <= y <= y1 for y in polygon[1::2]):
                raise ValueError('Instance polygon extends beyond human bbox')
            points = list(zip(polygon[::2],polygon[1::2]))
            polygon_area = abs(sum(x*y2-x2*y for (x,y),(x2,y2) in zip(points,points[1:]+points[:1])))/2
            if polygon_area <= 0:
                raise ValueError('Empty or degenerate segmentation polygon')
            area += polygon_area
        result['segmentation'] = polygons
        result['area'] = area
    elif segmentation:
        raise ValueError('Segmentation training requires every reviewed instance mask; bbox-only is not ready')
    return result


def export_coco(package: Path, annotations_path: Path, split_path: Path, output: Path,
                segmentation: bool = True, image_selection: list[str] | None = None) -> dict:
    manifest = json.loads((package / 'manifest.json').read_text())
    labels = json.loads(annotations_path.read_text())
    split_data = json.loads(split_path.read_text())
    sources = split_data['sources']
    original_sources = {r['id']: r for r in manifest['sources']}
    if {r['id'] for r in sources} != set(original_sources):
        raise ValueError('Split source set differs from immutable pilot')
    for source in sources:
        immutable = ('sha256', 'family_id', 'session_id', 'path', 'camera_id',
                     'camera_position_revision', 'reserved_test_candidate')
        if any(source.get(k) != original_sources[source['id']].get(k) for k in immutable):
            raise ValueError('Source grouping/provenance was changed')
    assignments = split_data['assignments']
    components = validate_splits(sources, assignments)
    by_id = {r['image_id']: r for r in labels['frames']}
    if len(by_id) != len(labels['frames']) or set(by_id) != {im['id'] for im in manifest['images']}:
        raise ValueError('Human annotation image set missing, duplicated or unexpected')
    selected = set(image_selection) if image_selection is not None else set(by_id)
    if not selected or not selected.issubset(by_id) or (image_selection is not None and len(selected) != len(image_selection)):
        raise ValueError('Explicit image selection is empty, duplicated or references unknown frames')
    datasets = {split: {'images': [], 'annotations': [], 'categories': [{'id': 1, 'name': 'cow', 'supercategory': 'animal'}],
                       'info': {'origin': 'manual', 'training_task': 'segmentation' if segmentation else 'detection'}}
                for split in SPLITS}
    for i, im in enumerate(manifest['images'], 1):
        if im['id'] not in selected:
            continue
        row = by_id[im['id']]
        human_record(row)
        image_file = safe_file(package, im['file'])
        if checksum(image_file) != im['sha256']:
            raise ValueError('Image checksum changed')
        with Image.open(image_file) as decoded:
            if decoded.size != (im['width'], im['height']):
                raise ValueError('Original image dimensions changed')
        split = assignments[im['source_id']]
        datasets[split]['images'].append({'id': i, 'file_name': image_file.name, 'width': im['width'],
                                         'height': im['height'], 'source_id': im['source_id'],
                                         'group_id': components[im['source_id']], 'origin': 'manual',
                                         'annotator': row['annotator'], 'annotated_at': row['annotated_at'],
                                         'sha256': im['sha256'], 'source_pts': im['pts'],
                                         'source_time_base': im['time_base'], 'source_offset_s': im['offset_s']})
        for obj in row['objects']:
            annotation = validate_object(obj, im['width'], im['height'], segmentation, package)
            datasets[split]['annotations'].append({'id': len(datasets[split]['annotations'])+1,
                                                   'image_id': i, 'category_id': 1, 'iscrowd': 0,
                                                   'origin': 'manual', **annotation})
    if any(not d['images'] or not d['annotations'] for d in datasets.values()):
        raise ValueError('Each independent split needs reviewed images and positive instances')
    prepare_output(output)
    for split, data in datasets.items():
        folder = output / split
        folder.mkdir()
        write_json(folder / '_annotations.coco.json', data)
        for im in data['images']:
            shutil.copyfile(safe_file(package, f'images/{im["file_name"]}'), folder / im['file_name'])
    proof = {'schema_version': 'izge-human-coco-1', 'human_reviewed': True, 'task': 'segmentation' if segmentation else 'detection',
             'split_verified': True, 'sources': sources, 'assignments': assignments,
             'package_manifest_sha256': checksum(package / 'manifest.json'),
             'annotations_sha256': checksum(annotations_path), 'splits_sha256': checksum(split_path),
             'selected_image_ids': sorted(selected),
             'class_mapping': {'coco_category_id': 1, 'rfdetr_roboflow_contiguous_label': 0,
                               'pretrained_coco_cow_id': 21},
             'files_sha256': {str(p.relative_to(output)): checksum(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    write_json(output / 'dataset_manifest.json', proof)
    return proof


def validate_training_dataset(root: Path, task: str = 'segmentation') -> dict:
    proof = json.loads((root / 'dataset_manifest.json').read_text())
    if not proof.get('human_reviewed') or not proof.get('split_verified') or proof.get('task') != task:
        raise ValueError('Human labels, independent splits and matching training task required')
    components = validate_splits(proof['sources'], proof['assignments'])
    image_hashes, groups, expected_files = {}, {}, set()
    counts = {}
    for split in sorted(SPLITS):
        expected_files.add(f'{split}/_annotations.coco.json')
        data = json.loads((root / split / '_annotations.coco.json').read_text())
        if data['categories'] != [{'id': 1, 'name': 'cow', 'supercategory': 'animal'}]:
            raise ValueError('COCO cow category map changed')
        if not data['images'] or not data['annotations']:
            raise ValueError('Empty split')
        image_ids = {im['id']: im for im in data['images']}
        if len(image_ids) != len(data['images']):
            raise ValueError('Duplicate image ID')
        for im in data['images']:
            expected_files.add(f'{split}/{im["file_name"]}')
            if im['origin'] != 'manual' or not im['annotator'] or not im['annotated_at']:
                raise ValueError('Human provenance missing')
            if (proof['assignments'].get(im['source_id']) != split
                    or components.get(im['source_id']) != im['group_id']):
                raise ValueError('COCO image source/group differs from verified split')
            image_path = f'{split}/{im["file_name"]}'
            if image_path not in proof['files_sha256']:
                raise ValueError('Dataset integrity manifest does not cover every COCO image/annotation file')
            if proof['files_sha256'][image_path] != im['sha256']:
                raise ValueError('COCO image checksum differs from dataset integrity manifest')
            for key, seen in ((im['sha256'], image_hashes), (im['group_id'], groups)):
                if key in seen and seen[key] != split:
                    raise ValueError('Image/group leakage in COCO')
                seen[key] = split
        for ann in data['annotations']:
            if ann.get('origin') != 'manual' or ann['category_id'] != 1 or ann['image_id'] not in image_ids:
                raise ValueError('COCO annotation not manual or references wrong image/class')
            if task == 'segmentation' and not ann.get('segmentation'):
                raise ValueError('Mask missing in segmentation export')
        counts[split] = {'images': len(data['images']), 'instances': len(data['annotations'])}
    if set(proof['files_sha256']) != expected_files:
        raise ValueError('Dataset integrity manifest does not cover every COCO image/annotation file')
    for name, digest in proof['files_sha256'].items():
        if checksum(safe_file(root, name)) != digest:
            raise ValueError('COCO dataset checksum mismatch')
    return counts
