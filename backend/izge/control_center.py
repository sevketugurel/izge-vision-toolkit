"""Local operator bridge. Runner credentials and dataset paths stay server-side."""

from collections import Counter
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .datasets.inventory import read_inventory
from .datasets.manifest import load_manifest
from .datasets.validation import labels_needed, validate
from .training import client

JOB_ID = r"^[a-f0-9]{32}$"
METRICS = {"epoch", "train_loss", "val_loss", "precision", "recall", "f1", "duration_s"}
STATES = {"queued", "running", "completed", "failed", "cancelled", "interrupted", "timed_out"}


def local_operator(request: Request):
    # This application has no remote user authentication. Fail closed outside loopback.
    if os.getenv("IZGE_CONTROL_CENTER_ENABLED") != "1":
        raise HTTPException(503, "Kontrol merkezi sunucuda etkinleştirilmemiş")
    host = request.url.hostname
    if (
        not request.client
        or request.client.host not in {"127.0.0.1", "::1"}
        or host not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise HTTPException(403, "Kontrol merkezi yalnız yerel operatöre açıktır")
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "Aynı kaynak gerekli")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Aynı kaynak gerekli")


router = APIRouter(prefix="/api/control", dependencies=[Depends(local_operator)])


def runner_request(method, path, data=None):
    url = urlsplit(os.getenv("IZGE_RUNNER_URL", "http://host.docker.internal:8765"))
    # Operator-selected internal hosts only; no browser-supplied URL or credential forwarding.
    internal = url.hostname in {"localhost", "host.docker.internal"}
    try:
        address = ipaddress.ip_address(url.hostname or "")
        internal |= address.is_loopback or address.is_private
    except ValueError:
        pass
    if (
        url.scheme != "http"
        or not internal
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise HTTPException(503, "Runner için geçerli iç ağ adresi gerekli")
    try:
        return client.request(method, path, data)
    except HTTPError as exc:
        code, message = {
            401: (502, "Runner kimlik doğrulaması başarısız"),
            403: (502, "Runner erişimi reddetti"),
            404: (404, "Runner kaydı bulunamadı"),
            409: (409, "Runner işlemi bu durumda kabul etmiyor"),
            422: (422, "Runner onaylı tarifi kabul etmedi"),
        }.get(exc.code, (502, "Runner isteği başarısız"))
        raise HTTPException(code, message) from None
    except (OSError, URLError, KeyError, ValueError):
        raise HTTPException(503, "Runner bağlantısı veya sunucu kimlik bilgisi kullanılamıyor") from None


def numeric_metrics(value):
    if not isinstance(value, list):
        raise HTTPException(502, "Runner metrik yanıtı geçersiz")
    return [
        {k: v for k, v in row.items() if k in METRICS and type(v) in (int, float) and math.isfinite(v)}
        for row in value
        if isinstance(row, dict)
    ]


def public_config(config):
    return dict(
        name=config["name"],
        kind=config["kind"],
        dataset_version=config["dataset"]["version"],
        budget=config["budget"],
        epochs=config["params"]["epochs"],
        device=config["device"],
        evaluation_split=config["eval"]["split"],
        infrastructure_smoke=config["kind"] == "smoke",
        origin="unavailable",
        prerequisites="generated" if config["kind"] == "smoke" else "operator_data_required",
    )


def public_job(row):
    config = row["config"]
    # Compare only identical recipes/datasets, allowing a different random seed.
    comparable = {k: v for k, v in config.items() if k != "seed"}
    return dict(
        id=row["id"],
        status=row["status"] if row["status"] in STATES else "unavailable",
        created=row["created"],
        config=public_config(config),
        comparison_group=hashlib.sha256(json.dumps(comparable, sort_keys=True).encode()).hexdigest(),
        resume_available=bool(row.get("resume_available")),
    )


def valid_id(job_id):
    if not re.fullmatch(JOB_ID, job_id):
        raise HTTPException(422, "Geçersiz iş kimliği")
    return job_id


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")


@router.get("/runner")
def runner_status():
    try:
        health = runner_request("GET", "/health")
        return dict(
            status="connected",
            origin="unavailable",
            device=health["device"],
            running_jobs=health["running_jobs"],
            free_disk_bytes=health["free_disk_bytes"],
            mps_available=health["mps_available"],
        )
    except HTTPException as exc:
        return dict(
            status="auth_error"
            if exc.detail in {"Runner kimlik doğrulaması başarısız", "Runner erişimi reddetti"}
            else "unavailable",
            origin="unavailable",
            message=exc.detail,
        )


@router.get("/configs")
def configs():
    return [public_config(row) for row in runner_request("GET", "/configs")]


@router.get("/jobs")
def jobs():
    return [public_job(row) for row in runner_request("GET", "/jobs")]


@router.post("/jobs", status_code=201)
def submit(body: Submission):
    return public_job(runner_request("POST", "/jobs", body.model_dump()))


@router.get("/jobs/{job_id}")
def job(job_id: str):
    valid_id(job_id)
    row = public_job(runner_request("GET", f"/jobs/{job_id}"))
    row["metrics"] = numeric_metrics(runner_request("GET", f"/jobs/{job_id}/metrics"))
    logs = runner_request("GET", f"/jobs/{job_id}/logs?tail=100")
    lines = []
    for line in logs.get("lines", []):
        try:
            values = numeric_metrics([json.loads(line)])
            lines.append(json.dumps(values[0]) if values and values[0] else "Diğer günlük satırı gizlendi")
        except (ValueError, TypeError):
            # Never export provider exceptions, tokens, paths or arbitrary log strings.
            lines.append("Eğitim hata/önkoşul kaydı; ayrıntı yerel runner günlüğünde")
    row["logs"] = lines
    return row


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    return public_job(runner_request("POST", f"/jobs/{valid_id(job_id)}/cancel"))


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str):
    return public_job(runner_request("POST", f"/jobs/{valid_id(job_id)}/resume"))


