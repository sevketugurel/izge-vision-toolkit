#!/usr/bin/env python3
"""Validate actual diagnostic outputs and queue, no model run/download or human acceptance."""

import argparse
import json
import sys
from pathlib import Path
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.artifact_io import checksum, write_json  # noqa: E402
from izge.vision.low_label import require_learning_source  # noqa: E402
from izge.vision.low_label_features import feature_key  # noqa: E402
from izge.vision.proposal_review import proposals, validate_revision  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
p.add_argument("--source-dir", type=Path, required=True)
p.add_argument("--report", type=Path, required=True)
p.add_argument("--ui-fixture", type=Path)
a = p.parse_args()
m = json.loads((a.package / "manifest.json").read_text())
q = json.loads((a.package / "queue.json").read_text())
features = json.loads((a.package / "features.json").read_text())
c = {r["id"]: r for r in json.loads((a.package / "candidates.json").read_text())}
raw = proposals(a.package)
assert len(m["frames"]) == 8 and len(q["crops"]) <= 40 and len(q["full_frames"]) <= 4
for f in m["frames"]:
    source = next(s for s in m["sources"] if s["id"] == f["source_id"])
    require_learning_source(source, m["sources"])
    assert (
        checksum(a.source_dir / source["path"]) == source["sha256"]
        and checksum(a.package / f["file"]) == f["sha256"]
    )
for prop in raw.values():
    assert prop["class_id"] == 21 and prop["class_name"] == "cow" and 0.5 < prop["score"] <= 1
    assert (
        prop["animal_id"] is None
        and prop["track_id"] is None
        and prop["stall_id"] is None
        and prop["posture_proposal"] is None
    )
    with Image.open(a.package / prop["mask_file"]) as im:
        mask = np.asarray(im)
        assert mask.shape == (1440, 2560) and set(np.unique(mask)) <= {0, 255} and mask.any()
    x0, y0, x1, y1 = prop["bbox_xyxy"]
    assert 0 <= x0 < x1 <= 2560 and 0 <= y0 < y1 <= 1440
for entry in features.get("features", []):
    item = c[entry["id"]]
    assert (
        feature_key(features["model"], item) == entry["cache_key"]
        and checksum(a.package / entry["file"]) == entry["sha256"]
    )
    with (
        Image.open(
            a.package / next(f["file"] for f in m["frames"] if f["id"] == item["frame_id"])
        ) as original,
        Image.open(a.package / item["crop_file"]) as crop,
    ):
        assert np.array_equal(np.asarray(original.crop(item["crop_xyxy"])), np.asarray(crop))
    v = np.load(a.package / entry["file"], allow_pickle=False)
    assert v.shape == (1024,) and np.isfinite(v).all() and abs(np.linalg.norm(v) - 1) < 1e-5
for row in q["crops"]:
    assert row["labels"] == {"posture": None, "head_visibility": None, "body_visibility": None}
    source = next(s for s in m["sources"] if s["id"] == row["source_id"])
    require_learning_source(source, m["sources"])
assert (
    not q["human_labels"] and not q["training_allowed"] and not q["roi_verified"] and not q["split_verified"]
)
if a.ui_fixture:
    try:
        validate_revision(a.package, json.loads(a.ui_fixture.read_text()))
    except ValueError as e:
        assert "fixture" in str(e)
    else:
        raise AssertionError("Automated UI fixture entered human seed")
report = {
    "status": "passed",
    "raw_frames": 8,
    "model_proposals": len(raw),
    "models": [r["model_class"] for r in m["runs"]],
    "features": features["features_extracted"],
    "crops": len(q["crops"]),
    "full_frames": len(q["full_frames"]),
    "human_labels": 0,
    "training_allowed": False,
    "source_and_masks_unchanged": True,
    "ui_fixture_rejected": bool(a.ui_fixture),
}
write_json(a.report, report)
print(json.dumps(report))
