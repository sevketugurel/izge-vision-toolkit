"""Frozen ViT-L/16 feature cache; bounded diagnostic selection, never posture inference."""

import json
import hashlib
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from .artifact_io import checksum, write_json
from .low_label import learning_sources, require_learning_source, representative_indices, rgb_descriptor


def feature_key(model: dict, item: dict) -> str:
    record = {
        "model": model,
        "frame_sha256": item["frame_sha256"],
        "crop_xyxy": item["crop_xyxy"],
        "pooling": "CLS_L2",
        "preprocess": "RGB_square224_bilinear_ImageNet",
    }
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pool_candidates(package: Path, maximum: int = 40) -> list[dict]:
    if not 1 <= maximum <= 40:
        raise ValueError("Frozen feature budget must be 1..40")
    manifest = json.loads((package / "manifest.json").read_text())
    candidates = json.loads((package / "candidates.json").read_text())
    result = []
    for frame in manifest["frames"]:
        source = next(s for s in manifest["sources"] if s["id"] == frame["source_id"])
        require_learning_source(source, manifest["sources"])
        rows = [c for c in candidates if c["frame_id"] == frame["id"]]
        # Per-frame appearance diversification before any heavy model call.
        vectors = []
        for c in rows:
            with Image.open(package / c["crop_file"]) as image:
                vectors.append(rgb_descriptor(image))
        if rows:
            indices = representative_indices(
                np.asarray(vectors), min(5, maximum), seed=42, duplicate_cosine=0.9999
            )
            result += [rows[i] for i in indices]
    return result[:maximum]


def extract_features(package: Path, access_path: Path) -> dict:
    manifest = json.loads((package / "manifest.json").read_text())
    items = pool_candidates(package)
    access = json.loads(access_path.read_text())
    report = {
        "status": "unavailable",
        "features_extracted": 0,
        "posture_inferred": False,
        "training_started": False,
        "maximum_features": 40,
        "inputs": [r["id"] for r in items],
        "reason": access.get("reason"),
    }
    if access["status"] != "weights_downloaded":
        write_json(package / "features.json", report)
        return report
    started = perf_counter()
    try:
        import torch
        from transformers import AutoModel
        from torchvision.transforms import functional as F

        snapshot = Path(access["snapshot_path"])
        weights = {p.name: checksum(p) for p in snapshot.glob("*.safetensors")}
        if not weights:
            raise ValueError("No real weights")
        model_info = {
            "repo_id": access["model"],
            "revision": access["revision"],
            "weights_sha256": weights,
            "license": "Meta DINOv3",
            "attribution": "Built with DINOv3",
        }
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = AutoModel.from_pretrained(snapshot, local_files_only=True).to(device).eval()
        model.requires_grad_(False)
        if torch.backends.mps.is_available():
            torch.mps.synchronize()
        report.update(
            model=model_info,
            device=device,
            model_class=type(model).__name__,
            model_load_seconds=perf_counter() - started,
        )
        cache = package / "feature-cache"
        cache.mkdir()
        rows = []
        for item in items:
            source = next(s for s in manifest["sources"] if s["id"] == item["source_id"])
            require_learning_source(source, manifest["sources"])
            key = feature_key(model_info, item)
            with Image.open(package / item["crop_file"]) as image:
                tensor = F.to_tensor(image.convert("RGB").resize((224, 224), Image.Resampling.BILINEAR))
            tensor = F.normalize(tensor, [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]).unsqueeze(0).to(device)
            t = perf_counter()
            with torch.inference_mode():
                vector = model(pixel_values=tensor).last_hidden_state[0, 0]
            vector = torch.nn.functional.normalize(vector.float(), dim=0).cpu().numpy()
            if device == "mps":
                torch.mps.synchronize()
            path = cache / f"{key}.npy"
            np.save(path, vector, allow_pickle=False)
            rows.append(
                {
                    "id": item["id"],
                    "cache_key": key,
                    "file": str(path.relative_to(package)),
                    "sha256": checksum(path),
                    "shape": list(vector.shape),
                    "inference_seconds": perf_counter() - t,
                    "crop_xyxy": item["crop_xyxy"],
                    "frame_sha256": item["frame_sha256"],
                }
            )
        report.update(
            status="success",
            features_extracted=len(rows),
            features=rows,
            total_seconds=perf_counter() - started,
        )
    except Exception as exc:
        report.update(reason=type(exc).__name__, status="unavailable")
    write_json(package / "features.json", report)
    return report


