import json

import pytest

from izge import admin_artifacts as artifacts


@pytest.fixture
def sources(tmp_path, monkeypatch):
    root, runs = tmp_path / "experiments", tmp_path / "runs"
    root.mkdir()
    runs.mkdir()
    monkeypatch.setenv("IZGE_ADMIN_EXPERIMENTS_ROOT", str(root))
    monkeypatch.setenv("IZGE_RUNS_ROOT", str(runs))
    return root, runs


def test_real_recipe_is_not_completed_and_missing_metrics_are_not_invented(sources):
    root, runs = sources
    (root / "rfdetr").mkdir()
    (root / "rfdetr/model.lock.json").write_text(
        json.dumps({"model_class": "RFDETRSegSmall", "package_version": "1.11.2"})
    )
    run = runs / ("a" * 32)
    run.mkdir()
    (run / "status.json").write_text('{"status":"completed", "infrastructure_smoke":true}')
    (run / "config.yaml").write_text('{"name":"smoke", "kind":"smoke", "eval":{"split":"val"}}')
    (run / "metrics.jsonl").write_text(
        '{"epoch":1,"train_loss":0.5,"precision":0.6,"token":"SECRET"}\n'
        '{"epoch":2,"val_loss":NaN,"recall":0.7}\n{"epoch":'
    )
    rows = artifacts.experiments()["records"]
    assert rows[0]["status"] == "unavailable" and rows[0]["metrics"] == []
    assert rows[1]["metrics"] == [
        {"epoch": 1, "train_loss": 0.5, "precision": 0.6},
        {"epoch": 2, "recall": 0.7},
    ]
    assert rows[1]["infrastructure_smoke"] is True
    assert "SECRET" not in json.dumps(rows) and str(runs) not in json.dumps(rows)
    assert "map" not in rows[1]["metrics"][0]
    (run / "config.yaml").write_text('{"eval":{"split":"test"}}')
    assert artifacts.experiments()["records"][1]["metrics"] == []


def test_symlink_escape_oversized_corrupt_and_empty_sources(sources, tmp_path):
    root, runs = sources
    assert artifacts.experiments()["status"] == "unavailable"
    run = runs / "bad"
    run.mkdir()
    outside = tmp_path / "private.json"
    outside.write_text('{"status":"completed", "secret":"PRIVATE"}')
    (run / "status.json").symlink_to(outside)
    assert artifacts.experiments()["unreadable_count"] == 1
    (run / "status.json").unlink()
    (run / "status.json").write_text("x" * 2_000_001)
    assert artifacts.experiments()["records"] == []
    (run / "status.json").write_text("{")
    assert artifacts.experiments()["unreadable_count"] == 1
