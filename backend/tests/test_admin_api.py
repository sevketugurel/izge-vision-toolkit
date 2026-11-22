from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from izge import admin_api as admin

TASK_ID = "12345678-1234-4234-8234-123456789012"


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    from izge.api import app

    monkeypatch.setenv("IZGE_CONTROL_CENTER_ENABLED", "1")
    monkeypatch.setenv("IZGE_ADMIN_OVERLAY", str(tmp_path / "overlay.sqlite3"))
    monkeypatch.setenv("IZGE_ADMIN_PLANNING_ENABLED", "1")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        yield client


def test_scope_and_disabled_guards_cover_every_admin_route(bridge, monkeypatch):
    assert bridge.get("/api/admin/experiments", headers={"Origin": "https://evil.test"}).status_code == 403
    assert bridge.get("/api/admin/operations", headers={"Host": "evil.test"}).status_code == 403
    monkeypatch.delenv("IZGE_CONTROL_CENTER_ENABLED")
    assert bridge.get("/api/admin/experiments").status_code == 503
    assert bridge.post("/api/admin/tasks", json={}).status_code == 503


def test_create_native_title_comments_and_remote_planning(bridge, monkeypatch):
    calls, kv = [], {}
    row = dict(id=TASK_ID, title="IZGE task", task="IZGE: task", status="draft", priority=70)

    def upstream(method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/api/kv?"):
            return {"entries": [{"key": admin.PREFIX + TASK_ID, "value": kv.get("value")}], "total": 1}
        if path.startswith("/api/kv/"):
            kv.update(body)
            return body
        if method == "PATCH":
            return {"task": {**row, **body}}
        if path.startswith("/api/tasks?"):
            return {"tasks": [row], "total": 1}
        return {**row, "logs": [{"id": "log", "eventType": "created", "metadata": "SECRET"}]}

    monkeypatch.setattr(admin.admin_swarm, "request", upstream)
    response = bridge.post(
        "/api/admin/tasks",
        json={"title": "Test", "description": "Description", "acceptance": "Criteria", "priority": 70},
    )
    assert response.status_code == 201
    sent = calls[0][2]
    assert sent["draft"] is True and sent["key"] == "shared/izge/"
    assert "Acceptance criteria:\nCriteria" in sent["task"]
    assert (
        bridge.patch(f"/api/admin/tasks/{TASK_ID}/title", json={"title": "New title"}).json()["title"]
        == "New title"
    )
    planning = dict(
        column="Review",
        priority=90,
        assignee=None,
        description="amendment",
        acceptance="criteria",
        blockers="review",
        dependencies=[],
        branch="feat/admin",
        commit="abc123",
        pr_url="https://github.com/example/repository/pull/3",
    )
    assert bridge.put(f"/api/admin/tasks/{TASK_ID}/planning", json=planning).status_code == 200
    assert calls[-1][0] == "PUT" and kv["value"]["column"] == "Review"
    board = bridge.get("/api/admin/tasks").json()
    assert board["tasks"][0]["status"] == "draft"  # Planning never changes execution status.
    assert board["planning"]["values"][TASK_ID]["column"] == "Review"
    assert bridge.post(f"/api/admin/tasks/{TASK_ID}/comments", json={"text": "Comment"}).status_code == 201
    detail = bridge.get(f"/api/admin/tasks/{TASK_ID}").json()
    assert len(detail["overlay"]) == 4 and "SECRET" not in str(detail)
    assert detail["history"][0]["eventType"] == "created"
    assert (
        bridge.put(
            f"/api/admin/tasks/{TASK_ID}/planning", json={**planning, "dependencies": [TASK_ID]}
        ).status_code
        == 422
    )
    assert (
        bridge.put(
            f"/api/admin/tasks/{TASK_ID}/planning", json={**planning, "pr_url": "javascript:alert(1)"}
        ).status_code
        == 422
    )
    assert bridge.get("/api/admin/tasks/not-a-uuid").status_code == 422


def test_failed_upstream_never_creates_local_task_or_comment(bridge, monkeypatch):
    def fail(*args):
        raise HTTPException(503, "unavailable")

    monkeypatch.setattr(admin.admin_swarm, "request", fail)
    assert bridge.get("/api/admin/tasks").status_code == 503
    assert (
        bridge.post(f"/api/admin/tasks/{TASK_ID}/comments", json={"text": "Not persisted"}).status_code == 503
    )
    assert admin.admin_overlay.entries(TASK_ID) == []
    assert bridge.get("/api/admin/experiments/unknown").status_code == 404


def test_upstream_success_survives_local_audit_failure(bridge, monkeypatch):
    def fail(*args):
        raise OSError("PRIVATE/path TOKEN")

    monkeypatch.setattr(admin.admin_overlay, "append", fail)
    monkeypatch.setattr(admin.admin_swarm, "request", lambda *args: {"id": TASK_ID, "status": "draft"})
    response = bridge.post("/api/admin/tasks", json={"title": "QA", "description": "QA", "acceptance": "QA"})
    assert response.status_code == 201 and response.json()["audit_status"] == "unavailable"
    assert "PRIVATE" not in response.text
    response = bridge.post(f"/api/admin/tasks/{TASK_ID}/comments", json={"text": "QA"})
    assert response.status_code == 503 and "PRIVATE" not in response.text


def test_histories_are_projected_and_malformed_planning_is_unavailable(bridge, monkeypatch):
    def upstream(method, path, body=None):
        if path.startswith("/api/sessions"):
            return {"sessions": [{"root": {"id": TASK_ID, "status": "completed", "task": "PRIVATE"}}]}
        if "/runs" in path:
            return {"runs": [{"id": TASK_ID, "status": "completed", "context": "PRIVATE"}]}
        if path.startswith("/api/tasks?"):
            return {"tasks": [], "total": 0}
        return {"wrong_shape": []}

    monkeypatch.setattr(admin.admin_swarm, "request", upstream)
    assert "PRIVATE" not in bridge.get("/api/admin/operations/sessions").text
    assert "PRIVATE" not in bridge.get(f"/api/admin/operations/workflows/{TASK_ID}/runs").text
    assert bridge.get("/api/admin/tasks").json()["planning"]["status"] == "unavailable"
