import json
import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PIL import Image

from izge.domain import Frame
from izge.vision.config import BehaviorConfig, load
from izge.vision.detection_inspection import inspect_detection
from izge.vision.detector import (
    Detection, DetectorUnavailable, RFDETRSegDetector, class_map_from_model, convert_predictions,
    cow_class_id, deduplicate_rois, restore_mask, select_device, validate_mode,
)
from izge.vision.geometry import CoordinateTransform
from izge.vision.preflight import checksum, inspect_video
from izge.vision.roi_review import DRAFT_LABEL, review_rois

import test_behavior_preflight as preflight_fixtures

config = preflight_fixtures.config
video = preflight_fixtures.video

ROOT = Path(__file__).resolve().parents[2]


def prediction(boxes=(), ids=(), scores=(), mask=None, names=None):
    return SimpleNamespace(xyxy=np.array(boxes, dtype=float).reshape(-1, 4),
                           class_id=np.array(ids, dtype=int), confidence=np.array(scores, dtype=float),
                           mask=mask, data={'class_name': names} if names is not None else {})


def transform():
    return CoordinateTransform.build((100, 60), (0, 0, 100, 60), (384, 384), 'resize')


def verified_config(width=100, height=60, reference_hash='a'*64):
    return BehaviorConfig.model_validate({
        'schema_version': 'behavior-config-1', 'camera_id': 'test',
        'reference_width': width, 'reference_height': height, 'placeholder': False,
        'geometry_verification': {'revision': 'test', 'camera_position_revision': 'test',
                                  'verified_by': 'Unit test only', 'verified_at': '2026-10-11T00:00:00Z',
                                  'reference_sha256': reference_hash},
        'animal_rois': [{'id': 'left', 'side': 'left', 'box': [0, 0, width, height]}],
        'feeder_rois': [{'id': 'feed', 'box': [0, 0, width, 10]}],
    })


def test_sparse_coco_and_contiguous_class_maps_are_not_mixed():
    coco = {1: 'person', 21: 'cow'}
    pretrained = SimpleNamespace(class_names=list(coco.values()), model_config=SimpleNamespace(num_classes=90))
    assert cow_class_id(class_map_from_model(pretrained, coco)) == 21
    custom = SimpleNamespace(class_names=['cow', 'person'], model_config=SimpleNamespace(num_classes=2))
    assert cow_class_id(class_map_from_model(custom, coco)) == 0
    with pytest.raises(DetectorUnavailable, match='cow_class_unavailable'):
        cow_class_id({19: 'horse'})


def test_empty_and_multiple_results_with_real_class_names():
    assert convert_predictions(prediction(), {21: 'cow'}, transform()) == []
    p = prediction([(1, 2, 20, 30), (40, 2, 60, 30), (70, 2, 90, 30)], [21, 21, 18], [.8, .6, .9],
                   names=['cow', 'cow', 'dog'])
    result = convert_predictions(p, {21: 'cow', 18: 'dog'}, transform())
    assert len(result) == 2 and [d.score for d in result] == [.8, .6]
    assert all(d.class_id == 21 and d.class_name == 'cow' for d in result)


@pytest.mark.parametrize('score', [-.1, 1.1, float('nan'), float('inf')])
def test_invalid_scores_fail_instead_of_becoming_empty(score):
    with pytest.raises(ValueError, match='skoru'):
        convert_predictions(prediction([(0, 0, 10, 10)], [21], [score]), {21: 'cow'}, transform())


@pytest.mark.parametrize('box', [(10, 0, 0, 10), (0, 0, 0, 10), (0, 0, float('nan'), 10)])
def test_invalid_boxes_fail(box):
    with pytest.raises(ValueError, match='kutusu'):
        convert_predictions(prediction([box], [21], [.7]), {21: 'cow'}, transform())


