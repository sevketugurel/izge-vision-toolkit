from pathlib import Path

import pytest
from pydantic import ValidationError

from izge.training.config import ROOT, TrainingConfig, approved_config, data_path, load_manifest


def test_approved_overrides_and_budget():
    assert approved_config('smoke', {'device': 'cpu', 'params.epochs': 2}).params.epochs == 2
    for overrides in ({'command': 'sh'}, {'dataset.manifest': '/etc/passwd'}, {'budget.max_epochs': 999},
                      {'params.epochs': 101}, {'eval.split': 'test'}, {'params.learning_rate': float('nan')}):
        with pytest.raises(ValueError):
            approved_config('smoke', overrides)
    with pytest.raises(ValueError):
        approved_config('../smoke', {})


def test_schema_and_manifest_boundaries(tmp_path, monkeypatch):
    data = approved_config('smoke', {}).model_dump()
    data['dataset']['manifest'] = '../secret'
    with pytest.raises(ValidationError):
        TrainingConfig.model_validate(data)
    monkeypatch.setenv('IZGE_DATA_ROOT', str(tmp_path))
    (tmp_path / 'escape').symlink_to('/tmp')
    with pytest.raises(ValueError):
        data_path('escape/secret')
    with pytest.raises(ValueError, match='missing'):
        load_manifest(approved_config('feeding_linear', {}))
    assert ROOT == Path(__file__).resolve().parents[2]
