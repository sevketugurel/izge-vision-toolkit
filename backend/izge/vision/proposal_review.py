"""Append-only human review revisions, separate from immutable model proposals and COCO labels."""

import json
import math
from datetime import datetime
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image

from .annotation_data import validate_object
from .artifact_io import checksum, prepare_output, write_json
from .low_label import require_learning_source

ACTIONS = {"accept", "correct", "reject", "unknown", "skip", "add_instance", "frame_complete"}


def proposals(package: Path) -> dict:
    manifest = json.loads((package / "manifest.json").read_text())
    result = {}
    for run in manifest["runs"]:
        if run["status"] != "success":
            continue
        path = package / run["model_class"] / "proposals.jsonl"
        if checksum(path) != run["proposals_sha256"]:
            raise ValueError("Immutable proposal file changed")
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row["origin"] != "model_proposal" or row["human_reviewed"]:
                raise ValueError("Proposal origin corrupted")
            if checksum(package / row["mask_file"]) != row["mask_sha256"]:
                raise ValueError("Immutable model mask changed")
            result[row["id"]] = row
    return result


def decode_rle(rle: dict, width: int, height: int) -> np.ndarray:
    if rle.get("size") != [height, width] or not isinstance(rle.get("counts"), list):
        raise ValueError("Original pixel RLE shape required")
    counts = rle["counts"]
    if not counts or any(type(n) is not int or n < 0 for n in counts) or sum(counts) != width * height:
        raise ValueError("Invalid binary RLE runs")
    flat = np.empty(width * height, dtype=np.uint8)
    offset = 0
    for i, n in enumerate(counts):
        flat[offset : offset + n] = (i % 2) * 255
        offset += n
    return flat.reshape((height, width), order="F")