@pytest.mark.parametrize('change', ['unknown_id', 'fractional_id', 'wrong_name', 'count'])
def test_class_and_result_contract_validation(change):
    p = prediction([(0, 0, 10, 10)], [21], [.7], names=['cow'])
    if change == 'unknown_id':
        p.class_id = np.array([19])
    elif change == 'fractional_id':
        p.class_id = np.array([21.5])
    elif change == 'wrong_name':
        p.data['class_name'] = ['horse']
    else:
        p.confidence = np.array([])
    with pytest.raises(ValueError):
        convert_predictions(p, {21: 'cow'}, transform())


@pytest.mark.parametrize('mask', [np.zeros((1, 60, 100), dtype=np.uint8),
                               np.zeros((1, 59, 100), dtype=bool), np.zeros((0, 60, 100), dtype=bool)])
def test_invalid_masks_fail(mask):
    with pytest.raises(ValueError, match='[Mm]aske'):
        convert_predictions(prediction([(0, 0, 10, 10)], [21], [.7], mask), {21: 'cow'}, transform())


def test_library_input_space_is_translated_once_with_box_and_mask_together():
    t = CoordinateTransform.build((200, 100), (40, 25, 140, 75), (384, 384), 'resize')
    mask = np.zeros((1, 50, 100), dtype=bool)
    mask[0, 10:20, 20:30] = True
    p = prediction([(20, 10, 30, 20)], [21], [.8], mask)
    d = convert_predictions(p, {21: 'cow'}, t, 'input', 'left')[0]
    assert d.box == (60, 35, 70, 45)  # No second inverse of the library's 384 resize.
    assert d.mask.shape == (100, 200) and d.mask[35:45, 60:70].all()
    assert np.count_nonzero(d.mask) == 100 and d.roi_ids == ('left',)


def test_external_letterbox_inverse_restores_matching_mask_and_box():
    t = CoordinateTransform.build((12, 8), (2, 2, 10, 6), (8, 8))
    mask = np.zeros((1, 8, 8), dtype=bool)
    mask[0, 2:4, 0:4] = True
    d = convert_predictions(prediction([(0, 2, 4, 4)], [21], [.8], mask), {21: 'cow'}, t, 'model')[0]
    assert d.box == (2, 2, 6, 4)
    assert d.mask[2:4, 2:6].all() and np.count_nonzero(d.mask) == 8
    assert not d.mask[:2].any()
    with pytest.raises(ValueError):
        restore_mask(mask[0], t, 'input')  # Wrong coordinate contract cannot silently resize.


def test_border_clip_and_fully_outside_detection():
    p = prediction([(-3, -4, 110, 80), (110, 1, 120, 10)], [21, 21], [.8, .7])
    results = convert_predictions(p, {21: 'cow'}, transform())
    assert len(results) == 1 and results[0].box == (0, 0, 100, 60) and results[0].boundary_clipped


def test_cross_roi_duplicates_keep_best_score_and_both_provenances():
    mask = np.zeros((60, 100), dtype=bool)
    mask[10:40, 10:50] = True
    a = Detection((10, 10, 50, 40), 21, 'cow', .9, mask, ('left',))
    b = replace(a, score=.8, roi_ids=('overlap',))
    result = deduplicate_rois([b, a])
    assert len(result) == 1 and result[0].score == .9 and result[0].roi_ids == ('left', 'overlap')
    assert len(deduplicate_rois([a, replace(b, roi_ids=('left',))])) == 2  # Not generic box NMS.


def test_adjacent_animals_with_identical_boxes_and_disjoint_masks_are_retained():
    left, right = np.zeros((60, 100), dtype=bool), np.zeros((60, 100), dtype=bool)
    left[10:40, 10:30] = True
    right[10:40, 30:50] = True
    a = Detection((10, 10, 50, 40), 21, 'cow', .9, left, ('left',))
    b = replace(a, score=.8, mask=right, roi_ids=('overlap',))
    assert len(deduplicate_rois([a, b])) == 2
    assert len(deduplicate_rois([replace(a, mask=None), replace(b, mask=None)])) == 2


