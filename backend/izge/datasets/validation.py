"""Adapters around the repository's existing human annotation formats."""
import json
import math
from pathlib import Path

from PIL import Image

from ..feeding.boris import load_mapping, parse
from ..feeding.states import STATES
from ..vision.annotation_data import human_record, safe_file, validate_object
from ..vision.artifact_io import checksum


def validate_annotation(root: Path, spec: dict, items: dict) -> set[tuple[str, str]]:
    path = safe_file(root, spec['path'])
    kind = spec['format']
    covered = set()
    if kind == 'boris':
        source = items[spec['source_video']]
        duration = source['media']['duration']
        events = parse(path.read_text(), load_mapping(), set(spec['stalls']), duration)
        if any(not math.isfinite(e.end) or e.end > duration for e in events):
            raise ValueError('Interval outside video')
        # Intervals are not whole-video completeness evidence.
        return covered
    data = json.loads(path.read_text())
    if kind == 'stall-state':
        # Portable snapshot of effective crop labels, after latest_labels/retraction resolution.
        seen = set()
        for row in data['samples']:
            if row['sample_id'] in seen:
                raise ValueError('Duplicate effective crop sample')
            seen.add(row['sample_id'])
            safe_file(root, row['crop_key'])
            if row['label'] not in (*STATES, 'skipped') or row['origin'] != 'manual' or not row['labeler'].strip():
                raise ValueError('Invalid effective crop label')
            if row['label'] in STATES:
                covered.add((row['crop_key'], 'feeding_head_state'))
    elif kind == 'human-frames':
        package = safe_file(root, spec['package_manifest'])
        manifest = json.loads(package.read_text())
        images = {im['id']: im for im in manifest['images']}
        seen = set()
        for row in data['frames']:
            human_record(row)
            im = images[row['image_id']]
            if row['image_id'] in seen:
                raise ValueError('Duplicate human frame')
            seen.add(row['image_id'])
            image_path = safe_file(package.parent, im['file'])
            if checksum(image_path) != im['sha256']:
                raise ValueError('Image checksum changed')
            with Image.open(image_path) as image:
                if image.size != (im['width'], im['height']):
                    raise ValueError('Original image dimensions changed')
                for obj in row['objects']:
                    validate_object(obj, image.width, image.height, False, package.parent)
            if row['objects'] or row.get('negative'):
                if all(obj.get('posture') in {'standing', 'lying', 'unknown'} for obj in row['objects']):
                    covered.add((image_path.relative_to(root).as_posix(), 'posture'))
    elif kind == 'coco':
        images = {im['id']: im for im in data['images']}
        categories = {c['id']: c['name'] for c in data['categories']}
        if len(images) != len(data['images']) or len(categories) != len(data['categories']):
            raise ValueError('Duplicate COCO IDs')
        if not categories or set(categories.values()) != {'cow'}:
            raise ValueError('Unknown COCO class; use exported cow mapping')
        for im in images.values():
            with Image.open(safe_file(path.parent, im['file_name'])) as image:
                if image.size != (im['width'], im['height']):
                    raise ValueError('COCO image dimensions mismatch')
        seen = set()
        for ann in data['annotations']:
            if ann['id'] in seen or ann['category_id'] not in categories:
                raise ValueError('Duplicate annotation or unknown class')
            seen.add(ann['id'])
            im = images[ann['image_id']]
            x, y, w, h = ann['bbox']
            if not all(math.isfinite(v) for v in (x, y, w, h)) or not (
                0 <= x < x+w <= im['width'] and 0 <= y < y+h <= im['height']):
                raise ValueError('COCO box outside image')
        # Standard cow COCO contains no posture, feeding or event labels.
    else:
        raise ValueError('Unsupported annotation format')
    return covered


def validate(value: dict) -> dict:
    root = Path(value['root'])
    items = {r['path']: r for r in value['items']}
    errors = list(value.get('errors', []))
    covered = set()
    intervals = {}
    for row in items.values():
        try:
            if checksum(safe_file(root, row['path'])) != row['sha256']:
                raise ValueError('Checksum changed')
            if row.get('source_video') and row['source_video'] not in items:
                raise ValueError('Source video missing from inventory')
            if row.get('label_path'):
                safe_file(root, row['label_path'])
        except (OSError, ValueError) as exc:
            errors.append(dict(path=row['path'], error=str(exc)))
    for spec in value.get('annotations', []):
        try:
            covered |= validate_annotation(root, spec, items)
            if spec['format'] == 'boris':
                source = items[spec['source_video']]
                events = parse(safe_file(root, spec['path']).read_text(), load_mapping(),
                               set(spec['stalls']), source['media']['duration'])
                for event in events:
                    key = (spec['source_video'], event.stall)
                    for other in intervals.get(key, []):
                        if event.start < other.end and other.start < event.end:
                            raise ValueError('Overlapping BORIS intervals across annotation files')
                    intervals.setdefault(key, []).append(event)
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            errors.append(dict(path=spec.get('path'), error=str(exc)))
    return dict(errors=errors, covered=sorted(covered))


def labels_needed(value: dict) -> dict:
    result = validate(value)
    if result['errors']:
        raise ValueError('Validate annotations before computing missing labels')
    covered = {tuple(pair) for pair in result['covered']}
    root = Path(value['root'])
    by_path = {r['path']: r for r in value['items']}
    intervals = {}
    for spec in value.get('annotations', []):
        if spec['format'] != 'boris':
            continue
        source = by_path[spec['source_video']]
        rows = parse(safe_file(root, spec['path']).read_text(), load_mapping(),
                     set(spec['stalls']), source['media']['duration'])
        for stall in spec['stalls']:
            intervals.setdefault((source['path'], stall), []).extend(
                (e.start, e.end) for e in rows if e.stall == stall)
    missing, strata = [], {}
    for row in value['items']:
        if row['kind'] not in {'video', 'frame'}:
            continue
        for task in ('feeding_head_state', 'posture', 'events'):
            if (row['path'], task) in covered:
                continue
            gaps = []
            if row['kind'] == 'video' and task == 'feeding_head_state':
                for (source, stall), spans in intervals.items():
                    if source != row['path']:
                        continue
                    cursor = 0.
                    for start, end in sorted(spans):
                        if start > cursor:
                            gaps.append(dict(stall=stall, start=cursor, end=start))
                        cursor = max(cursor, end)
                    if cursor < row['media']['duration']:
                        gaps.append(dict(stall=stall, start=cursor, end=row['media']['duration']))
                if any(source == row['path'] for source, _ in intervals) and not gaps:
                    continue
            key = (task, row.get('light', 'unknown'), row.get('distance', 'unknown'),
                   row.get('visibility', 'unknown'))
            name = '/'.join(key)
            strata[name] = strata.get(name, 0) + 1
            missing.append(dict(path=row['path'], source_video=row.get('source_video'),
                camera=row.get('camera'), day=row.get('day'), task=task, stratum=name,
                missing_intervals=gaps,
                reason='No complete reviewed annotation for this unit'))
    return dict(missing=missing, strata=strata,
                note='Units, not promised label quotas. Partial BORIS intervals do not complete a video. '
                     'Events require separate visible evidence; feeding intervals are not posture events.')
