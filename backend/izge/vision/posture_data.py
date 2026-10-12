"""Human crop posture dataset, frozen-feature integrity and independent-source training gates."""

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from .annotation_data import leakage_components, safe_file, validate_splits
from .artifact_io import checksum, prepare_output, write_json
from .low_label import learning_sources, require_learning_source
from .low_label_features import feature_key
from .proposal_review import proposals, validate_revision

POSTURES = ("standing", "lying")
DINO_REPO = "facebook/dinov3-vitl16-pretrain-lvd1689m"
DINO_REVISION = "ea8dc2863c51be0a264bab82070e3e8836b02d51"
DINO_WEIGHT = "dcb2e45127cccbf1601e5f42fef165eea275c8e5213197e8dcf3f48822718179"


def validate_backbone(model: dict):
    if (
        model.get("repo_id") != DINO_REPO
        or model.get("revision") != DINO_REVISION
        or model.get("weights_sha256") != {"model.safetensors": DINO_WEIGHT}
    ):
        raise ValueError("Pinned real DINOv3-L feature model/revision/weight mismatch")


def load_review_store(package: Path, store: Path | None) -> tuple[dict | None, list[dict]]:
    if store is None or not store.exists():
        return None, []
    previous, proof = None, []
    for i, path in enumerate(sorted(store.glob("revision_*.json")), 1):
        if path.name != f"revision_{i:04d}.json":
            raise ValueError("Contiguous review store revisions required")
        data = json.loads(path.read_text())
        validate_revision(package, data)
        parent = proof[-1]["sha256"] if proof else None
        if data.get("parent_revision_sha256") != parent:
            raise ValueError("Review store parent revision checksum mismatch")
        if previous and (
            data["events"][: len(previous["events"])] != previous["events"]
            or len(data["events"]) <= len(previous["events"])
        ):
            raise ValueError("Review store must preserve every old event")
        proof.append(
            {"file": str(path.resolve()), "sha256": checksum(path), "revision_id": data["revision_id"]}
        )
        previous = data
    return previous, proof


def source_splits(manifest: dict, split_path: Path | None) -> tuple[list[dict], dict, dict, list[str]]:
    sources = manifest["sources"]
    assignments = {s["id"]: "unassigned" for s in sources}
    blockers = []
    if split_path:
        data = json.loads(split_path.read_text())
        original = {s["id"]: s for s in sources}
        sources, assignments = data["sources"], data["assignments"]
        if {s["id"] for s in sources} != set(original):
            raise ValueError("Split source set differs from immutable package")
        for s in sources:
            old = original[s["id"]]
            for key in (
                "sha256",
                "family_id",
                "session_id",
                "path",
                "camera_id",
                "camera_position_revision",
                "reserved_test_candidate",
            ):
                if s.get(key) != old.get(key):
                    raise ValueError("Immutable source/test/group provenance changed")
            if not set(old.get("overlap_groups", [])) <= set(s.get("overlap_groups", [])):
                raise ValueError("Known overlap evidence cannot be removed")
    try:
        components = validate_splits(sources, assignments)
    except ValueError as exc:
        components = leakage_components(sources)
        blockers.append(str(exc))
    return sources, assignments, components, blockers


def human_crop(event: dict, candidate: dict, proposal: dict, frame: dict) -> list[int]:
    if event["bbox_xyxy"] == proposal["bbox_xyxy"]:
        return candidate["crop_xyxy"]
    x0, y0, x1, y1 = event["bbox_xyxy"]
    return [
        max(0, math.floor(x0) - 16),
        max(0, math.floor(y0) - 16),
        min(frame["width"], math.ceil(x1) + 16),
        min(frame["height"], math.ceil(y1) + 16),
    ]


def validate_vector(path: Path, digest: str) -> np.ndarray:
    if checksum(path) != digest:
        raise ValueError("Feature checksum changed")
    vector = np.load(path, allow_pickle=False)
    if (
        vector.shape != (1024,)
        or not np.isfinite(vector).all()
        or not np.isclose(np.linalg.norm(vector), 1, atol=1e-4)
    ):
        raise ValueError("Finite L2-normalised 1024-dimensional frozen feature required")
    return vector