@pytest.mark.parametrize('requested', ['mps', 'cuda'])
def test_unavailable_device_is_explicit(requested):
    capabilities = {'cpu': True, 'mps': False, 'cuda': False}
    with pytest.raises(DetectorUnavailable, match=f'{requested}_unavailable'):
        select_device(requested, capabilities)
    assert select_device('auto', capabilities) == ('cpu', 'mps_unavailable')
    assert select_device('auto', {**capabilities, 'mps': True}) == ('mps', None)


def test_missing_weights_never_uses_random_model(tmp_path):
    with pytest.raises(DetectorUnavailable, match='weights_missing_or_checksum_mismatch'):
        RFDETRSegDetector(tmp_path / 'missing.pt', 'cpu', verified_config(), 'verified-roi')


def test_backend_model_failure_is_not_exposed_as_success_or_raw_secret(tmp_path, monkeypatch):
    import izge.vision.detector as module
    weights = tmp_path / 'unit-only.pt'
    weights.write_text('unit fixture, not real model weights')
    lock = tmp_path / 'unit-lock.json'
    lock.write_text(json.dumps({'weights_sha256': checksum(weights), 'package_version': 'unit-only'}))
    monkeypatch.setattr(module, 'version', lambda _: 'unit-only')
    def fail(**kwargs):
        raise RuntimeError('private-token-from-backend')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False, is_built=lambda: True)),
        cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(RFDETRSegSmall=fail))
    monkeypatch.setitem(sys.modules, 'torchvision.transforms', SimpleNamespace(functional=object()))
    monkeypatch.setitem(sys.modules, 'rfdetr.assets.coco_classes', SimpleNamespace(COCO_CLASSES={21: 'cow'}))
    with pytest.raises(DetectorUnavailable, match='^model_load_unavailable$'):
        RFDETRSegDetector(weights, 'cpu', verified_config(), 'verified-roi', model_lock=lock)


def test_placeholder_cannot_pass_roi_gate_but_full_frame_diagnostic_is_independent(config):
    draft = load(config)
    with pytest.raises(ValueError, match='ROI doğrulanmadı'):
        validate_mode(draft, (160, 90), 'verified-roi')
    assert validate_mode(draft, (160, 90), 'diagnostic-full-frame') == {
        'roi_verified': False, 'roi_analysis_allowed': False, 'experiment_only': True,
        'config_geometry_verified': False}
    assert not validate_mode(verified_config(), (100, 60), 'diagnostic-full-frame')['roi_analysis_allowed']


def test_raw_frame_detector_failure_never_becomes_empty_success():
    d = object.__new__(RFDETRSegDetector)
    d.config, d.mode, d.threshold = verified_config(), 'diagnostic-full-frame', .5
    def fail(*args, **kwargs):
        raise RuntimeError('private-token')
    d._predict = fail
    frame = Frame(0, Fraction(1, 1000), 0., 100, 60, av.VideoFrame.from_image(Image.new('RGB', (100, 60))))
    with pytest.raises(DetectorUnavailable, match='inference_or_output_validation_failed'):
        d.detect(frame)


