"""Bounded Agent Swarm REST adapter. Credentials and upstream error bodies stay here."""

import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from .training.client import NoRedirect

TASK_FIELDS = {
    "id",
    "title",
    "task",
    "taskPreview",
    "status",
    "agentId",
    "priority",
    "dependsOn",
    "createdAt",
    "lastUpdatedAt",
    "finishedAt",
    "progress",
    "failureReason",
    "provider",
    "model",
    "parentTaskId",
    "totalCostUsd",
    "vcsRepo",
    "vcsNumber",
    "vcsUrl",
    "workflowRunId",
}


def token():
    value = os.getenv("IZGE_SWARM_TOKEN", "")
    if not value and os.getenv("IZGE_SWARM_TOKEN_FILE"):
        value = Path(os.environ["IZGE_SWARM_TOKEN_FILE"]).read_text().strip()
    return value


def clean(value):
    """Project only selected fields before this redaction; never proxy arbitrary blobs."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value[:1000]]
    if isinstance(value, str):
        try:
            secret = token()
        except OSError:
            secret = ""
        if secret:
            value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)(bearer\s+|(?:token|password|api_key|secret)\s*[=:]\s*)\S+", "[redacted]", value)
        value = re.sub(r"(?i)rtsp[s]?://\S+", "[redacted]", value)
        return value[:12000]
    return value


def request(method, path, data=None):
    try:
        url = urlsplit(os.getenv("IZGE_SWARM_URL", "http://localhost:3013"))
        internal = url.hostname == "localhost"
        try:
            address = ipaddress.ip_address(url.hostname or "")
            internal |= address.is_loopback or address.is_private
        except ValueError:
            pass
        if url.scheme != "http" or not internal or url.username or url.password or url.path not in {"", "/"}:
            raise HTTPException(503, "Swarm için yerel veya iç ağ adresi gerekli")
        if url.query or url.fragment:
            raise HTTPException(503, "Swarm adresi geçersiz")
        secret = token()
        if not secret:
            raise HTTPException(503, "Swarm sunucu kimlik bilgisi tanımlanmamış")
        headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
        if os.getenv("IZGE_SWARM_AGENT_ID"):
            headers["X-Agent-ID"] = os.environ["IZGE_SWARM_AGENT_ID"]
        req = Request(
            os.getenv("IZGE_SWARM_URL", "http://localhost:3013").rstrip("/") + path,
            method=method,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        with build_opener(NoRedirect).open(req, timeout=4) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Response limit")
            return json.loads(raw)
    except HTTPError as exc:
        code = exc.code if exc.code in {403, 404, 409, 422} else 502
        raise HTTPException(code, "Swarm isteği reddedildi; yetkiyi ve kayıt durumunu kontrol edin") from None
    except (OSError, URLError, ValueError, TypeError):
        raise HTTPException(503, "Swarm bağlantısı veya yanıtı kullanılamıyor") from None


def public_task(row):
    if not isinstance(row, dict) or not row.get("id"):
        raise HTTPException(502, "Swarm görev yanıtı geçersiz")
    return clean({k: v for k, v in row.items() if k in TASK_FIELDS})


def tasks(offset=0):
    value = request("GET", f"/api/tasks?limit=100&offset={offset}&includeTotal=true&fields=full&search=izge")
    try:
        return dict(
            status="available",
            tasks=[public_task(row) for row in value["tasks"]],
            total=value.get("total"),
            offset=offset,
            limit=100,
        )
    except (KeyError, TypeError):
        raise HTTPException(502, "Swarm görev listesi geçersiz") from None


def task(task_id):
    row = request("GET", f"/api/tasks/{task_id}?logsLimit=100")
    result = public_task(row)
    result["history"] = clean(
        [
            {k: r.get(k) for k in ("id", "eventType", "createdAt", "agentId")}
            for r in row.get("logs", [])
            if isinstance(r, dict)
        ]
    )
    return result


def operation_resource(path, key, fields):
    try:
        value = request("GET", path)
        rows = value[key] if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise ValueError("Shape")
        return dict(
            status="available",
            rows=clean([{k: r.get(k) for k in fields} for r in rows if isinstance(r, dict)]),
        )
    except (HTTPException, ValueError, TypeError, KeyError):
        return dict(status="unavailable", rows=[], message="Kaynak bağlı değil veya erişim yetkisi yok")


def operations():
    # Never request credential values, prompts, full session logs or arbitrary event payloads.
    sources = {
        "agents": (
            "/api/agents",
            "agents",
            {"id", "name", "status", "provider", "harnessProvider", "lastActivityAt"},
        ),
        "providers": (
            "/api/agents/credential-status",
            "agents",
            {"agentId", "name", "provider", "status", "lastCheckedAt"},
        ),
        "approvals": (
            "/api/approval-requests?status=pending",
            "approvalRequests",
            {"id", "title", "status", "createdAt"},
        ),
        "sessions": (
            "/api/active-sessions",
            "sessions",
            {"id", "taskId", "agentId", "startedAt", "lastHeartbeatAt"},
        ),
        "workflows": ("/api/workflows", "workflows", {"id", "name", "enabled", "lastUpdatedAt"}),
        "actions": ("/api/logs?limit=50", "logs", {"id", "taskId", "agentId", "eventType", "createdAt"}),
    }
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {key: pool.submit(operation_resource, *args) for key, args in sources.items()}
        return {key: future.result() for key, future in futures.items()}
