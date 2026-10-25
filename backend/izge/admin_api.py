"""Local admin routes extending the existing application and control center."""

import json
import os
import sqlite3
from typing import Literal
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import admin_artifacts, admin_overlay, admin_swarm
from .control_center import local_operator

router = APIRouter(prefix="/api/admin", dependencies=[Depends(local_operator)])
PREFIX = "izge:admin:planning:"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskCreate(Input):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(min_length=1, max_length=6000)
    priority: int = Field(default=50, ge=0, le=100)
    agent_id: uuid.UUID | None = None
    dependencies: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class TitleEdit(Input):
    title: str = Field(min_length=1, max_length=120)


class Planning(Input):
    column: Literal["Backlog", "To Do", "In Progress", "Review", "Blocked", "Done"]
    priority: int = Field(ge=0, le=100)
    assignee: uuid.UUID | None = None
    description: str = Field(max_length=12000)
    acceptance: str = Field(max_length=6000)
    blockers: str = Field(max_length=3000)
    dependencies: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    branch: str = Field(default="", max_length=160, pattern=r"^[\w./-]*$")
    commit: str = Field(default="", max_length=64, pattern=r"^[a-fA-F0-9]*$")
    pr_url: str = Field(
        default="", max_length=300, pattern=r"^(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)?$"
    )


class Comment(Input):
    text: str = Field(min_length=1, max_length=6000)


def planning_enabled():
    return os.getenv("IZGE_ADMIN_PLANNING_ENABLED") == "1"


def audit(task_id, kind, body):
    try:
        admin_overlay.append(str(task_id), kind, body)
        return "saved"
    except (OSError, sqlite3.Error):
        # An upstream success must not look like a failed create and invite duplicate submissions.
        return "unavailable"


def planning_path(task_id):
    return "/api/kv/" + quote(PREFIX + str(task_id), safe="")


def planning_values():
    if not planning_enabled():
        return dict(status="unavailable", values={})
    value = admin_swarm.request("GET", "/api/kv?prefix=" + quote(PREFIX, safe="") + "&limit=1000")
    result = {}
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise HTTPException(502, "Swarm planlama yanıtı geçersiz")
    for entry in value["entries"]:
        try:
            task_id = str(uuid.UUID(entry["key"].removeprefix(PREFIX)))
            raw = entry.get("value")
            raw = json.loads(raw) if isinstance(raw, str) else raw
            result[task_id] = admin_swarm.clean(Planning.model_validate(raw).model_dump(mode="json"))
        except (ValueError, TypeError, KeyError):
            continue
    return dict(status="available", values=result, truncated=value.get("total", 0) > 1000)


@router.get("/experiments")
def experiments():
    return admin_artifacts.experiments()


@router.get("/experiments/{artifact_id}")
def artifact(artifact_id: str):
    for row in admin_artifacts.experiments()["records"]:
        if row["id"] == artifact_id:
            return row
    raise HTTPException(404, "Deney kaydı bulunamadı")


@router.get("/tasks")
def tasks(offset: int = Query(0, ge=0, le=100000)):
    value = admin_swarm.tasks(offset)
    try:
        value["planning"] = planning_values()
    except HTTPException:
        value["planning"] = dict(status="unavailable", values={})
    value["planning_enabled"] = planning_enabled()
    return value


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate):
    payload = dict(
        task=f"IZGE: {body.title}\n\n{body.description}\n\nAcceptance criteria:\n{body.acceptance}",
        priority=body.priority,
        dependsOn=[str(v) for v in body.dependencies],
        source="ui",
        key="shared/izge/",
        tags=["izge", "admin-panel"],
        draft=True,
    )
    # Draft creation cannot dispatch an agent accidentally. Assignment is a creation-time native field.
    if body.agent_id:
        payload.update(
            agentId=str(body.agent_id),
            routingReason="human_pinned",
            routingNote="Local Izge admin operator selected this agent for a draft task",
        )
    row = admin_swarm.public_task(admin_swarm.request("POST", "/api/tasks", payload))
    row["audit_status"] = audit(row["id"], "task_created", {"source": "swarm", "draft": True})
    return row


@router.get("/tasks/{task_id}")
def task(task_id: uuid.UUID):
    row = admin_swarm.task(str(task_id))
    try:
        row["overlay"] = admin_overlay.entries(str(task_id))
        row["overlay_status"] = "available"
    except (OSError, sqlite3.Error):
        row.update(overlay=[], overlay_status="unavailable")
    return row


@router.patch("/tasks/{task_id}/title")
def edit_title(task_id: uuid.UUID, body: TitleEdit):
    result = admin_swarm.request("PATCH", f"/api/tasks/{task_id}/title", body.model_dump())
    row = admin_swarm.public_task(result["task"])
    row["audit_status"] = audit(task_id, "title_changed", {"title": body.title})
    return row


@router.put("/tasks/{task_id}/planning")
def edit_planning(task_id: uuid.UUID, body: Planning):
    if not planning_enabled():
        raise HTTPException(503, "Swarm KV planlama eki etkinleştirilmemiş")
    admin_swarm.task(str(task_id))  # No orphan planning records, and preserve upstream read permissions.
    if task_id in body.dependencies:
        raise HTTPException(422, "Görev kendisine bağımlı olamaz")
    payload = admin_swarm.clean(body.model_dump(mode="json"))
    admin_swarm.request("PUT", planning_path(task_id), {"value": payload, "valueType": "json"})
    return dict(status="saved", planning=payload, audit_status=audit(task_id, "planning_saved", payload))


@router.post("/tasks/{task_id}/comments", status_code=201)
def comment(task_id: uuid.UUID, body: Comment):
    admin_swarm.task(str(task_id))
    try:
        return admin_overlay.append(str(task_id), "comment", {"text": body.text})
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "Yerel yorum kaydı kullanılamıyor") from None


@router.get("/operations")
def operations():
    return admin_swarm.operations()


@router.get("/operations/workflows/{workflow_id}/runs")
def workflow_history(workflow_id: uuid.UUID):
    return admin_swarm.operation_resource(
        f"/api/workflows/{workflow_id}/runs?limit=30",
        "runs",
        {"id", "status", "startedAt", "finishedAt", "createdAt"},
    )


@router.get("/operations/sessions")
def session_history():
    try:
        value = admin_swarm.request("GET", "/api/sessions?limit=30&q=izge")
        rows = []
        for row in value["sessions"]:
            root = row["root"]
            rows.append({k: root.get(k) for k in ("id", "title", "status", "createdAt", "lastUpdatedAt")})
        return dict(status="available", rows=admin_swarm.clean(rows))
    except (HTTPException, KeyError, TypeError):
        return dict(status="unavailable", rows=[])
