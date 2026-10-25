"""Deterministic component splits and explicit sealed-test access."""
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from ..vision.annotation_data import leakage_components, safe_file, validate_splits
from ..vision.artifact_io import checksum, write_json
from .validation import validate


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def check_leakage(items: list[dict], sources: list[dict]) -> None:
    components = leakage_components(sources)
    seen, hashes, labels = {}, {}, {}
    for row in items:
        group = components[row['source_video']]
        split = row['split']
        if split not in {'train', 'val', 'test'}:
            raise ValueError('Invalid split')
        if seen.setdefault(group, split) != split or hashes.setdefault(row['sha256'], split) != split:
            raise ValueError('Dataset leakage across splits')
        if row.get('label_path') and labels.setdefault(row['label_path'], split) != split:
            raise ValueError('Label file shared across splits; export separate annotation files')


def split_inventory(value: dict, seed: int = 42, holdout: tuple[str, ...] = ('barn3',),
                    group_by: str = 'camera-day') -> dict:
    result = validate(value)
    if result['errors']:
        raise ValueError('Inventory or annotation validation failed')
    if group_by not in {'camera-day', 'source-video'}:
        raise ValueError('Unknown grouping policy')
    sources = [dict(r) for r in value['items'] if r['kind'] == 'video']
    if not sources:
        raise ValueError('Explicit source videos required')
    for row in sources:
        if not row.get('camera'):
            raise ValueError('Explicit source camera required')
        row['reserved_test_candidate'] = row.get('camera') in holdout
    # Existing gate is deliberately stricter than camera/day: simultaneous cameras,
    # derivatives, sessions, multi-day recordings and overlaps stay connected.
    components = leakage_components(sources)
    groups = sorted(set(components.values()))
    locked = {components[s['id']] for s in sources if s['reserved_test_candidate']}
    available = [g for g in groups if g not in locked]
    random.Random(seed).shuffle(available)
    if len(available) < (2 if locked else 3):
        raise ValueError('Need independent train/val/test groups with verified days')
    assignments = {g: 'test' for g in locked}
    assignments[available.pop()] = 'valid'
    if not locked:
        assignments[available.pop()] = 'test'
    for group in available:
        assignments[group] = 'train'
    by_source = {s['id']: assignments[components[s['id']]] for s in sources}
    validate_splits(sources, by_source)
    for source in sources:
        source['split'] = 'val' if by_source[source['id']] == 'valid' else by_source[source['id']]
    items = []
    item_kind = 'frame' if any(r['kind'] == 'frame' for r in value['items']) else 'video'
    for row in value['items']:
        if row['kind'] != item_kind:
            continue
        source = row.get('source_video')
        if source not in by_source:
            raise ValueError('Every frame needs explicit source_video metadata')
        parent = next(s for s in sources if s['id'] == source)
        if any(row.get(key) not in (None, parent.get(key)) for key in ('camera', 'day')):
            raise ValueError('Frame/source camera or day mismatch')
        items.append(dict(id=row['id'], source_video=source, camera=parent['camera'], day=parent['day'],
            path=row['path'], label_path=row.get('label_path'), sha256=row['sha256'],
            label_sha256=checksum(safe_file(Path(value['root']), row['label_path'])) if row.get('label_path') else None,
            split='val' if by_source[source] == 'valid' else by_source[source]))
    check_leakage(items, sources)
    config = dict(seed=seed, holdout=sorted(holdout), group_by=group_by,
                  effective_policy='source_session_family_overlap_and_day_across_cameras',
                  inventory_sha256=config_hash(value))
    return dict(schema_version='izge-dataset-1', root=value['root'], sealed_test=True,
                config=config, config_hash=config_hash(config), sources=sources, items=items,
                stats=dict(Counter(r['split'] for r in items)))


def write_manifest(value: dict, dataset: str, version: str) -> Path:
    if any(not token or token in {'.', '..'} or '/' in token or '\\' in token for token in (dataset, version)):
        raise ValueError('Dataset/version must be a single directory name')
    path = Path(value['root']) / 'manifests' / dataset / version / 'manifest.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # The native training runner compares this immutable dataset version with its
    # approved recipe before it gives an adapter any paths.
    write_json(path, {**value, 'version': version})
    return path


def load_manifest(path: Path, *, final_eval: bool = False, split: str | None = None,
                  data_root: Path | None = None) -> dict:
    value = json.loads(Path(path).read_text())
    if value.get('schema_version') != 'izge-dataset-1' or value.get('sealed_test') is not True:
        raise ValueError('Sealed versioned dataset required')
    if not isinstance(value.get('version'), str) or not value['version']:
        raise ValueError('Dataset version required')
    if value['config_hash'] != config_hash(value['config']):
        raise ValueError('Config hash mismatch')
    check_leakage(value['items'], value['sources'])
    validate_splits(value['sources'], {s['id']: 'valid' if s['split'] == 'val' else s['split']
                                      for s in value['sources']})
    sources = {s['id']: s for s in value['sources']}
    if len({r['id'] for r in value['items']}) != len(value['items']):
        raise ValueError('Duplicate manifest item')
    for row in value['items']:
        source = sources[row['source_video']]
        if row['split'] != source['split'] or any(row[key] != source[key] for key in ('camera', 'day')):
            raise ValueError('Manifest item/source mismatch')
    if split not in {None, 'train', 'val', 'test'}:
        raise ValueError('Invalid split')
    if split == 'test' and not final_eval:
        raise ValueError('Test is sealed; explicit --final-eval required')
    selected = [r for r in value['items'] if (split is None or r['split'] == split)
                and (final_eval or r['split'] != 'test')]
    root = Path(data_root or value['root'])
    for row in selected:
        if checksum(safe_file(root, row['path'])) != row['sha256']:
            raise ValueError('Dataset checksum changed')
        if row.get('label_path') and checksum(safe_file(root, row['label_path'])) != row['label_sha256']:
            raise ValueError('Annotation checksum changed')
    value['items'] = selected
    allowed = {r['source_video'] for r in selected}
    value['sources'] = [s for s in value['sources'] if s['id'] in allowed]
    value['root'] = str(root)
    value['stats'] = dict(Counter(r['split'] for r in selected))
    return value
