import json
import uuid

import numpy as np
import pytest
from PIL import Image

from izge.vision.artifact_io import checksum
from izge.vision.low_label import learning_sources, require_learning_source, representative_indices
from izge.vision.low_label_features import feature_key, pool_candidates
from izge.vision.proposal_review import decode_rle, human_bridge, save_revision, validate_revision
from izge.vision.detector import DetectorUnavailable, RFDETRSegDetector


def sources():
    return [
        {
            "id": str(i),
            "sha256": str(i),
            "family_id": str(i),
            "session_id": str(i),
            "camera_id": "barn3" if i == 3 else "barn2",
            "reserved_test_candidate": i == 3,
        }
        for i in range(1, 4)
    ]


@pytest.mark.parametrize("key", ["family_id", "session_id", "sha256", "overlap_groups"])
def test_locked_test_derivatives_are_excluded_transitively(key):
    s = sources()
    if key == "overlap_groups":
        s[0][key] = ["a"]
        s[1][key] = ["a", "b"]
        s[2][key] = ["b"]
    else:
        s[0][key] = s[2][key]
    assert "1" not in learning_sources(s)
    with pytest.raises(ValueError, match="Locked test"):
        require_learning_source(s[2], s)


def test_selection_is_deterministic_budgeted_and_removes_exact_copies():
    x = np.asarray([[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    a = representative_indices(x, 4, 42)
    assert a == representative_indices(x, 4, 42) and len(a) == 3
    assert len(representative_indices(x, 2)) == 2
    with pytest.raises(ValueError):
        representative_indices(x, 41)
    with pytest.raises(ValueError):
        representative_indices(np.zeros((2, 3)), 2)


def test_feature_cache_key_has_weight_revision_frame_crop_and_preprocessing():
    model = {"revision": "a", "weights_sha256": {"weights": "sha"}}
    item = {"frame_sha256": "frame", "crop_xyxy": [0, 0, 4, 4]}
    first = feature_key(model, item)
    assert first != feature_key({**model, "revision": "b"}, item)
    assert first != feature_key(model, {**item, "crop_xyxy": [1, 0, 4, 4]})
    assert first != feature_key(model, {**item, "frame_sha256": "changed"})


@pytest.fixture
def package(tmp_path):
    p = tmp_path / "package"
    p.mkdir()
    (p / "images").mkdir()
    (p / "RFDETRSegLarge").mkdir()
    im = p / "images/f.png"
    Image.new("RGB", (8, 6)).save(im)
    mask = p / "RFDETRSegLarge/m.png"
    a = np.zeros((6, 8), np.uint8)
    a[1:4, 2:5] = 255
    Image.fromarray(a).save(mask)
    prop = {
        "id": "p1",
        "frame_id": "f",
        "source_id": "1",
        "origin": "model_proposal",
        "human_reviewed": False,
        "model_class": "RFDETRSegLarge",
        "bbox_xyxy": [2, 1, 5, 4],
        "mask_file": "RFDETRSegLarge/m.png",
        "mask_sha256": checksum(mask),
    }
    path = p / "RFDETRSegLarge/proposals.jsonl"
    path.write_text(json.dumps(prop) + "\n")
    frame = {
        "id": "f",
        "source_id": "1",
        "file": "images/f.png",
        "width": 8,
        "height": 6,
        "sha256": checksum(im),
        "pts": 1000,
        "time_base": "1/1000",
        "offset_s": 1.0,
    }
    manifest = {
        "sources": sources(),
        "frames": [frame],
        "runs": [{"status": "success", "model_class": "RFDETRSegLarge", "proposals_sha256": checksum(path)}],
    }
    (p / "manifest.json").write_text(json.dumps(manifest))
    (p / "queue.json").write_text(json.dumps({"crops": [{"id": "p1"}], "full_frames": [frame]}))
    return p, prop


def event(package, action="accept", scope="full_frame_instance"):
    p, prop = package
    return {
        "id": str(uuid.uuid4()),
        "parent_event_id": None,
        "origin": "manual",
        "gold": False,
        "training_allowed": False,
        "action": action,
        "reviewer": "synthetic unit fixture",
        "reviewed_at": "2026-10-12T12:00:00Z",
        "active_ms": 1200,
        "item_id": ("crop:" if scope == "posture_crop" else "full:") + "p1",
        "frame_id": "f",
        "proposal_id": "p1",
        "proposal_mask_sha256": prop["mask_sha256"],
        "scope": scope,
        "bbox_xyxy": [2, 1, 5, 4],
        "mask_rle": {"size": [6, 8], "counts": [13, 3, 3, 3, 3, 3, 20]},
        "labels": {"posture": "unknown", "head_visibility": "partial", "body_visibility": "partial"},
        "corrections": {"mask": 0},
    }


def revision(package, events):
    p, _ = package
    return {
        "schema_version": "low-label-human-review-1",
        "revision_id": str(uuid.uuid4()),
        "parent_revision_sha256": None,
        "package_sha256": checksum(p / "manifest.json"),
        "review_model": "RFDETRSegLarge",
        "events": events,
    }


def test_loading_or_empty_revision_never_makes_a_human_label(package):
    p, _ = package
    r = revision(package, [])
    assert validate_revision(p, r)["events"] == 0
    with pytest.raises(ValueError, match="Crop labels alone"):
        human_bridge(p, r, p.parent / "bad")


@pytest.mark.parametrize("action", ["accept", "correct", "reject", "unknown", "skip"])
def test_explicit_human_actions_are_separate_from_proposals(package, action):
    p, prop = package
    before = checksum(p / "RFDETRSegLarge/proposals.jsonl")
    e = event(package, action)
    r = revision(package, [e])
    assert validate_revision(p, r)["events"] == 1
    save_revision(p, r, p.parent / "reviews")
    assert checksum(p / "RFDETRSegLarge/proposals.jsonl") == before and prop["origin"] == "model_proposal"


def test_resume_preserves_old_events_and_raw_revision_bytes(package):
    p, _ = package
    r = revision(package, [event(package)])
    raw = json.dumps(r, separators=(",", ":")).encode()
    first = save_revision(p, r, p.parent / "store", raw)
    assert first.read_bytes() == raw
    e = event(package, "skip")
    e["parent_event_id"] = r["events"][0]["id"]
    second = {
        **r,
        "revision_id": str(uuid.uuid4()),
        "parent_revision_sha256": checksum(first),
        "events": r["events"] + [e],
    }
    save_revision(p, second, p.parent / "store")
    second["events"][0]["reviewer"] = "changed"
    with pytest.raises(ValueError):
        save_revision(p, second, p.parent / "store")


def test_crop_labels_unknown_skips_and_rejection_do_not_create_full_frame_training(package):
    p, _ = package
    for action in ["accept", "unknown", "skip", "reject"]:
        r = revision(package, [event(package, action, "posture_crop")])
        with pytest.raises(ValueError, match="Crop labels alone"):
            human_bridge(p, r, p.parent / action)


def test_complete_full_frame_human_bridge_still_does_not_approve_split_or_roi(package):
    p, _ = package
    e = event(package)
    done = {
        **event(package),
        "id": str(uuid.uuid4()),
        "parent_event_id": e["id"],
        "item_id": "complete:f",
        "scope": "frame_complete",
        "action": "frame_complete",
        "proposal_id": None,
        "all_visible_instances_reviewed": True,
        "remaining_unknown_instances": False,
    }
    result = human_bridge(p, revision(package, [e, done]), p.parent / "bridge")
    assert not result["training_allowed"] and not result["roi_verified"] and not result["split_verified"]
    labels = json.loads((p.parent / "bridge/annotations.human.json").read_text())
    assert labels["frames"][0]["origin"] == "manual" and len(labels["frames"][0]["objects"]) == 1
    e["action"] = "unknown"
    with pytest.raises(ValueError, match="Incomplete frame"):
        human_bridge(p, revision(package, [e, done]), p.parent / "bad")


@pytest.mark.parametrize("change", ["fixture", "origin", "gold", "mask_shape", "mask_runs", "box", "chain"])
def test_review_geometry_provenance_and_fixture_gates(package, change):
    p, _ = package
    e = event(package)
    if change == "fixture":
        e["test_fixture"] = True
    elif change == "origin":
        e["origin"] = "model_proposal"
    elif change == "gold":
        e["gold"] = True
    elif change == "mask_shape":
        e["mask_rle"]["size"] = [8, 6]
    elif change == "mask_runs":
        e["mask_rle"]["counts"] = [1]
    elif change == "box":
        e["bbox_xyxy"] = [0, 0, 1, 1]
    else:
        e["parent_event_id"] = "nonexistent"
    with pytest.raises(ValueError):
        validate_revision(p, revision(package, [e]))


def test_pseudo_and_locked_test_cannot_enter_existing_human_gate(package):
    p, _ = package
    e = event(package)
    e["origin"] = "pseudo"
    with pytest.raises(ValueError):
        validate_revision(p, revision(package, [e]))
    manifest = json.loads((p / "manifest.json").read_text())
    manifest["frames"][0]["source_id"] = "3"
    (p / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Locked test"):
        validate_revision(p, revision(package, [event(package)]))
    (p / "candidates.json").write_text("[]")
    with pytest.raises(ValueError, match="Locked test"):
        pool_candidates(p)


def test_large_native_resolution_mismatch_is_explicit_unavailable(tmp_path):
    lock = tmp_path / "model.json"
    lock.write_text(json.dumps({"model_class": "RFDETRSegLarge", "input_size": [384, 384]}))
    with pytest.raises(DetectorUnavailable, match="native_resolution"):
        RFDETRSegDetector(tmp_path / "no-weight", "cpu", None, "diagnostic-full-frame", model_lock=lock)


def test_mask_rle_column_order():
    m = decode_rle({"size": [6, 8], "counts": [13, 3, 3, 3, 3, 3, 20]}, 8, 6)
    assert (m[1:4, 2:5] == 255).all() and np.count_nonzero(m) == 9


def test_real_output_writer_hashes_closed_jsonl_without_model_download(tmp_path, monkeypatch):
    import av
    from fractions import Fraction
    from izge.domain import Frame
    from izge.vision.detector import Detection
    from izge.vision import low_label

    src = tmp_path / "sources"
    src.mkdir()
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    rows = []
    for i, name in enumerate(["day-example.mp4", "night-example.mp4"]):
        p = src / name
        p.write_bytes(b"synthetic-source-fixture")
        rows.append(
            {
                "id": str(i),
                "path": name,
                "sha256": checksum(p),
                "family_id": str(i),
                "session_id": str(i),
                "camera_id": "barn2",
            }
        )
    (pilot / "manifest.json").write_text(json.dumps({"sources": rows}))
    monkeypatch.setattr(low_label, "FileVideoSource", lambda p: None)
    monkeypatch.setattr(
        low_label,
        "frame_at",
        lambda reader, offset: Frame(
            round(offset * 1000),
            Fraction(1, 1000),
            offset,
            8,
            6,
            av.VideoFrame.from_image(Image.new("RGB", (8, 6))),
        ),
    )

    class ModelFixture:
        def __init__(self, *args, model_lock):
            self.provenance = json.loads(model_lock.read_text())
            self.device = "cpu"
            self.input_size = (8, 8)
            self.load_seconds = 0.0
            self.inference_seconds = []

        def detect(self, frame):
            self.inference_seconds.append(0.001)
            mask = np.ones((6, 8), dtype=bool)
            return [Detection((0, 0, 8, 6), 21, "cow", 0.8, mask)]

    monkeypatch.setattr(low_label, "RFDETRSegDetector", ModelFixture)
    locks = []
    for name in ["RFDETRSegSmall", "RFDETRSegLarge"]:
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps({"model_class": name, "weights_sha256": "synthetic-fixture"}))
        locks.append(p)
    result = low_label.comparison(
        pilot, src, tmp_path / "output", locks, [tmp_path / "unused"] * 2, "cpu",
        day_source="day-example.mp4", night_source="night-example.mp4",
    )
    for run in result["runs"]:
        assert run["status"] == "success"
        assert run["proposals_sha256"] == checksum(
            tmp_path / "output" / run["model_class"] / "proposals.jsonl"
        )
