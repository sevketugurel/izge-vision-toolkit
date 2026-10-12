"""Budgeted, test-source-excluding proposal experiments and representative selection."""

import json
import random
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from ..video import FileVideoSource
from .artifact_io import checksum, prepare_output, write_json
from .config import load
from .data_preparation import frame_at
from .detection_inspection import environment_provenance, render_detections
from .detector import RFDETRSegDetector, box_iou
from .inspection import code_provenance
from .video_experiment import memory_snapshot


def learning_sources(sources: list[dict]) -> set[str]:
    """Exclude held-out camera and known derivative/session/overlap closure; unknown dates stay diagnostic."""
    blocked = {s["id"] for s in sources if s.get("reserved_test_candidate") or s.get("camera_id") == "barn3"}

    def tags(s):
        result = {("family", s["family_id"]), ("session", s["session_id"]), ("sha", s["sha256"])}
        result |= {("overlap", x) for x in s.get("overlap_groups", [])}
        if s.get("recording_day_verified"):
            result |= {("day", x) for x in s.get("recording_days") or [s["recording_day"]]}
        return result

    while True:
        held = set().union(*(tags(s) for s in sources if s["id"] in blocked)) if blocked else set()
        expanded = blocked | {s["id"] for s in sources if tags(s) & held}
        if expanded == blocked:
            return {s["id"] for s in sources} - blocked
        blocked = expanded


def require_learning_source(source: dict, sources: list[dict]):
    if source["id"] not in learning_sources(sources):
        raise ValueError("Locked test source excluded from proposals/features/selection/pseudo/adaptation")


def representative_indices(
    vectors: np.ndarray, budget: int, seed: int = 42, duplicate_cosine: float = 0.995
) -> list[int]:
    if not 1 <= budget <= 40 or vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise ValueError("Finite descriptors and explicit budget 1..40 required")
    if not len(vectors):
        return []
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms == 0).any():
        raise ValueError("Zero feature vector")
    normal = vectors / norms
    rng = random.Random(seed)
    remaining = list(range(len(vectors)))
    rng.shuffle(remaining)
    selected = [remaining.pop()]
    while remaining and len(selected) < budget:
        similarity = normal[remaining] @ normal[selected].T
        nearest = similarity.max(axis=1)
        winner = int(np.argmin(nearest))
        if nearest[winner] >= duplicate_cosine:
            break
        selected.append(remaining.pop(winner))
    return selected


