import json

import numpy as np
import pytest

from izge.feeding.backbone import DINOV3_ID
from izge.feeding.states import STATES
from izge.training.adapters import feeding, rfdetr
from izge.training.config import approved_config


def test_feeding_wraps_existing_classifier(tmp_path, monkeypatch):
    monkeypatch.setenv('IZGE_DATA_ROOT', str(tmp_path))
    samples = []
    rng = np.random.default_rng(42)
    for split in ('train', 'val', 'test'):
        for i in range(9):
            path = f'{split}{i}.npy'
            np.save(tmp_path / path, rng.normal(size=8) + (i % 3) * 2)
            samples.append(dict(split=split, group_id=split, embedding=path, label=STATES[i % 3],
                                origin='manual', gold=False))
    manifest = tmp_path / 'manifests/feeding/v1/manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(dict(version='v1', backbone=DINOV3_ID, samples=samples)))
    directory = tmp_path / 'run'
    (directory / 'checkpoints').mkdir(parents=True)
    metrics = []
    feeding(approved_config('feeding_linear', {}), directory, False, metrics.append)
    assert metrics[0]['epoch'] == 1
    assert (directory / 'checkpoints/last.pt').read_bytes()[:2] == b'PK'
    samples[-1]['group_id'] = 'train'
    manifest.write_text(json.dumps(dict(version='v1', backbone=DINOV3_ID, samples=samples)))
    with pytest.raises(ValueError, match='overlap'):
        feeding(approved_config('feeding_linear', {}), directory, False, metrics.append)


def test_rfdetr_missing_data_fails_early(tmp_path, monkeypatch):
    monkeypatch.setenv('IZGE_DATA_ROOT', str(tmp_path))
    with pytest.raises(ValueError, match='manifest missing'):
        rfdetr(approved_config('rfdetr_detect', {}), tmp_path, False, lambda row: None)