def training_gate(rows: list[dict], split_blockers: list[str]) -> list[str]:
    blockers = list(split_blockers)
    if not rows:
        blockers.append("No definitive human standing/lying labels")
    if any(r["feature_status"] != "ready" for r in rows):
        blockers.append("Corrected or missing crops need new real features")
    for split in ("train", "valid"):
        present = {r["label"] for r in rows if r["split"] == split}
        for missing in sorted(set(POSTURES) - present):
            blockers.append(f"{split}: missing human {missing} examples")
    # Small baseline minimum is explicitly structural, not statistical sufficiency.
    groups = {
        split: {r["group_id"] for r in rows if r["split"] == split} for split in ("train", "valid", "test")
    }
    if not groups["train"] or not groups["valid"] or groups["train"] & groups["valid"]:
        blockers.append("Independent labelled train/validation source groups required")
    seen = {}
    for r in rows:
        for tag in ("group:" + r["group_id"], "frame:" + r["frame_sha256"], "feature:" + r["cache_key"]):
            if tag in seen and seen[tag] != r["split"]:
                blockers.append("Source/frame/crop feature leakage across splits")
            seen[tag] = r["split"]
    return sorted(set(blockers))


def build_dataset(package: Path, store: Path | None, output: Path, split_path: Path | None = None) -> dict:
    manifest = json.loads((package / "manifest.json").read_text())
    feature_manifest = json.loads((package / "features.json").read_text())
    if feature_manifest["status"] != "success":
        raise ValueError("Real frozen DINOv3-L feature manifest required")
    model = feature_manifest["model"]
    validate_backbone(model)
    review, review_proof = load_review_store(package, store)
    raw = proposals(package)
    frames = {f["id"]: f for f in manifest["frames"]}
    candidates = {c["id"]: c for c in json.loads((package / "queue.json").read_text())["crops"]}
    features = {f["cache_key"]: f for f in feature_manifest["features"]}
    sources, assignments, groups, split_blockers = source_splits(manifest, split_path)
    allowed = learning_sources(sources)
    latest = {e["item_id"]: e for e in (review or {}).get("events", []) if e["scope"] == "posture_crop"}
    rows, unresolved = [], []
    label_counts = Counter()
    for item_id, event in latest.items():
        reviewed_frame = frames[event["frame_id"]]
        context = {
            "source_id": reviewed_frame["source_id"],
            "frame_id": reviewed_frame["id"],
            "light": reviewed_frame["light"],
            "split": assignments.get(reviewed_frame["source_id"], "unassigned"),
        }
        label = event.get("labels", {}).get("posture")
        if event["action"] == "unknown":
            label = "unknown"
        label_counts[label or "unlabelled"] += 1
        if event["action"] not in ("accept", "correct") or label not in POSTURES:
            unresolved.append(
                {
                    **context,
                    "item_id": item_id,
                    "event_id": event["id"],
                    "action": event["action"],
                    "label": label,
                    "loss_allowed": False,
                    "reason": "not_definitive_accepted_human_label",
                }
            )
            continue
        if event["labels"]["body_visibility"] in ("not_visible", "uncertain"):
            unresolved.append(
                {
                    **context,
                    "item_id": item_id,
                    "event_id": event["id"],
                    "action": event["action"],
                    "label": label,
                    "loss_allowed": False,
                    "reason": "body_evidence_insufficient",
                }
            )
            continue
        candidate = candidates[event["proposal_id"]]
        frame = frames[event["frame_id"]]
        if frame["source_id"] not in allowed:
            raise ValueError("Locked test source cannot enter posture dataset")
        source = next(s for s in sources if s["id"] == frame["source_id"])
        crop = human_crop(event, candidate, raw[event["proposal_id"]], frame)
        item = {
            "crop_xyxy": crop,
            "frame_sha256": frame["sha256"],
            "human_bbox_xyxy": event["bbox_xyxy"],
            "proposal_bbox_xyxy": raw[event["proposal_id"]]["bbox_xyxy"],
        }
        key = feature_key(model, item)
        cached = features.get(key)
        image = safe_file(package, frame["file"])
        if checksum(image) != frame["sha256"]:
            raise ValueError("Original raw frame checksum changed")
        if cached:
            if cached["frame_sha256"] != frame["sha256"] or cached["crop_xyxy"] != crop:
                raise ValueError("Feature cache crop/frame provenance differs")
            validate_vector(safe_file(package, cached["file"]), cached["sha256"])
        rows.append(
            {
                "item_id": item_id,
                "origin": "manual",
                "gold": False,
                "review_event_id": event["id"],
                "reviewer": event["reviewer"],
                "reviewed_at": event["reviewed_at"],
                "label": label,
                "visibility": event["labels"],
                "source_id": source["id"],
                "source_sha256": source["sha256"],
                "camera_id": source["camera_id"],
                "session_id": source["session_id"],
                "family_id": source["family_id"],
                "recording_day": source.get("recording_day"),
                "recording_day_verified": source.get("recording_day_verified", False),
                "group_id": groups[source["id"]],
                "split": assignments.get(source["id"], "unassigned"),
                "frame_id": frame["id"],
                "frame_sha256": frame["sha256"],
                "frame_file": str(image),
                "pts": frame["pts"],
                "time_base": frame["time_base"],
                "offset_s": frame["offset_s"],
                "light": frame["light"],
                "crop_xyxy": crop,
                "proposal_bbox_xyxy": raw[event["proposal_id"]]["bbox_xyxy"],
                "human_bbox_xyxy": event["bbox_xyxy"],
                "cache_key": key,
                "feature_status": "ready" if cached else "needs_real_feature",
                "feature_file": str(safe_file(package, cached["file"])) if cached else None,
                "feature_sha256": cached["sha256"] if cached else None,
            }
        )
    blockers = training_gate(rows, split_blockers)
    verified_groups = {r["group_id"] for r in rows if r["recording_day_verified"]}
    report = {
        "schema_version": "posture-label-sufficiency-1",
        "human_crop_items_reviewed": len(latest),
        "definitive_training_labels": len(rows),
        "accepted_class_counts": {c: sum(r["label"] == c for r in rows) for c in POSTURES},
        "latest_review_label_counts": {c: label_counts[c] for c in (*POSTURES, "unknown", "unlabelled")},
        "latest_action_counts": dict(Counter(e["action"] for e in latest.values())),
        "unknown_loss_allowed": False,
        "unresolved_items": unresolved,
        "day_night_counts": {c: sum(r["light"] == c for r in rows) for c in ("day", "night")},
        "source_counts": dict(Counter(r["source_id"] for r in rows)),
        "camera_counts": dict(Counter(r["camera_id"] for r in rows)),
        "verified_independent_labelled_groups": len(verified_groups),
        "conservative_source_components": len(set(groups.values())),
        "feature_ready": sum(r["feature_status"] == "ready" for r in rows),
        "training_allowed": not blockers,
        "blockers": blockers,
        "metrics": "ölçülemedi",
        "coverage_warnings": [
            f"Missing {light} {label} human coverage"
            for light in ("day", "night")
            for label in POSTURES
            if not any(r["light"] == light and r["label"] == label for r in rows)
        ],
        "minimum_policy": "Train and validation each need both classes and distinct verified groups; minimum is not statistical adequacy",
        "next_small_collection": "If lying absent: review body-visible lying candidates, then 5-10 lying crops per light from distinct verified sessions; target is collection guidance, not an acceptance guarantee",
    }
    prepare_output(output)
    proof = {
        "schema_version": "human-posture-dataset-1",
        "task": "crop_posture",
        "model": model,
        "package": str(package.resolve()),
        "package_sha256": checksum(package / "manifest.json"),
        "feature_manifest_sha256": checksum(package / "features.json"),
        "review_revisions": review_proof,
        "split_file": str(split_path.resolve()) if split_path else None,
        "split_sha256": checksum(split_path) if split_path else None,
        "sources": sources,
        "assignments": assignments,
        "rows": rows,
        "unresolved": unresolved,
        "training_allowed": not blockers,
        "roi_verified": False,
        "experiment_only": True,
        "feature_input": "unmasked RGB crop; padding16; CLS_L2; RGB_square224_bilinear_ImageNet",
        "cache_policy": "Mask-only edits do not affect unmasked RGB features; bbox/crop/frame/model/preprocessing changes invalidate cache",
    }
    write_json(output / "dataset.json", proof)
    write_json(output / "label_sufficiency.json", report)
    write_json(
        output / "splits.template.json",
        {"sources": sources, "assignments": assignments, "status": "not_human_verified"},
    )
    (output / "label_sufficiency.md").write_text(
        "# İnsan duruş etiketi yeterliliği\n\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report


def validate_dataset(path: Path) -> dict:
    data = json.loads(path.read_text())
    if (
        data.get("schema_version") != "human-posture-dataset-1"
        or data.get("task") != "crop_posture"
        or data.get("test_fixture")
    ):
        raise ValueError("Human posture dataset required; fixtures are not real training data")
    validate_backbone(data["model"])
    package = Path(data["package"])
    if (
        checksum(package / "manifest.json") != data["package_sha256"]
        or checksum(package / "features.json") != data["feature_manifest_sha256"]
    ):
        raise ValueError("Dataset package/feature manifest checksum mismatch")
    manifest = json.loads((package / "manifest.json").read_text())
    fm = json.loads((package / "features.json").read_text())
    if fm.get("status") != "success" or fm["model"] != data["model"]:
        raise ValueError("Dataset backbone differs from real feature manifest")
    split_path = Path(data["split_file"]) if data["split_file"] else None
    if split_path and checksum(split_path) != data["split_sha256"]:
        raise ValueError("Verified split file checksum changed")
    sources, assignments, groups, blockers = source_splits(manifest, split_path)
    if sources != data["sources"] or assignments != data["assignments"]:
        raise ValueError("Dataset sources/assignments differ from split evidence")
    refs = data["review_revisions"]
    review, checked = load_review_store(package, Path(refs[0]["file"]).parent if refs else None)
    if refs != checked:
        raise ValueError("Review store changed; create a new dataset revision")
    latest = {e["item_id"]: e for e in (review or {}).get("events", []) if e["scope"] == "posture_crop"}
    eligible = {
        i: e
        for i, e in latest.items()
        if e["action"] in ("accept", "correct")
        and e["labels"]["posture"] in POSTURES
        and e["labels"]["body_visibility"] in ("visible", "partial")
    }
    if {r["item_id"] for r in data["rows"]} != set(eligible) or len(data["rows"]) != len(eligible):
        raise ValueError("Dataset must contain exactly the latest definitive human decisions")
    raw = proposals(package)
    frames = {f["id"]: f for f in manifest["frames"]}
    crops = {c["id"]: c for c in json.loads((package / "queue.json").read_text())["crops"]}
    known_features = {r["cache_key"]: r for r in fm["features"]}
    refreshed = {r["cache_key"]: r for r in data.get("feature_refresh", {}).get("features", [])}
    for row in data["rows"]:
        e = eligible[row["item_id"]]
        f = frames[e["frame_id"]]
        source = next(s for s in sources if s["id"] == f["source_id"])
        require_learning_source(source, sources)
        expected_crop = human_crop(e, crops[e["proposal_id"]], raw[e["proposal_id"]], f)
        expected = {
            "review_event_id": e["id"],
            "origin": "manual",
            "gold": False,
            "label": e["labels"]["posture"],
            "reviewer": e["reviewer"],
            "reviewed_at": e["reviewed_at"],
            "visibility": e["labels"],
            "source_id": source["id"],
            "source_sha256": source["sha256"],
            "camera_id": source["camera_id"],
            "session_id": source["session_id"],
            "family_id": source["family_id"],
            "group_id": groups[source["id"]],
            "split": assignments.get(source["id"], "unassigned"),
            "frame_id": f["id"],
            "frame_sha256": f["sha256"],
            "light": f["light"],
            "pts": f["pts"],
            "time_base": f["time_base"],
            "offset_s": f["offset_s"],
            "crop_xyxy": expected_crop,
            "human_bbox_xyxy": e["bbox_xyxy"],
            "proposal_bbox_xyxy": raw[e["proposal_id"]]["bbox_xyxy"],
            "recording_day": source.get("recording_day"),
            "recording_day_verified": source.get("recording_day_verified", False),
        }
        if any(row.get(k) != v for k, v in expected.items()):
            raise ValueError("Dataset row differs from actual human/source/crop evidence")
        image = safe_file(package, f["file"])
        if row["frame_file"] != str(image) or checksum(image) != f["sha256"]:
            raise ValueError("Dataset original image changed")
        if row["cache_key"] != feature_key(data["model"], row):
            raise ValueError("Stale crop/model/preprocessing feature key")
        if row["feature_status"] == "ready":
            entry = known_features.get(row["cache_key"])
            if entry:
                if (
                    row["feature_file"] != str(safe_file(package, entry["file"]))
                    or row["feature_sha256"] != entry["sha256"]
                ):
                    raise ValueError("Ready feature differs from immutable cache")
            else:
                entry = refreshed.get(row["cache_key"])
                if not entry or any(
                    row[k] != entry[k]
                    for k in ("feature_file", "feature_sha256", "crop_xyxy", "frame_sha256")
                ):
                    raise ValueError("Corrected crop needs new real feature extraction evidence")
            validate_vector(Path(row["feature_file"]), row["feature_sha256"])
    data["blockers"] = training_gate(data["rows"], blockers)
    return data


def refresh_features(dataset: Path, access_path: Path, output: Path, maximum: int = 6) -> dict:
    """Optional bounded real inference in the existing isolated ML interpreter; no downloads."""
    if not 1 <= maximum <= 40:
        raise ValueError("Explicit new-feature budget 1..40 required")
    data = validate_dataset(dataset)
    missing = [r for r in data["rows"] if r["feature_status"] != "ready"]
    if len(missing) > maximum:
        raise ValueError("Corrected crop feature budget exceeded")
    access = json.loads(access_path.read_text())
    if (
        access["status"] != "weights_downloaded"
        or access["model"] != DINO_REPO
        or access["revision"] != DINO_REVISION
    ):
        raise ValueError("Matching local real DINOv3-L snapshot required")
    snapshot = Path(access["snapshot_path"])
    if checksum(snapshot / "model.safetensors") != DINO_WEIGHT:
        raise ValueError("Local DINOv3-L weight checksum mismatch")
    prepare_output(output)
    if missing:
        import torch
        from torchvision.transforms import functional as F
        from transformers import AutoModel

        model = AutoModel.from_pretrained(snapshot, local_files_only=True).to("cpu").eval()
        model.requires_grad_(False)
        (output / "feature-cache").mkdir()
        for row in missing:
            with Image.open(row["frame_file"]) as image:
                crop = (
                    image.convert("RGB").crop(row["crop_xyxy"]).resize((224, 224), Image.Resampling.BILINEAR)
                )
            tensor = F.normalize(F.to_tensor(crop), [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]).unsqueeze(0)
            with torch.inference_mode():
                vector = model(pixel_values=tensor).last_hidden_state[0, 0]
                vector = torch.nn.functional.normalize(vector.float(), dim=0).numpy()
            path = output / "feature-cache" / (row["cache_key"] + ".npy")
            np.save(path, vector, allow_pickle=False)
            row.update(
                feature_file=str(path.resolve()), feature_sha256=checksum(path), feature_status="ready"
            )
            validate_vector(path, row["feature_sha256"])
    data["parent_dataset_sha256"] = checksum(dataset)
    new_entries = [
        {k: r[k] for k in ("cache_key", "feature_file", "feature_sha256", "crop_xyxy", "frame_sha256")}
        for r in missing
    ]
    old_entries = data.get("feature_refresh", {}).get("features", [])
    data["feature_refresh"] = {
        "real_features_extracted": len(missing),
        "device": "cpu",
        "maximum": maximum,
        "model": data["model"],
        "features": old_entries + new_entries,
    }
    data["blockers"] = training_gate(
        data["rows"],
        [b for b in data["blockers"] if b != "Corrected or missing crops need new real features"],
    )
    data["training_allowed"] = not data["blockers"]
    write_json(output / "dataset.json", data)
    return data["feature_refresh"]


def prepare_session(package: Path, output: Path) -> dict:
    manifest = json.loads((package / "manifest.json").read_text())
    queue = json.loads((package / "queue.json").read_text())
    chosen = {}
    for light in ("day", "night"):
        rows = [r for r in queue["crops"] if r["light"] == light]
        selected, frames = [], set()
        for band in ("near", "middle", "far"):
            candidates = [r for r in rows if r["image_band_proxy"] == band and r not in selected]
            if not candidates:
                candidates = [r for r in rows if r not in selected]
            if not candidates:
                raise ValueError("Three existing diverse crops per light required")
            row = min(candidates, key=lambda r: (r["frame_id"] in frames, rows.index(r)))
            source = next(s for s in manifest["sources"] if s["id"] == row["source_id"])
            require_learning_source(source, manifest["sources"])
            selected.append(row)
            frames.add(row["frame_id"])
        chosen[light] = selected
    rows = [r for pair in zip(chosen["day"], chosen["night"], strict=True) for r in pair]
    result = {
        "schema_version": "posture-first-session-1",
        "package_sha256": checksum(package / "manifest.json"),
        "queue_sha256": checksum(package / "queue.json"),
        "crop_ids": [r["id"] for r in rows],
        "items": [
            {
                "item_id": "crop:" + r["id"],
                "light": r["light"],
                "frame_id": r["frame_id"],
                "crop_xyxy": r["crop_xyxy"],
                "image_band_proxy": r["image_band_proxy"],
            }
            for r in rows
        ],
        "selection": "Existing queue only; three light/band/PTS-diverse crops per light, interleaved",
        "human_labels": 0,
        "training_allowed": False,
    }
    prepare_output(output)
    write_json(output / "session.json", result)
    return result
