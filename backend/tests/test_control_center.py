"""Bridge behavior tests; generated fixtures are not field data or model scores."""

import copy
import json
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient
import pytest

from izge import control_center as cc
from izge.training.config import approved_config

FIRST, SECOND = "a" * 32, "b" * 32


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("IZGE_CONTROL_CENTER_ENABLED", "1")
    monkeypatch.setenv("IZGE_RUNNER_URL", "http://127.0.0.1:8765")
    from izge.api import app

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 10000)) as client:
        yield client


def row(job_id=FIRST, **changes):
    return dict(
        id=job_id,
        status="completed",
        config=approved_config("smoke", {}).model_dump(),
        created=123.0,
        resume_available=False,
        **changes,
    )


def test_local_access_disabled_remote_origin_and_host(bridge, monkeypatch):
    monkeypatch.delenv("IZGE_CONTROL_CENTER_ENABLED")
    assert bridge.get("/api/control/runner").status_code == 503
    monkeypatch.setenv("IZGE_CONTROL_CENTER_ENABLED", "1")
    assert bridge.get("/api/control/datasets", headers={"Origin": "https://evil.test"}).status_code == 403
    assert bridge.get("/api/control/datasets", headers={"Host": "evil.test"}).status_code == 403
    assert (
        bridge.post(
            "/api/control/jobs", json={"config_name": "smoke"}, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    with TestClient(bridge.app, base_url="http://127.0.0.1", client=("10.0.0.5", 1)) as remote:
        assert remote.get("/api/control/datasets").status_code == 403
    assert bridge.get("/api/control/datasets", headers={"Origin": "http://127.0.0.1"}).status_code == 200


@pytest.mark.parametrize(
    ("exc", "state"),
    [
        (HTTPError("secret-path", 401, "TOKEN", {}, None), "auth_error"),
        (URLError("/private/token"), "unavailable"),
        (FileNotFoundError("/private/token"), "unavailable"),
    ],
)
def test_safe_runner_errors(bridge, monkeypatch, exc, state):
    def fail(*args):
        raise exc

    monkeypatch.setattr(cc.client, "request", fail)
    response = bridge.get("/api/control/runner")
    assert response.json()["status"] == state
    assert (
        "/private" not in response.text
        and "TOKEN" not in response.text
        and "secret-path" not in response.text
    )


def test_external_runner_url_rejected_before_reading_credentials(bridge, monkeypatch):
    monkeypatch.setenv("IZGE_RUNNER_URL", "http://public.example:8765")
    monkeypatch.setattr(cc.client, "request", lambda *a: pytest.fail("external host contacted"))
    assert bridge.get("/api/control/jobs").status_code == 503


def test_approved_only_no_commands_paths_or_overrides(bridge, monkeypatch):
    calls = []

    def request(method, path, data=None):
        calls.append((method, path, data))
        return row()

    monkeypatch.setattr(cc.client, "request", request)
    for data in [
        {"config_name": "../smoke"},
        {"config_name": "smoke", "command": "sh"},
        {"config_name": "smoke", "token": "sensitive-invalid-input"},
        {"config_name": "smoke", "overrides": {"dataset.manifest": "/secret"}},
    ]:
        response = bridge.post("/api/control/jobs", json=data)
        assert response.status_code == 422
        assert response.json() == {"detail": "Kontrol merkezi isteği geçersiz"}
    assert not calls
    result = bridge.post("/api/control/jobs", json={"config_name": "smoke"})
    assert result.status_code == 201
    assert calls == [("POST", "/jobs", {"config_name": "smoke"})]
    assert "manifest" not in result.text and "generated/smoke" not in result.text
    assert result.json()["config"]["infrastructure_smoke"]
    assert result.json()["config"]["origin"] == "unavailable"
    for route in ["cancel", "resume"]:
        assert bridge.post(f"/api/control/jobs/{FIRST}/{route}").status_code == 200
        assert calls[-1][:2] == ("POST", f"/jobs/{FIRST}/{route}")
    assert bridge.post("/api/control/jobs/../cancel").status_code != 200


def test_logs_metrics_allowlist_and_empty_data(bridge, monkeypatch):
    def request(method, path, data=None):
        if "/metrics" in path:
            return [
                dict(epoch=1, val_loss=0.4, test_loss=0.1, token="secret", path="/private", f1=float("nan"))
            ]
        if "/logs" in path:
            return dict(lines=['{"epoch":1,"val_loss":0.4,"token":"secret"}', "Error: /private/token secret"])
        return row()

    monkeypatch.setattr(cc.client, "request", request)
    response = bridge.get(f"/api/control/jobs/{FIRST}")
    assert response.json()["metrics"] == [{"epoch": 1, "val_loss": 0.4}]
    assert (
        "secret" not in response.text and "/private" not in response.text and "test_loss" not in response.text
    )
    monkeypatch.setattr(cc.client, "request", lambda *a: [])
    assert bridge.get("/api/control/jobs").json() == []


def test_comparison_rejects_test_incomplete_and_different_dataset(bridge, monkeypatch):
    rows = {FIRST: row(), SECOND: row(SECOND)}

    def request(method, path, data=None):
        if path.endswith("/metrics"):
            return [dict(epoch=1, val_loss=0.3, test_loss=0.01, train_loss=0.4)]
        return rows[path.split("/")[2]]

    monkeypatch.setattr(cc.client, "request", request)
    url = f"/api/control/comparison?left={FIRST}&right={SECOND}"
    response = bridge.get(url)
    assert response.status_code == 200 and response.json()["split"] == "val"
    assert (
        response.json()["infrastructure_smoke"]
        and "test_loss" not in response.text
        and "train_loss" not in response.text
    )
    for status in ["cancelled", "interrupted", "running", "failed"]:
        rows[SECOND]["status"] = status
        assert bridge.get(url).status_code == 422
    rows[SECOND]["status"] = "completed"
    original = copy.deepcopy(rows[SECOND])
    rows[SECOND]["config"]["dataset"]["version"] = "other"
    assert bridge.get(url).status_code == 422
    rows[SECOND] = original
    rows[SECOND]["config"]["eval"]["split"] = "test"
    assert bridge.get(url).status_code == 422
    assert bridge.get(f"/api/control/comparison?left={FIRST}&right={FIRST}").status_code == 422


def test_dataset_real_producer_contract_and_checksum_failure(bridge, monkeypatch, tmp_path):
    from test_datasets import synthetic_fixture
    from izge.datasets.inventory import inventory, save_inventory
    from izge.datasets.manifest import split_inventory, write_manifest

    root = tmp_path / "data"
    metadata = synthetic_fixture(root)
    value = inventory(root, metadata)
    snapshot = tmp_path / "inventory.json"
    save_inventory(snapshot, value)
    manifest = split_inventory(value)
    write_manifest(manifest, "fixture", "v1")
    monkeypatch.setenv("IZGE_DATA_ROOT", str(root))
    monkeypatch.setenv("IZGE_CONTROL_INVENTORY", str(snapshot))
    response = bridge.get("/api/control/datasets")
    data = response.json()
    assert data["status"] == "validated" and data["inventory_counts"] == {"frame": 4, "video": 4}
    assert data["labels_needed_count"] == 24 and data["reviewed_units"] == 0
    assert data["versions"][0]["status"] == "validated_train_val"
    assert data["versions"][0]["splits"] == {"train": 2, "val": 1}
    assert str(root) not in response.text and "frame0.png" not in response.text
    # The validation gate checks train/val integrity, never opens sealed-test media.
    sealed = next(i for i in manifest["items"] if i["split"] == "test")
    (root / sealed["path"]).unlink()
    assert bridge.get("/api/control/datasets").json()["versions"][0]["status"] == "validated_train_val"
    train = next(i for i in manifest["items"] if i["split"] == "train")
    (root / train["path"]).write_bytes(b"changed")
    data = bridge.get("/api/control/datasets").json()
    assert data["status"] == "invalid" and data["labels_needed_count"] is None
    assert data["versions"][0]["status"] == "invalid"


def test_dataset_missing_invalid_and_external_symlink(bridge, monkeypatch, tmp_path):
    monkeypatch.delenv("IZGE_CONTROL_INVENTORY", raising=False)
    assert bridge.get("/api/control/datasets").json()["status"] == "unavailable"
    monkeypatch.setenv("IZGE_DATA_ROOT", str(tmp_path))
    snapshot = tmp_path / "inventory.json"
    snapshot.write_text("invalid")
    monkeypatch.setenv("IZGE_CONTROL_INVENTORY", str(snapshot))
    assert bridge.get("/api/control/datasets").json()["status"] == "invalid"
    snapshot.write_text(
        json.dumps(dict(schema_version="izge-inventory-1", items=[], annotations=[], errors=[]))
    )
    external = tmp_path.parent / "external-control.json"
    external.write_text('{"secret":"not-to-be-read"}')
    directory = tmp_path / "manifests/x/v1"
    directory.mkdir(parents=True)
    (directory / "manifest.json").symlink_to(external)
    assert bridge.get("/api/control/datasets").json()["versions"] == []