def test_verified_roi_crops_raw_pixels_and_deduplicates_in_original_space():
    data = verified_config().model_dump(mode='json')
    data['animal_rois'] = [{'id': 'left', 'side': 'left', 'box': [0, 0, 80, 60]},
                           {'id': 'right', 'side': 'right', 'box': [20, 0, 100, 60]}]
    detector = object.__new__(RFDETRSegDetector)
    detector.config, detector.mode, detector.device = BehaviorConfig.model_validate(data), 'verified-roi', 'cpu'
    detector.inference_seconds = []
    detector.class_map = {21: 'cow'}
    images = []
    def predict_raw(image):
        assert image.size == (80, 60) and image.getpixel((0, 0)) == (13, 41, 89)
        start = 20 if not images else 0
        images.append(image)
        mask = np.zeros((1, 60, 80), dtype=bool)
        mask[0, 10:40, start:start+40] = True
        return prediction([(start, 10, start+40, 40)], [21], [.8], mask)
    detector._predict = predict_raw
    frame = Frame(1400, Fraction(1, 1000), 1.4, 100, 60,
                  av.VideoFrame.from_image(Image.new('RGB', (100, 60), (13, 41, 89))))
    results = detector.detect(frame)
    assert len(images) == 2 and len(results) == 1
    assert results[0].box == (20, 10, 60, 40) and results[0].roi_ids == ('left', 'right')
    assert results[0].mask.shape == (60, 100) and results[0].mask[10:40, 20:60].all()


def test_full_frame_passes_raw_image_without_draft_roi_crop(config):
    detector = object.__new__(RFDETRSegDetector)
    detector.config, detector.mode, detector.device = load(config), 'diagnostic-full-frame', 'cpu'
    detector.inference_seconds, detector.class_map = [], {21: 'cow'}
    def predict_raw(image):
        assert image.size == (160, 90) and image.getpixel((0, 0)) == (13, 41, 89)
        return prediction()
    detector._predict = predict_raw
    frame = Frame(4200, Fraction(1, 1000), 4.2, 160, 90,
                  av.VideoFrame.from_image(Image.new('RGB', (160, 90), (13, 41, 89))))
    assert detector.detect(frame) == []


def test_library_postprocess_owns_original_coordinates_without_second_inverse():
    # Tensor fixture checks preprocessing and target_sizes without RF-DETR or downloaded weights.
    torch = pytest.importorskip('torch')
    detector = object.__new__(RFDETRSegDetector)
    detector.torch, detector.device, detector.threshold = torch, 'cpu', .5
    detector.class_map = {21: 'cow'}
    def resize(tensor, size, antialias):
        assert size == [384, 384] and antialias is False
        return torch.nn.functional.interpolate(tensor[None], size=size, mode='bilinear',
                                               align_corners=False, antialias=antialias)[0]
    detector.functional = SimpleNamespace(
        to_tensor=lambda im: torch.from_numpy(np.array(im)).permute(2, 0, 1).float()/255.,
        resize=resize,
        normalize=lambda t, mean, std: (t-torch.tensor(mean)[:, None, None])/torch.tensor(std)[:, None, None])
    def forward(tensor):
        assert tensor.shape == (1, 3, 384, 384)
        assert tensor[0, 0, 0, 0].item() == pytest.approx((1.-.485)/.229)
        return {'unit_tensor_fixture': True}
    def postprocess(raw, target_sizes, score_threshold):
        assert raw['unit_tensor_fixture'] and target_sizes.tolist() == [[30, 50]] and score_threshold == .5
        mask = torch.zeros((1, 1, 30, 50), dtype=torch.bool)
        mask[0, 0, 2:10, 3:15] = True
        return [{'boxes': torch.tensor([[3., 2., 15., 10.]]), 'labels': torch.tensor([21]),
                 'scores': torch.tensor([.8]), 'masks': mask}]
    detector.model = SimpleNamespace(means=[.485, .456, .406], stds=[.229, .224, .225],
                                     model=SimpleNamespace(model=forward, postprocess=postprocess))
    result = detector._predict(Image.new('RGB', (50, 30), (255, 0, 0)))
    assert result.xyxy.tolist() == [[3., 2., 15., 10.]] and result.mask.shape == (1, 30, 50)
    assert result.data['class_name'] == ['cow']


class UnitDetector:
    """Dependency injection only; never used by the real CLI or presented as model evidence."""
    def __init__(self, weights, device, config, mode, threshold):
        self.provenance = {'model_class': 'unit_fixture'}
        self.capabilities = {'cpu': True, 'mps': False, 'cuda': False}
        self.device, self.fallback_reason = 'cpu', None
        self.class_map, self.cow_id = {21: 'cow'}, 21
        self.load_seconds, self.inference_seconds = .01, []

    def detect(self, frame):
        self.inference_seconds.append(.02)
        return []


