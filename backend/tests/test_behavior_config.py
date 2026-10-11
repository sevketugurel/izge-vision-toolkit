import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from izge.vision.config import BehaviorConfig, load


def config_data(**changes):
    data = {
        'schema_version': 'behavior-config-1', 'camera_id': 'test_camera',
        'reference_width': 320, 'reference_height': 180, 'placeholder': False,
        'geometry_verification': {
            'revision': 'geometry-1', 'camera_position_revision': 'position-1',
            'verified_by': 'Test annotator', 'verified_at': '2026-10-11T12:00:00+03:00',
            'reference_sha256': 'a' * 64},
        'animal_rois': [{'id': 'left', 'side': 'left', 'box': [0, 0, 160, 180]},
                        {'id': 'right', 'side': 'right', 'box': [160, 0, 320, 180]}],
        'feeder_rois': [{'id': 'feed', 'animal_roi_id': 'left', 'polygon': [[0, 0], [100, 0], [100, 40]]}],
        'stalls': [{'id': 'stall', 'animal_roi_id': 'left', 'feeder_roi_id': 'feed', 'box': [0, 40, 100, 170]}],
    }
    data.update(changes)
    return data


def test_reference_is_placeholder_and_blocks_analysis():
    cfg = load(Path(__file__).resolve().parents[2] / 'config/behavior/reference_barn.yaml')
    assert cfg.schema_version == 'behavior-config-1'
    assert cfg.placeholder and not cfg.animal_rois and not cfg.feeder_rois
    assert not cfg.roi_verified_for(2560, 1440)
    with pytest.raises(ValueError, match='ROI doğrulanmadı'):
        cfg.require_analysis_ready(2560, 1440)


def test_verified_geometry_and_scaled_preview_policy():
    cfg = BehaviorConfig.model_validate(config_data())
    cfg.require_analysis_ready(320, 180)
    cfg.validate_resolution(640, 360)
    assert not cfg.roi_verified_for(640, 360)
    with pytest.raises(ValueError, match='ROI doğrulanmadı'):
        cfg.require_analysis_ready(640, 360)
    with pytest.raises(ValueError, match='en-boy'):
        cfg.validate_resolution(320, 240)


def test_placeholder_cannot_be_verified_even_with_verification_record():
    cfg = BehaviorConfig.model_validate(config_data(placeholder=True))
    assert not cfg.roi_verified_for(320, 180)


@pytest.mark.parametrize('changes', [
    {'schema_version': 'behavior-config-2'}, {'unexpected': 'secret'},
    {'reference_width': 0}, {'reference_width': True}, {'placeholder': 'false'},
    {'geometry_verification': None}, {'animal_rois': []}, {'feeder_rois': []},
    {'sampling': {'detection_hz': 0}}, {'sampling': {'posture_hz': float('nan')}},
    {'sampling': {'source_gap_threshold_s': -1}}, {'thresholds': {'max_gap_factor': 0}},
])
def test_invalid_root_fields(changes):
    with pytest.raises(ValidationError):
        BehaviorConfig.model_validate(config_data(**changes))


@pytest.mark.parametrize('region', [
    {'id': 'left', 'side': 'left', 'box': [-1, 0, 100, 100]},
    {'id': 'left', 'side': 'left', 'box': [0, 0, 400, 100]},
    {'id': 'left', 'side': 'left', 'box': [10, 10, 0, 0]},
    {'id': 'left', 'side': 'left', 'box': [0, 0, float('inf'), 100]},
    {'id': 'left', 'side': 'left'},
    {'id': 'left', 'side': 'left', 'box': [0, 0, 100, 100], 'polygon': [[0, 0], [10, 0], [10, 10]]},
    {'id': 'left', 'side': 'left', 'polygon': [[0, 0], [10, 10], [20, 20]]},
    {'id': 'left', 'side': 'left', 'polygon': [[0, 0], [100, 100], [0, 100], [100, 0]]},
    {'id': 'left', 'side': 'left', 'polygon': [[0, 0], [10, 0], [10, 10], [0, 0]]},
    {'id': 'left', 'side': 'middle', 'box': [0, 0, 100, 100]},
])
def test_invalid_roi_shapes(region):
    data = config_data()
    data['animal_rois'][0] = region
    with pytest.raises(ValidationError):
        BehaviorConfig.model_validate(data)


@pytest.mark.parametrize('mutation', ['duplicate', 'animal_reference', 'feeder_reference', 'naive_time'])
def test_relations_and_verification(mutation):
    data = config_data()
    if mutation == 'duplicate':
        data['stalls'][0]['id'] = 'left'
    elif mutation == 'animal_reference':
        data['feeder_rois'][0]['animal_roi_id'] = 'missing'
    elif mutation == 'feeder_reference':
        data['stalls'][0]['feeder_roi_id'] = 'missing'
    else:
        data['geometry_verification']['verified_at'] = '2026-10-11T12:00:00'
    with pytest.raises(ValidationError):
        BehaviorConfig.model_validate(data)


def test_fingerprint_is_order_independent_and_geometry_is_immutable():
    data = config_data()
    cfg = BehaviorConfig.model_validate(data)
    shuffled = BehaviorConfig.model_validate(dict(reversed(list(copy.deepcopy(data).items()))))
    assert cfg.fingerprint() == shuffled.fingerprint()
    with pytest.raises(ValidationError):
        cfg.placeholder = True
    assert isinstance(cfg.animal_rois, tuple)
    assert isinstance(cfg.feeder_rois[0].polygon, tuple)


def test_json_load(tmp_path):
    cfg = BehaviorConfig.model_validate(config_data())
    path = tmp_path / 'config.json'
    path.write_text(cfg.model_dump_json())
    assert load(path) == cfg