def select_queue(package: Path, crop_budget: int = 24, seed: int = 42) -> dict:
    if not 20 <= crop_budget <= 40:
        raise ValueError("Crop review budget20..40")
    manifest = json.loads((package / "manifest.json").read_text())
    features = json.loads((package / "features.json").read_text())
    by_id = {c["id"]: c for c in json.loads((package / "candidates.json").read_text())}
    feature_by_id = {r["id"]: r for r in features.get("features", [])}
    candidates = pool_candidates(package)
    selected = []
    for light in ("day", "night"):
        rows = [r for r in candidates if r["light"] == light]
        vectors = []
        for r in rows:
            if features["status"] == "success":
                entry = feature_by_id[r["id"]]
                if checksum(package / entry["file"]) != entry["sha256"]:
                    raise ValueError("Feature checksum changed")
                vectors.append(np.load(package / entry["file"], allow_pickle=False))
            else:
                with Image.open(package / r["crop_file"]) as image:
                    vectors.append(rgb_descriptor(image))
        indices = representative_indices(np.asarray(vectors), crop_budget // 2, seed, 0.995) if rows else []
        selected += [
            {
                **rows[i],
                "scope": "posture_crop",
                "selection_reason": "light_balanced_farthest_feature_diversity",
                "labels": {"posture": None, "head_visibility": None, "body_visibility": None},
            }
            for i in indices
        ]
    # Explicit random high-score QC: replace two final slots, never inferred correctness.
    import random

    rng = random.Random(seed)
    high = [r for r in candidates if r["score"] >= 0.8 and r["id"] not in {x["id"] for x in selected}]
    rng.shuffle(high)
    for r in high[:2]:
        same = [
            i
            for i, x in enumerate(selected)
            if x["light"] == r["light"] and x["selection_reason"] != "seeded_high_score_quality_control"
        ]
        if same:
            selected[same[-1]] = {
                **r,
                "scope": "posture_crop",
                "selection_reason": "seeded_high_score_quality_control",
                "labels": {"posture": None, "head_visibility": None, "body_visibility": None},
            }
    chosen_frames = []
    for light in ("day", "night"):
        fs = [f for f in manifest["frames"] if f["light"] == light]
        # Two per light: priority is disagreement signal, second has distant appearance.
        ranked = sorted(
            fs,
            key=lambda f: (
                -sum(c["model_disagreement_signal"] for c in by_id.values() if c["frame_id"] == f["id"]),
                f["id"],
            ),
        )
        chosen_frames += ranked[:1] + (
            [max(ranked[1:], key=lambda f: abs(f["offset_s"] - ranked[0]["offset_s"]))]
            if len(ranked) > 1
            else []
        )
    q = {
        "schema_version": "low-label-queue-1",
        "seed": seed,
        "crop_budget": crop_budget,
        "full_frame_budget": 4,
        "selection_method": "DINOv3_L_CLS_frozen"
        if features["status"] == "success"
        else "RGB_thumbnail_model_free",
        "duplicate_cosine": 0.995,
        "posture_model": "unavailable",
        "human_labels": 0,
        "training_allowed": False,
        "roi_verified": False,
        "split_verified": False,
        "crops": selected,
        "full_frames": chosen_frames,
        "heldout_sources_excluded": sorted(
            set(s["id"] for s in manifest["sources"]) - learning_sources(manifest["sources"])
        ),
    }
    write_json(package / "queue.json", q)
    return q
