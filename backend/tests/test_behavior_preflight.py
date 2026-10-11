import csv
import hashlib
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import av
import pytest
from PIL import Image

from izge.domain import SourceGap
from izge.video import FileVideoSource
from izge.vision.preflight import PreflightError, inspect_video

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def video(tmp_path):
    source = tmp_path / 'vfr.mp4'
    with av.open(str(source), 'w') as container:
        stream = container.add_stream('mpeg4', rate=25)
        stream.width, stream.height, stream.pix_fmt = 160, 90, 'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
        for i, pts in enumerate((0, 40, 160, 230, 300, 400)):
            frame = av.VideoFrame.from_image(Image.new('RGB', (160, 90), (i * 30, 40, 80)))
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return source


@pytest.fixture
def config(tmp_path):
    path = tmp_path / 'behavior.json'
    path.write_text(json.dumps({
        'schema_version': 'behavior-config-1', 'camera_id': 'test',
        'reference_width': 160, 'reference_height': 90, 'placeholder': True,
        'animal_rois': [], 'feeder_rois': [],
    }))
    return path


def test_real_cli_vfr_outputs_checksum_and_placeholder_warning(video, config, tmp_path):
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    output = tmp_path / 'out'
    result = subprocess.run([
        sys.executable, str(ROOT / 'scripts/inspect_behavior_video.py'),
        '--source', str(video), '--config', str(config), '--output-dir', str(output),
    ], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'ROI doğrulanmadı' in result.stderr
    assert 'Ham ilk/son PTS' in result.stdout and 'kapsayıcı' in result.stdout
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['source']['sha256_before'] == manifest['source']['sha256_after'] == digest
    assert hashlib.sha256(video.read_bytes()).hexdigest() == digest
    assert manifest['timing']['counts']['valid_timestamp_frames'] == 6
    assert manifest['timing']['gap_reasons'] == {}
    assert manifest['timing']['last_frame_offset_s'] == .4
    assert manifest['metadata']['video_stream_duration_s'] > .4
    assert manifest['metadata']['container_duration_s'] >= .4
    assert manifest['clock'] == {'verified': False, 'recorded_at': None}
    assert not manifest['roi']['verified_for_video'] and not manifest['roi']['analysis_allowed']
    assert manifest['roi']['preview_file'] is None
    assert manifest['model_loaded'] is False and manifest['behavior_results_produced'] is False
    assert len(list(output.glob('sample_*.png'))) == len(manifest['samples']) == 4
    assert len({sample['decoded_index'] for sample in manifest['samples']}) == 4
    for path in output.glob('sample_*.png'):
        with Image.open(path) as image:
            assert image.size == (160, 90) and image.format == 'PNG'
    with (output / 'frames.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 6
    assert [float(row['offset_s']) for row in rows] == [0, .04, .16, .23, .3, .4]
    assert any(row['sampling_reason'] == 'planned_sampling_skip' for row in rows)
    assert all(row['pts'] for row in rows)


def test_explicit_verification_record_produces_geometry_preview(video, config, tmp_path):
    data = json.loads(config.read_text())
    data.update({
        'placeholder': False,
        'geometry_verification': {'revision': 'g1', 'camera_position_revision': 'p1',
                                  'verified_by': 'Test', 'verified_at': '2026-10-11T12:00:00Z',
                                  'reference_sha256': 'a' * 64},
        'animal_rois': [{'id': 'left', 'side': 'left', 'box': [0, 0, 80, 90]}],
        'feeder_rois': [{'id': 'feed', 'animal_roi_id': 'left', 'box': [0, 0, 80, 20]}],
    })
    config.write_text(json.dumps(data))
    manifest = inspect_video(video, config, tmp_path / 'verified')
    assert manifest['roi']['verified_for_video']
    assert manifest['roi']['preview_file'] == 'roi_preview.png'
    assert (tmp_path / 'verified/roi_preview.png').is_file()


def test_output_reuse_is_rejected_without_touching_existing_files(video, config, tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'manifest.json'
    marker.write_text('user data')
    with pytest.raises(PreflightError, match='yeni veya boş'):
        inspect_video(video, config, output)
    assert marker.read_text() == 'user data'
    with pytest.raises(PreflightError):
        inspect_video(video, config, video.parent)


def test_resolution_mismatch_rejects_before_writing(video, config, tmp_path):
    data = json.loads(config.read_text())
    data['reference_height'] = 120
    config.write_text(json.dumps(data))
    with pytest.raises(PreflightError, match='çözünürlük'):
        inspect_video(video, config, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_partial_decode_is_visible_in_manifest_and_csv(video, config, tmp_path, monkeypatch):
    good = next(FileVideoSource(video).frames())
    monkeypatch.setattr(FileVideoSource, 'read_events', lambda self: iter([
        good, SourceGap('missing_pts', decoded_index=1), SourceGap('decode_error')]))
    manifest = inspect_video(video, config, tmp_path / 'partial')
    assert manifest['timing']['status'] == 'partial'
    assert manifest['timing']['gap_reasons'] == {'missing_pts': 1, 'decode_error': 1}
    assert manifest['timing']['source_gaps'][0]['end_offset'] is None
    assert manifest['source']['unchanged']
    assert not manifest['roi']['analysis_allowed']
    with (tmp_path / 'partial/frames.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert rows[1]['pts'] == '' and rows[1]['offset_s'] == ''
    assert rows[2]['source_status'] == 'decode_error'


def test_empty_decode_is_failed_not_successful(video, config, tmp_path, monkeypatch):
    monkeypatch.setattr(FileVideoSource, 'read_events', lambda self: iter([SourceGap('decode_error')]))
    manifest = inspect_video(video, config, tmp_path / 'failed')
    assert manifest['timing']['status'] == 'failed'
    assert manifest['samples'] == [] and manifest['timing']['last_frame_pts'] is None


def test_invalid_yaml_errors_are_safe(video, config, tmp_path):
    config.write_text('value: [invalid-yaml-and-private-token')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/inspect_behavior_video.py'),
                             '--source', str(video), '--config', str(config),
                             '--output-dir', str(tmp_path / 'invalid')],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 2
    assert 'private-token' not in result.stderr and 'Traceback' not in result.stderr


def test_invalid_video_is_rejected_with_safe_error(config, tmp_path):
    source = tmp_path / 'invalid.mp4'
    source.write_text('not a video; private-content')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/inspect_behavior_video.py'),
                             '--source', str(source), '--config', str(config),
                             '--output-dir', str(tmp_path / 'invalid')],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 2
    assert 'private-content' not in result.stderr and 'Traceback' not in result.stderr
    assert not (tmp_path / 'invalid').exists()


def test_preflight_import_does_not_import_model_libraries():
    result = subprocess.run([sys.executable, '-c',
                            "import sys; sys.path.insert(0, 'backend'); import izge.vision.preflight; "
                            "assert not {'torch', 'transformers', 'ultralytics'} & set(sys.modules)"],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