@router.get("/comparison")
def comparison(left: str, right: str):
    if left == right:
        raise HTTPException(422, "İki farklı iş seçin")
    rows = [public_job(runner_request("GET", f"/jobs/{valid_id(i)}")) for i in (left, right)]
    if (
        any(row["status"] != "completed" or row["config"]["evaluation_split"] != "val" for row in rows)
        or rows[0]["comparison_group"] != rows[1]["comparison_group"]
    ):
        raise HTTPException(
            422, "Yalnız aynı tarif ve veri sürümündeki tamamlanmış validation işleri karşılaştırılır"
        )
    for row in rows:
        metrics = numeric_metrics(runner_request("GET", f"/jobs/{row['id']}/metrics"))
        row["validation"] = {
            k: v
            for k, v in (metrics[-1] if metrics else {}).items()
            if k in {"epoch", "val_loss", "precision", "recall", "f1"}
        }
    return dict(split="val", infrastructure_smoke=rows[0]["config"]["infrastructure_smoke"], jobs=rows)


@router.get("/datasets")
def datasets():
    """Validate operator-owned artifacts; return aggregates, never media/label paths."""
    root_value = os.getenv("IZGE_DATA_ROOT")
    inventory_path = os.getenv("IZGE_CONTROL_INVENTORY")
    if not root_value or not inventory_path:
        return dict(
            status="unavailable",
            origin="unavailable",
            message="Veri kökü ve envanter sunucuda tanımlanmamış",
            versions=[],
        )
    root = Path(root_value).resolve()
    try:
        value = read_inventory(Path(inventory_path))
        if value.get("schema_version") != "izge-inventory-1":
            raise ValueError("inventory schema")
        # Ignore the serialized machine-specific root. All checks use the operator's current root.
        value["root"] = str(root)
        result = validate(value)
        needed = labels_needed(value) if not result["errors"] else None
        versions = []
        for path in sorted((root / "manifests").glob("*/*/manifest.json")):
            if not path.resolve().is_relative_to(root):
                continue
            record = dict(
                dataset=path.parent.parent.name,
                version=path.parent.name,
                origin="unavailable",
                status="invalid",
                sealed_test=None,
                splits={},
            )
            try:
                # Default producer gate checks train/val, never reads sealed-test media.
                manifest = load_manifest(path, data_root=root)
                record.update(
                    status="validated_train_val",
                    sealed_test=manifest["sealed_test"],
                    splits=manifest["stats"],
                    config_hash=manifest["config_hash"],
                )
            except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
                pass
            versions.append(record)
        return dict(
            status="invalid" if result["errors"] else "validated",
            origin="unavailable",
            inventory_counts=dict(Counter(row["kind"] for row in value["items"])),
            validation_error_count=len(result["errors"]),
            reviewed_units=len(result["covered"]),
            labels_needed_count=len(needed["missing"]) if needed else None,
            label_strata=needed["strata"] if needed else {},
            versions=versions,
            note="Envanter doğrulaması tam video çözümleme veya saat doğrulaması değildir. Envanter tüm yerel dosya checksumlarını, manifest kapısı train/val içeriğini denetler; mühürlü test performansı hesaplanmaz. Manifest eğitim embedding/COCO paketi değildir.",
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return dict(
            status="invalid",
            origin="unavailable",
            message="Envanter okunamadı veya doğrulanamadı",
            versions=[],
        )
