import json

import pytest

from izge.training.config import approved_config
from izge.training import run

torch = pytest.importorskip("torch")
pytest.importorskip("mlflow")
pytest.importorskip("psutil")



def test_real_checkpoint_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('IZGE_RUNS_ROOT', str(tmp_path))
    config = tmp_path / 'config.yaml'
    config.write_text(approved_config('smoke', {'device': 'cpu'}).model_dump_json())
    directory = tmp_path / 'job'
    original = run.smoke
    def interrupted(config, run_dir, device, resume, emit):
        def emit_once(row):
            emit(row)
            raise RuntimeError('injected interruption after committed epoch')
        original(config, run_dir, device, resume, emit_once)
    monkeypatch.setattr(run, 'smoke', interrupted)
    assert run.execute(config, directory) == 1
    status = json.loads((directory / 'status.json').read_text())
    checkpoint = torch.load(directory / 'checkpoints/last.pt', weights_only=True)
    assert checkpoint['epoch'] == 1
    assert 'optimizer' in checkpoint
    monkeypatch.setattr(run, 'smoke', original)
    assert run.execute(config, directory, resume=True) == 0
    resumed = json.loads((directory / 'status.json').read_text())
    assert resumed['mlflow_run_id'] == status['mlflow_run_id']
    assert [json.loads(line)['epoch'] for line in (directory / 'metrics.jsonl').read_text().splitlines()] == [1, 2, 3]
    assert run.execute(config, directory) == 1
    assert json.loads((directory / 'status.json').read_text()) == resumed


def test_device_preference(monkeypatch):
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: True)
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: True)
    assert run.pick_device() == 'mps'
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    assert run.pick_device() == 'cuda'
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    assert run.pick_device() == 'cpu'
    with pytest.raises(ValueError, match='unavailable'):
        run.pick_device('mps')