def validate_revision(package: Path, data: dict) -> dict:
    manifest = json.loads((package / "manifest.json").read_text())
    queue = json.loads((package / "queue.json").read_text())
    if data.get("review_model") not in {
        r["model_class"] for r in manifest["runs"] if r["status"] == "success"
    }:
        raise ValueError("Review model must reference a real successful proposal run")
    if data.get("schema_version") != "low-label-human-review-1" or data.get("package_sha256") != checksum(
        package / "manifest.json"
    ):
        raise ValueError("Review revision/package mismatch")
    raw = proposals(package)
    frames = {f["id"]: f for f in manifest["frames"]}
    crop_ids = {r["id"] for r in queue["crops"]}
    full_ids = {f["id"] for f in queue["full_frames"]}
    seen = {}
    last = None
    for e in data["events"]:
        UUID(e["id"])
        if e.get("test_fixture"):
            raise ValueError("Automated fixture is not a human seed")
        if (
            e.get("origin") != "manual"
            or e.get("gold") is not False
            or e.get("training_allowed") is not False
        ):
            raise ValueError("Explicit manual review event required; gold/training remains gated")
        if e.get("action") not in ACTIONS or not str(e.get("reviewer") or "").strip():
            raise ValueError("Human action/reviewer required")
        time = datetime.fromisoformat(e["reviewed_at"].replace("Z", "+00:00"))
        if time.tzinfo is None:
            raise ValueError("Review time needs timezone")
        if e["id"] in seen or e.get("parent_event_id") != last:
            raise ValueError("Append-only event chain broken")
        if type(e["active_ms"]) is not int or not 0 <= e["active_ms"] <= 3600000:
            raise ValueError("Bounded active interaction time required")
        frame = frames[e["frame_id"]]
        source = next(s for s in manifest["sources"] if s["id"] == frame["source_id"])
        require_learning_source(source, manifest["sources"])
        if e.get("scope") == "posture_crop":
            if e["proposal_id"] not in crop_ids:
                raise ValueError("Outside small crop queue")
        elif e.get("scope") not in ("full_frame_instance", "frame_complete") or frame["id"] not in full_ids:
            raise ValueError("Outside full-frame review queue")
        pid = e.get("proposal_id")
        expected_item = (
            "complete:" + frame["id"] if e["scope"] == "frame_complete"
            else ("crop:" if e["scope"] == "posture_crop" else "full:") + (pid or e["item_id"][5:])
        )
        if e["item_id"] != expected_item:
            raise ValueError("Review item/scope/proposal mismatch")
        if e["scope"] == "posture_crop" and e["action"] not in {"accept", "correct", "reject", "unknown", "skip"}:
            raise ValueError("Crop review cannot attest instance or frame completeness")
        if (e["scope"] == "frame_complete") != (e["action"] == "frame_complete"):
            raise ValueError("Frame completeness scope/action mismatch")
        if pid:
            p = raw[pid]
            if (p["frame_id"] != frame["id"] or e.get("proposal_mask_sha256") != p["mask_sha256"]
                    or p["model_class"] != data["review_model"]):
                raise ValueError("Proposal/frame provenance changed")
        elif e["action"] not in ("add_instance", "frame_complete"):
            raise ValueError("Proposal reference required")
        if e["action"] in ("accept", "correct", "add_instance"):
            labels = e.get("labels", {})
            for name, choices in {
                "posture": {"standing", "lying", "unknown"},
                "body_visibility": {"visible", "partial", "not_visible", "uncertain"},
                "head_visibility": {"visible", "partial", "not_visible", "uncertain"},
            }.items():
                if labels.get(name) is not None and labels[name] not in choices:
                    raise ValueError("Invalid human posture/visibility label")
                if e["scope"] == "posture_crop" and labels.get(name) not in choices:
                    raise ValueError("Explicit posture/body/head labels required")
            box = e["bbox_xyxy"]
            w, h = frame["width"], frame["height"]
            if (
                len(box) != 4
                or not all(math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= w
                or not 0 <= box[1] < box[3] <= h
            ):
                raise ValueError("Original pixel bbox required")
            if e["action"] == "accept" and box != p["bbox_xyxy"]:
                raise ValueError("Changed geometry requires an explicit correction event")
            if e["scope"] == "full_frame_instance":
                mask = decode_rle(e["mask_rle"], w, h)
                ys, xs = np.nonzero(mask)
                if (
                    not len(xs)
                    or xs.min() < box[0]
                    or xs.max() + 1 > box[2]
                    or ys.min() < box[1]
                    or ys.max() + 1 > box[3]
                ):
                    raise ValueError("Human mask must be nonempty and contained in bbox")
                if e["action"] == "accept":
                    with Image.open(package / p["mask_file"]) as original:
                        if box != p["bbox_xyxy"] or not np.array_equal(mask, np.asarray(original)):
                            raise ValueError("Changed geometry requires an explicit correction event")
        if e["action"] == "frame_complete":
            if (
                e.get("all_visible_instances_reviewed") is not True
                or e.get("remaining_unknown_instances") is not False
            ):
                raise ValueError("Full-frame completeness requires explicit human attestation")
        seen[e["id"]] = e
        last = e["id"]
    return {
        "events": len(seen),
        "active_ms": sum(e["active_ms"] for e in seen.values()),
        "correction_operations": sum(sum(e.get("corrections", {}).values()) for e in seen.values()),
        "gold": False,
        "training_allowed": False,
    }


def save_revision(package: Path, data: dict, store: Path, serialized: bytes | None = None) -> Path:
    validate_revision(package, data)
    store.mkdir(exist_ok=True)
    existing = sorted(store.glob("revision_*.json"))
    if existing:
        previous = json.loads(existing[-1].read_text())
        if (
            data.get("parent_revision_sha256") != checksum(existing[-1])
            or data["events"][: len(previous["events"])] != previous["events"]
        ):
            raise ValueError("Resume must preserve every old event and parent revision checksum")
        if len(data["events"]) <= len(previous["events"]):
            raise ValueError("No new human events")
    elif data.get("parent_revision_sha256") is not None:
        raise ValueError("Missing parent review revision")
    path = store / f"revision_{len(existing) + 1:04d}.json"
    if serialized is not None and json.loads(serialized) != data:
        raise ValueError("Serialized review differs from validated data")
    with path.open("xb") as f:
        f.write(
            serialized if serialized is not None else json.dumps(data, indent=2, ensure_ascii=False).encode()
        )
    return path


def human_bridge(package: Path, data: dict, output: Path) -> dict:
    validate_revision(package, data)
    manifest = json.loads((package / "manifest.json").read_text())
    raw = proposals(package)
    latest = {e["item_id"]: e for e in data["events"]}
    complete = {e["frame_id"]: e for e in latest.values() if e["action"] == "frame_complete"}
    objects_by_frame = {}
    completion_ids = {}
    for frame_id, completion in complete.items():
        completion_index = next(i for i, e in enumerate(data["events"]) if e["id"] == completion["id"])
        if any(
            e["frame_id"] == frame_id and e["scope"] == "full_frame_instance"
            for e in data["events"][completion_index + 1 :]
        ):
            raise ValueError("New instance edits require a new full-frame completion review")
        baseline = [
            p for p in raw.values() if p["frame_id"] == frame_id and p["model_class"] == data["review_model"]
        ]
        objects = []
        for p in baseline:
            e = latest.get("full:" + p["id"])
            if e is None or e["action"] not in ("accept", "correct", "reject"):
                raise ValueError(
                    "Incomplete frame: every proposal must be resolved, rejection is not a negative frame"
                )
            if e["action"] != "reject":
                objects.append(e)
        objects += [e for e in latest.values() if e["frame_id"] == frame_id and e["action"] == "add_instance"]
        if not objects:
            raise ValueError("Empty frame needs separate negative-image protocol, not proposal rejection")
        objects_by_frame[frame_id] = objects
        completion_ids[frame_id] = completion
    if not complete:
        raise ValueError("Crop labels alone cannot create full-frame COCO training data")
    prepare_output(output)
    (output / "images").mkdir()
    (output / "masks").mkdir()
    labels = []
    images = []
    for f in manifest["frames"]:
        import shutil

        target = output / f["file"]
        shutil.copyfile(package / f["file"], target)
        images.append({**f, "source_offset_s": f["offset_s"]})
        annotation = {
            "image_id": f["id"],
            "status": "unlabeled",
            "origin": None,
            "annotator": None,
            "annotated_at": None,
            "objects": [],
        }
        if f["id"] in objects_by_frame:
            completion = completion_ids[f["id"]]
            annotation.update(
                status="reviewed",
                origin="manual",
                annotator=completion["reviewer"],
                annotated_at=completion["reviewed_at"],
            )
            for e in objects_by_frame[f["id"]]:
                mask = decode_rle(e["mask_rle"], f["width"], f["height"])
                name = f"masks/{e['id']}.png"
                Image.fromarray(mask).save(output / name)
                obj = {
                    "class_name": "cow",
                    "bbox_xyxy": e["bbox_xyxy"],
                    "mask_file": name,
                    **e.get("labels", {}),
                    "human_review_event_id": e["id"],
                    "proposal_id": e.get("proposal_id"),
                }
                validate_object(obj, f["width"], f["height"], True, output)
                annotation["objects"].append(obj)
        labels.append(annotation)
    proof = {
        "sources": manifest["sources"],
        "images": images,
        "human_review_revision_id": data["revision_id"],
        "roi_verified": False,
        "training_allowed": False,
        "split_verified": False,
    }
    write_json(output / "manifest.json", proof)
    write_json(output / "annotations.human.json", {"frames": labels})
    write_json(output / "selected_full_frames.json", sorted(complete))
    return proof
