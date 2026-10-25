"""Read bounded, allowlisted experiment metadata. Never serve raw files or weights."""

import hashlib
import json
import math
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
METRICS = {"epoch", "train_loss", "val_loss", "precision", "recall", "f1", "map", "map_50"}


def text(value):
    value = str(value or "unavailable")[:300]
    return value if re.fullmatch(r"[\w .:@+()\-/]+", value) and not value.startswith("/") else "unavailable"


def read_json(path, root):
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 2_000_000:
        raise ValueError("Artifact boundary")
    return json.loads(path.read_text())


def metric_rows(path, root):
    if not path.exists():
        return []
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 2_000_000:
        raise ValueError("Metric boundary")
    rows = []
    for line in path.read_text().splitlines()[:10000]:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(
                    {
                        k: v
                        for k, v in row.items()
                        if k in METRICS and type(v) in (int, float) and math.isfinite(v)
                    }
                )
        except ValueError:
            continue  # A partially written final record is not a measurement.
    return [row for row in rows if row]


def experiments():
    records, errors = [], 0
    root = Path(os.getenv("IZGE_ADMIN_EXPERIMENTS_ROOT", ROOT / "experiments")).resolve()
    # Repository model locks are recipes, never completed experiments.
    for path in sorted(root.glob("*/*.model.lock.json")) + sorted(root.glob("*/model.lock.json")):
        try:
            value = read_json(path, root)
            records.append(
                dict(
                    id=hashlib.sha256(str(path).encode()).hexdigest()[:24],
                    name=text(value.get("model_class")),
                    version=text(value.get("package_version")),
                    config=text(path.name),
                    status="unavailable",
                    source="experiments",
                    origin="unavailable",
                    metrics=[],
                    infrastructure_smoke=False,
                    note="Model tarifi; yerel eğitim sonucu bulunmuyor",
                )
            )
        except (OSError, ValueError, TypeError, AttributeError):
            errors += 1
    runs = Path(os.getenv("IZGE_RUNS_ROOT", Path.home() / ".izge-runner/runs")).resolve()
    for path in sorted(runs.glob("*/status.json"))[:200]:
        try:
            value = read_json(path, runs)
            config_path = path.parent / "config.yaml"  # Runner persists JSON under this fixed name.
            config = read_json(config_path, runs) if config_path.exists() else {}
            # No iterative comparison of sealed/final test results, even from imported output.
            metrics = (
                metric_rows(path.parent / "metrics.jsonl", runs)
                if config.get("eval", {}).get("split") == "val"
                else []
            )
            run_id = hashlib.sha256(str(path).encode()).hexdigest()[:24]
            state = value.get("status")
            records.append(
                dict(
                    id=run_id,
                    name=text(config.get("name", path.parent.name)),
                    version=text(value.get("config_sha256")),
                    config=text(config.get("kind")),
                    status=state
                    if state
                    in {"queued", "running", "completed", "failed", "cancelled", "interrupted", "timed_out"}
                    else "unavailable",
                    origin="unavailable",
                    source="training_output",
                    metrics=metrics,
                    infrastructure_smoke=value.get("infrastructure_smoke") is True,
                    error_type=text(value.get("error_type")),
                    artifact_url=f"/api/admin/experiments/{run_id}",
                )
            )
        except (OSError, ValueError, TypeError, AttributeError):
            errors += 1
    return dict(
        status="available" if records else "unavailable",
        records=records,
        unreadable_count=errors,
        note="Yalnız yerel model kilitleri ve runner status.json / metrics.jsonl. Mühürlü test okunmaz.",
    )
