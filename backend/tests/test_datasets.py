"""Synthetic data only: tests do not provide human labels or field metrics."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from izge.datasets import check_leakage, load_manifest
from izge.datasets.__main__ import main
from izge.datasets.inventory import inventory, read_inventory, save_inventory
from izge.datasets.manifest import split_inventory, write_manifest
from izge.datasets.validation import labels_needed, validate
from izge.vision.artifact_io import checksum


def synthetic_fixture(root: Path) -> dict:
    """Small generated videos and unique frames; no owner media touched."""
    root.mkdir(parents=True, exist_ok=True)
    metadata = {'sources': {}, 'annotations': []}
    for index, color in enumerate(('red', 'green', 'blue', 'white')):
        video = root / f'source{index}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        f'color={color}:s=32x24:r=2:d=1', '-c:v', 'mpeg4', str(video)],
                       check=True, capture_output=True)
        frame = root / f'frame{index}.png'
        Image.new('RGB', (32, 24), color).save(frame)
        info = dict(camera='barn3' if index == 3 else 'barn2', day=f'2026-01-0{index+1}',
                    light='color_daylight' if index % 2 else 'monochrome_night_appearance',
                    distance='near', visibility='visible', recording_day_verified=True,
                    recording_day_verification=dict(verified_by='synthetic_fixture',
                        verified_at='2026-01-10T12:00:00+00:00', evidence='test_fixture_only'))
        metadata['sources'][video.name] = info
        metadata['sources'][frame.name] = dict(info, source_video=video.name)
    return metadata


@pytest.fixture
def sample(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe required for generated video fixtures')
    metadata = synthetic_fixture(tmp_path / 'data')
    return inventory(tmp_path / 'data', metadata)


def label_file(value, name, data, spec):
    path = Path(value['root']) / name
    path.write_text(json.dumps(data))
    value['annotations'].append(dict(path=name, **spec))
    return path


def test_inventory_video_frame_labels_errors_and_parquet(sample, tmp_path):
    root = Path(sample['root'])
    (root / 'bad.mp4').write_bytes(b'not a video')
    (root / 'labels.json').write_text('{}')
    (root / 'outside.png').symlink_to(tmp_path / 'missing.png')
    value = inventory(root)
    assert value['errors'] == [{'path': 'bad.mp4', 'error': 'unreadable_or_invalid_metadata'}]
    video = next(r for r in value['items'] if r['kind'] == 'video')
    assert video['media']['width'] == 32 and video['media']['duration'] == 1
    assert video['sha256'] == checksum(root / video['path'])
    assert video['day'] is None and not video['recording_day_verified']
    assert any(r['kind'] == 'label' for r in value['items'])
    for extension in ('json', 'parquet'):
        path = tmp_path / f'inventory.{extension}'
        save_inventory(path, value)
        assert read_inventory(path) == value


def test_deterministic_split_sealed_access_and_integrity(sample):
    manifest = split_inventory(sample, seed=5)
    assert manifest == split_inventory(sample, seed=5)
    assert {r['split'] for r in manifest['items']} == {'train', 'val', 'test'}
    assert all(r['split'] == 'test' for r in manifest['items'] if r['camera'] == 'barn3')
    path = write_manifest(manifest, 'fixture', 'v1')
    assert path == Path(sample['root']) / 'manifests/fixture/v1/manifest.json'
    assert json.loads(path.read_text())['version'] == 'v1'
    assert all(r['split'] != 'test' for r in load_manifest(path)['items'])
    assert all(s['camera'] != 'barn3' for s in load_manifest(path)['sources'])
    with pytest.raises(ValueError, match='sealed'):
        load_manifest(path, split='test')
    assert load_manifest(path, final_eval=True, split='test')['items']
    assert main(['check-manifest', str(path), '--split', 'test']) == 2
    assert main(['check-manifest', str(path), '--split', 'test', '--final-eval']) == 0
    with pytest.raises(FileExistsError):
        write_manifest(manifest, 'fixture', 'v1')
    with pytest.raises(ValueError):
        write_manifest(manifest, '../escape', 'v2')
    invalid_version = json.loads(path.read_text())
    invalid_version.pop('version')
    path.write_text(json.dumps(invalid_version))
    with pytest.raises(ValueError, match='version'):
        load_manifest(path)
    path.write_text(json.dumps({**invalid_version, 'version': 'v1'}))
    row = load_manifest(path)['items'][0]
    (Path(sample['root']) / row['path']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        load_manifest(path)


def test_leakage_across_day_camera_family_hash_and_label(sample):
    manifest = split_inventory(sample)
    changed = copy.deepcopy(manifest['items'])
    changed.append(dict(changed[0], id='duplicate', split='test' if changed[0]['split'] != 'test' else 'train'))
    with pytest.raises(ValueError, match='leakage'):
        check_leakage(changed, manifest['sources'])
    changed = copy.deepcopy(manifest['items'])
    for row in changed:
        row['label_path'] = 'all-labels.json'
    with pytest.raises(ValueError, match='Label file'):
        check_leakage(changed, manifest['sources'])
    sources = copy.deepcopy(manifest['sources'])
    for row in sources:
        row['recording_day'] = '2026-01-01'
    with pytest.raises(ValueError, match='leakage'):
        check_leakage(manifest['items'], sources)
    sources = copy.deepcopy(manifest['sources'])
    for row in sources:
        row['family_id'] = 'same-derivative-family'
    with pytest.raises(ValueError, match='leakage'):
        check_leakage(manifest['items'], sources)


def test_unknown_day_missing_source_corruption_and_frame_mismatch_block_split(sample):
    unknown = copy.deepcopy(sample)
    for row in unknown['items']:
        row['recording_day_verified'] = False
    with pytest.raises(ValueError, match='verified days'):
        split_inventory(unknown)
    unknown = copy.deepcopy(sample)
    next(r for r in unknown['items'] if r['kind'] == 'frame')['source_video'] = None
    with pytest.raises(ValueError, match='source_video'):
        split_inventory(unknown)
    unknown = copy.deepcopy(sample)
    next(r for r in unknown['items'] if r['kind'] == 'frame')['camera'] = 'wrong'
    with pytest.raises(ValueError, match='mismatch'):
        split_inventory(unknown)
    unknown = copy.deepcopy(sample)
    unknown['errors'] = [{'error': 'broken file'}]
    with pytest.raises(ValueError, match='validation failed'):
        split_inventory(unknown)


def test_boris_bounds_unknown_class_overlap_and_missing_file(sample):
    root = Path(sample['root'])
    path = root / 'boris.csv'
    sample['annotations'] = [dict(path=path.name, format='boris', source_video='source0.mp4', stalls=['S1'])]
    header = 'Subject,Behavior,Start (s),Stop (s)\n'
    for text in ('S1,yemde,0,2\n', 'S1,unknown,0,1\n',
                 'S1,yemde,0,0.8\nS1,yemde_degil,0.5,1\n', 'S2,yemde,0,1\n'):
        path.write_text(header + text)
        assert validate(sample)['errors']
    path.write_text(header + 'S1,yemde,0,0.5\n')
    assert not validate(sample)['errors']
    needed = labels_needed(sample)
    entry = next(r for r in needed['missing'] if r['path'] == 'source0.mp4' and r['task'] == 'feeding_head_state')
    assert entry['missing_intervals'] == [dict(stall='S1', start=0.5, end=1.0)]
    second = root / 'second.csv'
    second.write_text(header + 'S1,yemde_degil,0.25,1\n')
    sample['annotations'].append(dict(sample['annotations'][0], path=second.name))
    assert 'across annotation files' in validate(sample)['errors'][0]['error']
    sample['annotations'].pop()
    path.unlink()
    assert validate(sample)['errors']


def test_coco_boxes_schema_unknown_categories_and_files(sample):
    data = dict(images=[dict(id=1, file_name='frame0.png', width=32, height=24)],
                categories=[dict(id=1, name='cow')],
                annotations=[dict(id=1, image_id=1, category_id=1, bbox=[0, 0, 20, 20])])
    path = label_file(sample, 'coco.json', data, dict(format='coco'))
    assert not validate(sample)['errors']
    for box in ([31, 0, 2, 2], [0, 0, -1, 2], [float('nan'), 0, 2, 2]):
        data['annotations'][0]['bbox'] = box
        path.write_text(json.dumps(data))
        assert validate(sample)['errors']
    data['annotations'][0]['bbox'] = [0, 0, 2, 2]
    data['categories'][0]['name'] = 'invalid'
    path.write_text(json.dumps(data))
    assert validate(sample)['errors']
    path.write_text('{}')
    assert validate(sample)['errors']


def test_stall_state_and_labels_needed_strata(sample):
    data = dict(samples=[dict(sample_id='sample', crop_key='frame0.png', label='yemde',
                              origin='manual', labeler='synthetic_fixture')])
    path = label_file(sample, 'stall.json', data, dict(format='stall-state'))
    result = labels_needed(sample)
    assert not any(r['path'] == 'frame0.png' and r['task'] == 'feeding_head_state' for r in result['missing'])
    assert any(r['path'] == 'frame0.png' and r['task'] == 'posture' for r in result['missing'])
    assert any('monochrome_night_appearance/near/visible' in k for k in result['strata'])
    for change in ({'label': 'invented'}, {'origin': 'model'}, {'labeler': ''}):
        changed = copy.deepcopy(data)
        changed['samples'][0].update(change)
        path.write_text(json.dumps(changed))
        assert validate(sample)['errors']


def test_human_annotation_adapter_reuses_existing_format(sample):
    root = Path(sample['root'])
    package = dict(images=[dict(id='frame', file='frame0.png', width=32, height=24,
                                sha256=checksum(root / 'frame0.png'))])
    (root / 'package.json').write_text(json.dumps(package))
    data = dict(frames=[dict(image_id='frame', status='reviewed', origin='manual',
        annotator='synthetic_fixture', annotated_at='2026-01-01T12:00:00+00:00',
        objects=[dict(class_name='cow', bbox_xyxy=[0, 0, 20, 20], posture='standing')])])
    path = label_file(sample, 'human.json', data, dict(format='human-frames', package_manifest='package.json'))
    assert ['frame0.png', 'posture'] in validate(sample)['covered'] or ('frame0.png', 'posture') in validate(sample)['covered']
    data['frames'][0]['objects'][0]['bbox_xyxy'] = [0, 0, 40, 20]
    path.write_text(json.dumps(data))
    assert validate(sample)['errors']


def test_cli_inventory_validate_split_labels_needed(sample, tmp_path, capsys):
    path = tmp_path / 'inventory.json'
    save_inventory(path, sample)
    assert main(['validate', '--inventory', str(path)]) == 0
    assert main(['split', '--inventory', str(path), '--dataset', 'test', '--version', 'cli']) == 0
    assert main(['labels-needed', '--inventory', str(path)]) == 0
    missing = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(missing['missing']) == 24
    assert main(['inventory', '--root', sample['root'], '--output', str(tmp_path / 'scan.json')]) == 0
    sample['errors'].append(dict(path='bad.mp4', error='unreadable'))
    bad = tmp_path / 'bad.json'
    save_inventory(bad, sample)
    assert main(['validate', '--inventory', str(bad)]) == 2
