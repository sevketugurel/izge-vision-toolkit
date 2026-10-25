"""Validated, operator-owned recipes. API callers cannot select paths or commands."""
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]


class TrainingPrerequisiteError(ValueError):
    """Safe static prerequisite messages; never wrap provider exceptions."""



class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Dataset(StrictModel):
    manifest: str
    version: str = Field(min_length=1)

    @model_validator(mode='after')
    def relative_manifest(self):
        path = Path(self.manifest)
        if path.is_absolute() or '..' in path.parts or not self.manifest:
            raise ValueError('manifest must be relative to IZGE_DATA_ROOT')
        return self


class Budget(StrictModel):
    max_wall_minutes: float = Field(gt=0, le=10080, allow_inf_nan=False)
    max_epochs: int = Field(ge=1, le=10000)


class Evaluation(StrictModel):
    split: Literal['val'] = 'val'


class Params(StrictModel):
    epochs: int = Field(default=3, ge=1, le=10000)
    learning_rate: float = Field(default=0.1, gt=0, le=1, allow_inf_nan=False)
    c: float = Field(default=1, gt=0, le=100, allow_inf_nan=False)
    batch_size: int = Field(default=1, ge=1, le=64)
    grad_accum_steps: int = Field(default=4, ge=1, le=64)
    weights: str | None = None


class TrainingConfig(StrictModel):
    name: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    kind: Literal['smoke', 'feeding_linear', 'rfdetr_detect']
    dataset: Dataset
    params: Params = Field(default_factory=Params)
    budget: Budget
    device: Literal['auto', 'mps', 'cuda', 'cpu'] = 'auto'
    seed: int = Field(default=42, ge=0, le=2**31-1)
    eval: Evaluation = Field(default_factory=Evaluation)

    @model_validator(mode='after')
    def check_budget(self):
        if self.params.epochs > self.budget.max_epochs:
            raise ValueError('epochs exceed approved budget')
        if self.kind == 'feeding_linear' and self.params.epochs != 1:
            raise ValueError('feeding_linear uses one atomic sklearn fit; epochs must be 1')
        return self


def load_config(path: Path) -> TrainingConfig:
    return TrainingConfig.model_validate(yaml.safe_load(path.read_text()))


def approved_config(name: str, overrides: dict, registry: Path | None = None) -> TrainingConfig:
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name):
        raise ValueError('invalid config name')
    registry = registry or Path(os.getenv('IZGE_TRAINING_CONFIG_ROOT', ROOT / 'config/training/approved'))
    config = load_config(registry / f'{name}.yaml')
    if config.name != name:
        raise ValueError('config name mismatch')
    allowed = {'params.epochs', 'params.learning_rate', 'params.c', 'params.batch_size',
               'params.grad_accum_steps', 'seed', 'device'}
    data = config.model_dump()
    for key, value in overrides.items():
        if key not in allowed:
            raise ValueError(f'override not allowed: {key}')
        if '.' in key:
            section, field = key.split('.')
            data[section][field] = value
        else:
            data[key] = value
    return TrainingConfig.model_validate(data)


def data_path(relative: str) -> Path:
    root = Path(os.environ.get('IZGE_DATA_ROOT', 'data')).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('dataset path escapes IZGE_DATA_ROOT')
    return path


def load_manifest(config: TrainingConfig) -> dict:
    """Temporary consumer contract; dataset producer owns generation and validation."""
    path = data_path(config.dataset.manifest)
    if not path.is_file():
        raise TrainingPrerequisiteError('dataset manifest missing under IZGE_DATA_ROOT')
    manifest = json.loads(path.read_text())
    if manifest.get('version') != config.dataset.version:
        raise ValueError('dataset version mismatch')
    return manifest