def test_zero_detections_write_explicit_success_and_pts_not_fps(video, config, tmp_path):
    output = tmp_path / 'out'
    manifest = inspect_detection(video, config, Path('unit-unused'), output, detector_factory=UnitDetector)
    assert manifest['status'] == 'success' and manifest['source']['unchanged']
    rows = [json.loads(line) for line in (output / 'detections.jsonl').read_text().splitlines()]
    assert len(rows) == 4 and all(r['status'] == 'success' and r['detections'] == [] for r in rows)
    assert [row['offset_s'] for row in rows] == [0., .16, .3, .4]
    assert all(row['time_base'] and isinstance(row['pts'], int) for row in rows)
    assert not manifest['roi_analysis_allowed'] and not manifest['identity_results_produced']
    assert len(list(output.glob('detection_*.png'))) == 4
    with pytest.raises(ValueError, match='yeni veya boş'):
        inspect_detection(video, config, Path('unit-unused'), output, detector_factory=UnitDetector)


def test_cli_model_unavailable_returns_nonzero_with_failed_manifest(video, config, tmp_path):
    output = tmp_path / 'unavailable'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/inspect_behavior_detection.py'),
                             '--source', str(video), '--config', str(config), '--model', str(tmp_path/'missing.pt'),
                             '--device', 'cpu', '--mode', 'diagnostic-full-frame', '--output-dir', str(output)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and 'weights_missing_or_checksum_mismatch' in result.stderr
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['status'] == 'failed' and not manifest['model_loaded'] and not manifest['inference_completed']
    assert manifest['frames'] == [] and not (output / 'detections.jsonl').exists()


def test_verified_roi_requires_matching_source_checksum(video, config, tmp_path):
    config.write_text(verified_config(160, 90).model_dump_json())
    manifest = inspect_detection(video, config, Path('unit-unused'), tmp_path/'mismatch',
                                 mode='verified-roi', detector_factory=UnitDetector)
    assert manifest['status'] == 'failed' and manifest['errors'] == [{'code': 'roi_reference_checksum_mismatch'}]
    config.write_text(verified_config(160, 90, checksum(video)).model_dump_json())
    manifest = inspect_detection(video, config, Path('unit-unused'), tmp_path/'match',
                                 mode='verified-roi', detector_factory=UnitDetector)
    assert manifest['status'] == 'success' and manifest['roi_analysis_allowed']


def test_draft_review_does_not_create_verifier_and_preflight_still_does_not_draw(video, config, tmp_path):
    data = json.loads(config.read_text())
    data['animal_rois'] = [{'id': 'left_candidate', 'side': 'left', 'box': [0, 0, 80, 90]}]
    config.write_text(json.dumps(data))
    before = config.read_bytes()
    result = review_rois(video, config, tmp_path/'review')
    assert config.read_bytes() == before and result['config']['geometry_verification'] is None
    assert result['geometry_verification'] is None and not result['roi_verified']
    assert DRAFT_LABEL in (tmp_path/'review/roi_review.md').read_text()
    assert len(list((tmp_path/'review').glob('roi_draft_*.png'))) == 4
    assert inspect_video(video, config, tmp_path/'preflight')['roi']['preview_file'] is None
    config.write_text(verified_config(160, 90).model_dump_json())
    with pytest.raises(ValueError, match='Taslak inceleme'):
        review_rois(video, config, tmp_path/'reject')


def test_imports_do_not_load_or_download_a_model():
    result = subprocess.run([sys.executable, '-c',
                             "import sys; sys.path.insert(0,'backend'); import izge.vision.detector; "
                             "import izge.vision.roi_review; assert not {'torch','rfdetr','ultralytics'} & set(sys.modules)"],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