def rgb_descriptor(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB").resize((16, 16)), dtype=np.float32).ravel() + 1


def comparison(
    pilot: Path, source_dir: Path, output: Path, locks: list[Path], weights: list[Path], device: str
) -> dict:
    prepare_output(output)
    manifest = json.loads((pilot / "manifest.json").read_text())
    sources = manifest["sources"]
    specs = [
        ("private-recording.MP4", [0, 60, 900, 1798.7], "day"),
        ("private-recording.MP4", [0, 60, 903.62, 1806.24], "night"),
    ]
    frames = []
    (output / "images").mkdir()
    for name, targets, light in specs:
        source = next(s for s in sources if s["path"] == name)
        require_learning_source(source, sources)
        path = source_dir / name
        if checksum(path) != source["sha256"]:
            raise ValueError("Source checksum changed")
        reader = FileVideoSource(path)
        for target in targets:
            started = perf_counter()
            frame = frame_at(reader, target)
            frame_id = f"{source['id']}-pts{frame.pts}"
            image = output / "images" / f"{frame_id}.png"
            frame.image.to_image().convert("RGB").save(image)
            frames.append(
                {
                    "id": frame_id,
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                    "camera_id": source["camera_id"],
                    "light": light,
                    "pts": frame.pts,
                    "time_base": str(frame.time_base),
                    "offset_s": frame.offset,
                    "file": str(image.relative_to(output)),
                    "sha256": checksum(image),
                    "width": frame.width,
                    "height": frame.height,
                    "decode_seconds": perf_counter() - started,
                    "reserved_test_candidate": False,
                    "recording_day": None,
                    "split": "unassigned",
                }
            )
    if len(frames) != 8 or len(locks) != 2 or len(weights) != 2:
        raise ValueError("This comparison requires two models and exactly eight frames, max24 pilot frames")
    report = {
        "schema_version": "low-label-comparison-1",
        "sources": sources,
        "frames": frames,
        "runs": [],
        "roi_verified": False,
        "roi_analysis_allowed": False,
        "training_allowed": False,
        "experiment_only": True,
        "human_labels": 0,
        "frame_budget": 8,
        "code": code_provenance(),
        "environment": environment_provenance(),
    }
    for lock, weight in zip(locks, weights, strict=True):
        spec = json.loads(lock.read_text())
        name = spec["model_class"]
        run_dir = output / name
        run_dir.mkdir()
        run = {"model_class": name, "status": "failed", "errors": [], "frames": [], "code": code_provenance()}
        started = perf_counter()
        try:
            detector = RFDETRSegDetector(
                weight,
                device,
                load(Path("config/behavior/barn2_long.draft.yaml")),
                "diagnostic-full-frame",
                model_lock=lock,
            )
            run.update(
                model=detector.provenance,
                device=detector.device,
                model_load_seconds=detector.load_seconds,
                memory_after_load=memory_snapshot(),
                preprocess={
                    "input_size": list(detector.input_size),
                    "square_resize": True,
                    "postprocess": "original pixels",
                    "score_gt": 0.5,
                },
            )
            with (run_dir / "proposals.jsonl").open("x") as sink:
                for item in frames:
                    source = next(s for s in sources if s["id"] == item["source_id"])
                    frame = frame_at(FileVideoSource(source_dir / source["path"]), item["offset_s"])
                    if frame.pts != item["pts"]:
                        raise ValueError("Comparison PTS changed")
                    results = detector.detect(frame)
                    proposals = []
                    for j, d in enumerate(results):
                        pid = f"{name}-{item['id']}-{j + 1:03d}"
                        mask = run_dir / f"{pid}.png"
                        Image.fromarray(d.mask.astype(np.uint8) * 255).save(mask)
                        row = {
                            "id": pid,
                            "frame_id": item["id"],
                            "origin": "model_proposal",
                            "human_reviewed": False,
                            "model_class": name,
                            "run_id": name,
                            "model_sha256": spec["weights_sha256"],
                            "source_id": item["source_id"],
                            "source_sha256": item["source_sha256"],
                            "frame_sha256": item["sha256"],
                            "pts": frame.pts,
                            "time_base": str(frame.time_base),
                            "offset_s": frame.offset,
                            "class_id": d.class_id,
                            "class_name": d.class_name,
                            "score": d.score,
                            "bbox_xyxy": list(d.box),
                            "mask_file": str(mask.relative_to(output)),
                            "mask_sha256": checksum(mask),
                            "mask_shape": [frame.height, frame.width],
                            "posture_proposal": None,
                            "animal_id": None,
                            "track_id": None,
                            "stall_id": None,
                        }
                        sink.write(json.dumps(row, allow_nan=False) + "\n")
                        proposals.append(pid)
                    preview = run_dir / f"{item['id']}-preview.png"
                    render_detections(
                        frame.image.to_image(),
                        results,
                        f"{name} | MODEL ÖNERİSİ / ROI KAPALI | PTS {frame.pts}",
                    ).save(preview)
                    run["frames"].append(
                        {
                            "id": item["id"],
                            "count": len(results),
                            "proposal_ids": proposals,
                            "inference_seconds": detector.inference_seconds[-1],
                            "preview": str(preview.relative_to(output)),
                        }
                    )
            run.update(
                status="success",
                inference_seconds=detector.inference_seconds,
                proposals_sha256=checksum(run_dir / "proposals.jsonl"),
            )
            del detector
            import gc

            gc.collect()
        except Exception as exc:
            run["errors"].append({"code": getattr(exc, "code", "model_or_source_unavailable")})
        run.update(total_seconds=perf_counter() - started, memory_final=memory_snapshot())
        write_json(run_dir / "manifest.json", run)
        report["runs"].append(run)
    report["source_checksums_unchanged"] = all(
        checksum(source_dir / s["path"]) == s["sha256"] for s in sources if s["path"] in {x[0] for x in specs}
    )
    write_json(output / "manifest.json", report)
    return report


def prepare_candidates(package: Path) -> list[dict]:
    manifest = json.loads((package / "manifest.json").read_text())
    preferred = next((r for r in reversed(manifest["runs"]) if r["status"] == "success"), None)
    if preferred is None:
        raise ValueError("No real successful model run")
    proposals = [
        json.loads(x)
        for x in (package / preferred["model_class"] / "proposals.jsonl").read_text().splitlines()
    ]
    all_rows = []
    for run in manifest["runs"]:
        if run["status"] == "success":
            all_rows += [
                json.loads(x)
                for x in (package / run["model_class"] / "proposals.jsonl").read_text().splitlines()
            ]
    frames = {f["id"]: f for f in manifest["frames"]}
    (package / "crops").mkdir()
    candidates = []
    for p in proposals:
        f = frames[p["frame_id"]]
        source = next(s for s in manifest["sources"] if s["id"] == f["source_id"])
        require_learning_source(source, manifest["sources"])
        x0, y0, x1, y1 = p["bbox_xyxy"]
        crop = [
            max(0, int(x0) - 16),
            max(0, int(y0) - 16),
            min(f["width"], int(np.ceil(x1)) + 16),
            min(f["height"], int(np.ceil(y1)) + 16),
        ]
        with Image.open(package / f["file"]) as image:
            image.crop(crop).save(package / "crops" / f"{p['id']}.png")
        other = [r for r in all_rows if r["frame_id"] == f["id"] and r["model_class"] != p["model_class"]]
        overlap = [
            box_iou(p["bbox_xyxy"], r["bbox_xyxy"])
            for r in proposals
            if r["frame_id"] == f["id"] and r["id"] != p["id"]
        ]
        candidates.append(
            {
                "id": p["id"],
                "proposal_id": p["id"],
                "frame_id": f["id"],
                "source_id": f["source_id"],
                "light": f["light"],
                "crop_xyxy": crop,
                "crop_file": f"crops/{p['id']}.png",
                "frame_sha256": f["sha256"],
                "score": p["score"],
                "image_band_proxy": "far"
                if (y0 + y1) / 2 < f["height"] * 0.35
                else "near"
                if (y0 + y1) / 2 > f["height"] * 0.65
                else "middle",
                "model_disagreement_signal": 1
                - max((box_iou(p["bbox_xyxy"], r["bbox_xyxy"]) for r in other), default=0),
                "neighbour_box_overlap_signal": max(overlap, default=0),
                "posture_proposal": None,
            }
        )
    write_json(package / "candidates.json", candidates)
    return candidates
