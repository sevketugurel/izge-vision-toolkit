"""Synthetic schema/features only; these fixtures are never real human labels or model runs."""

import json
from copy import deepcopy
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from test_low_label import event, package as base_fixture, revision
from izge.vision.artifact_io import checksum
from izge.vision.checkpoint_audit import audit_query_rows
from izge.vision.low_label_features import feature_key
from izge.vision.posture_classifier import PostureMLP, evaluate_saved, fit, metrics, train
from izge.vision.posture_data import (
    DINO_REPO,
    DINO_REVISION,
    DINO_WEIGHT,
    build_dataset,
    load_review_store,
    prepare_session,
    source_splits,
    training_gate,
    validate_dataset,
    validate_vector,
)
from izge.vision.proposal_review import save_revision, validate_revision

MODEL = {
    "repo_id": DINO_REPO,
    "revision": DINO_REVISION,
    "weights_sha256": {"model.safetensors": DINO_WEIGHT},
    "license": "Meta DINOv3",
    "attribution": "Built with DINOv3",
}


@pytest.fixture
def posture_package(tmp_path):
    p, prop = base_fixture.__wrapped__(tmp_path)
    Image.new("RGB", (40, 40), "brown").save(p / "images/f.png")
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[18:24, 18:24] = 255
    Image.fromarray(mask).save(p / prop["mask_file"])
    prop.update(bbox_xyxy=[18, 18, 24, 24], mask_sha256=checksum(p / prop["mask_file"]))
    raw = p / "RFDETRSegLarge/proposals.jsonl"
    raw.write_text(json.dumps(prop) + "\n")
    m = json.loads((p / "manifest.json").read_text())
    f = m["frames"][0]
    f.update(width=40, height=40, sha256=checksum(p / "images/f.png"), light="day")
    m["runs"][0]["proposals_sha256"] = checksum(raw)
    (p / "manifest.json").write_text(json.dumps(m))
    c = {
        "id": "p1",
        "proposal_id": "p1",
        "frame_id": "f",
        "source_id": "1",
        "light": "day",
        "image_band_proxy": "near",
        "crop_xyxy": [2, 2, 40, 40],
        "frame_sha256": f["sha256"],
    }
    (p / "queue.json").write_text(json.dumps({"crops": [c], "full_frames": [f]}))
    vector = np.ones(1024, dtype=np.float32) / 32
    (p / "feature-cache").mkdir()
    key = feature_key(MODEL, c)
    np.save(p / "feature-cache" / (key + ".npy"), vector, allow_pickle=False)
    file = "feature-cache/" + key + ".npy"
    fm = {
        "status": "success",
        "model": MODEL,
        "features": [
            {
                "id": "p1",
                "cache_key": key,
                "file": file,
                "sha256": checksum(p / file),
                "crop_xyxy": c["crop_xyxy"],
                "frame_sha256": f["sha256"],
            }
        ],
    }
    (p / "features.json").write_text(json.dumps(fm))
    return p, prop


def crop_event(pack, action="accept", label="standing"):
    e = event(pack, action, "posture_crop")
    e["bbox_xyxy"] = pack[1]["bbox_xyxy"]
    e["labels"]["posture"] = label
    return e


def reviewed(pack, e):
    store = pack[0].parent / "reviews"
    save_revision(pack[0], revision(pack, [e]), store)
    return store


def test_zero_real_labels_report_and_training_gate(posture_package):
    p, _ = posture_package
    report = build_dataset(p, None, p.parent / "dataset")
    assert report["definitive_training_labels"] == 0
    assert report["accepted_class_counts"] == {"standing": 0, "lying": 0}
    assert report["verified_independent_labelled_groups"] == 0
    assert report["conservative_source_components"] == 1
    with pytest.raises(ValueError, match="Training gate closed"):
        train(p.parent / "dataset/dataset.json", p.parent / "must-not-train")
    assert not (p.parent / "must-not-train").exists()


@pytest.mark.parametrize(
    "action,label",
    [("reject", "standing"), ("skip", "lying"), ("unknown", "standing"), ("accept", "unknown")],
)
def test_uncertain_rejected_skipped_never_enter_loss(posture_package, action, label):
    p, _ = posture_package
    store = reviewed(posture_package, crop_event(posture_package, action, label))
    r = build_dataset(p, store, p.parent / "dataset")
    assert r["definitive_training_labels"] == 0 and r["unresolved_items"][0]["loss_allowed"] is False


@pytest.mark.parametrize("body", ["not_visible", "uncertain"])
def test_conflicting_body_evidence_is_not_definitive(posture_package, body):
    p, _ = posture_package
    e = crop_event(posture_package)
    e["labels"]["body_visibility"] = body
    store = reviewed(posture_package, e)
    r = build_dataset(p, store, p.parent / "dataset")
    assert (
        r["definitive_training_labels"] == 0
        and r["unresolved_items"][0]["reason"] == "body_evidence_insufficient"
    )


def test_only_latest_human_decision_is_used(posture_package):
    p, _ = posture_package
    first = crop_event(posture_package)
    store = reviewed(posture_package, first)
    second = crop_event(posture_package, "correct", "lying")
    second["parent_event_id"] = first["id"]
    new = revision(posture_package, [first, second])
    new["parent_revision_sha256"] = checksum(store / "revision_0001.json")
    save_revision(p, new, store)
    report = build_dataset(p, store, p.parent / "dataset")
    data = validate_dataset(p.parent / "dataset/dataset.json")
    assert report["accepted_class_counts"] == {"standing": 0, "lying": 1}
    assert data["rows"][0]["review_event_id"] == second["id"] and report["feature_ready"] == 1
    assert data["rows"][0]["group_id"] == "1" and data["rows"][0]["split"] == "unassigned"


def test_later_rejection_removes_previous_label(posture_package):
    p, _ = posture_package
    first = crop_event(posture_package)
    store = reviewed(posture_package, first)
    last = crop_event(posture_package, "reject")
    last["parent_event_id"] = first["id"]
    r = revision(posture_package, [first, last])
    r["parent_revision_sha256"] = checksum(store / "revision_0001.json")
    save_revision(p, r, store)
    result = build_dataset(p, store, p.parent / "dataset")
    assert result["definitive_training_labels"] == 0


def test_corrected_crop_requires_new_key_not_old_feature(posture_package):
    p, prop = posture_package
    e = crop_event(posture_package, "correct")
    e["bbox_xyxy"] = [20, 20, 30, 30]
    store = reviewed(posture_package, e)
    build_dataset(p, store, p.parent / "dataset")
    data = validate_dataset(p.parent / "dataset/dataset.json")
    row = data["rows"][0]
    assert row["crop_xyxy"] == [4, 4, 40, 40]
    assert row["feature_status"] == "needs_real_feature" and row["feature_file"] is None
    old = json.loads((p / "features.json").read_text())["features"][0]
    assert row["cache_key"] != old["cache_key"]
    # Relabelling the old vector as the new crop must be rejected too.
    row.update(
        feature_status="ready", feature_file=str((p / old["file"]).resolve()), feature_sha256=old["sha256"]
    )
    path = p.parent / "dataset/forged.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="new real feature"):
        validate_dataset(path)


def test_unchanged_unmasked_rgb_crop_reuses_feature_after_label_edit(posture_package):
    p, _ = posture_package
    store = reviewed(posture_package, crop_event(posture_package, "correct", "lying"))
    build_dataset(p, store, p.parent / "dataset")
    data = validate_dataset(p.parent / "dataset/dataset.json")
    assert data["rows"][0]["feature_status"] == "ready"
    assert "Mask-only" in data["cache_policy"]


def test_bbox_edit_invalidates_old_feature_even_if_integer_crop_is_identical(posture_package):
    p, _ = posture_package
    e = crop_event(posture_package, "correct")
    e["bbox_xyxy"] = [18.1, 18.1, 24, 24]
    store = reviewed(posture_package, e)
    build_dataset(p, store, p.parent / "dataset")
    data = validate_dataset(p.parent / "dataset/dataset.json")
    row = data["rows"][0]
    assert row["crop_xyxy"] == [2, 2, 40, 40]
    assert row["feature_status"] == "needs_real_feature"
    old = json.loads((p / "features.json").read_text())["features"][0]
    assert row["cache_key"] != old["cache_key"]


@pytest.mark.parametrize("mutation", ["fixture", "wrong_item", "changed_accept", "crop_complete"])
def test_revision_scope_fixture_and_geometry_guards(posture_package, mutation):
    e = crop_event(posture_package)
    if mutation == "fixture":
        e["test_fixture"] = True
    if mutation == "wrong_item":
        e["item_id"] = "crop:not-p1"
    if mutation == "changed_accept":
        e["bbox_xyxy"] = [0, 0, 30, 30]
    if mutation == "crop_complete":
        e["action"] = "frame_complete"
    with pytest.raises(ValueError):
        validate_revision(posture_package[0], revision(posture_package, [e]))


def test_review_store_tamper_and_stale_dataset_are_rejected(posture_package):
    p, _ = posture_package
    first = crop_event(posture_package)
    store = reviewed(posture_package, first)
    build_dataset(p, store, p.parent / "dataset")
    second = crop_event(posture_package, "unknown")
    second["parent_event_id"] = first["id"]
    r = revision(posture_package, [first, second])
    r["parent_revision_sha256"] = checksum(store / "revision_0001.json")
    save_revision(p, r, store)
    with pytest.raises(ValueError, match="store changed"):
        validate_dataset(p.parent / "dataset/dataset.json")
    raw = json.loads((store / "revision_0002.json").read_text())
    raw["parent_revision_sha256"] = "wrong"
    (store / "revision_0002.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_review_store(p, store)


@pytest.mark.parametrize("key", ["camera_id", "reserved_test_candidate", "session_id", "sha256"])
def test_source_or_test_identity_cannot_be_rewritten(posture_package, key):
    p, _ = posture_package
    m = json.loads((p / "manifest.json").read_text())
    data = {"sources": deepcopy(m["sources"]), "assignments": {"1": "train", "2": "valid", "3": "test"}}
    data["sources"][2][key] = "changed"
    path = p.parent / "splits.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Immutable"):
        source_splits(m, path)


def test_unknown_recording_days_are_one_conservative_group(posture_package):
    p, _ = posture_package
    m = json.loads((p / "manifest.json").read_text())
    _, _, groups, blockers = source_splits(m, None)
    assert len(set(groups.values())) == 1 and blockers


def test_structural_class_split_leakage_gate():
    base = {"feature_status": "ready", "cache_key": "a", "frame_sha256": "f"}
    rows = [
        {
            **base,
            "label": label,
            "split": split,
            "group_id": split,
            "cache_key": split + label,
            "frame_sha256": split + label,
        }
        for split in ("train", "valid")
        for label in ("standing", "lying")
    ]
    assert training_gate(rows, []) == []
    assert any("lying" in b for b in training_gate([r for r in rows if r["label"] == "standing"], []))
    rows[-1]["frame_sha256"] = rows[0]["frame_sha256"]
    assert any("leakage" in b for b in training_gate(rows, []))


@pytest.mark.parametrize("vector", [np.ones(16), np.zeros(1024), np.full(1024, np.nan)])
def test_feature_dimension_l2_and_finite_contract(tmp_path, vector):
    path = tmp_path / "bad.npy"
    np.save(path, vector)
    with pytest.raises(ValueError):
        validate_vector(path, checksum(path))


def test_mlp_really_fits_synthetic_features_and_roundtrips_probabilities():
    rng = np.random.default_rng(10)
    x = rng.normal(0, 0.001, (20, 1024))
    x[:10, 0] = 1
    x[10:, 0] = -1
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    labels = ["standing"] * 10 + ["lying"] * 10
    model, params = fit(x, labels, MODEL, hidden=4)
    restored = PostureMLP.from_bytes(model.to_bytes())
    assert np.allclose(model.predict_proba(x), restored.predict_proba(x))
    assert np.allclose(restored.predict_proba(x).sum(axis=1), 1)
    assert np.array_equal(restored.predict_proba(x).argmax(axis=1), [0] * 10 + [1] * 10)
    assert params["device"] == "cpu" and params["standardisation"] == "train only"
    with pytest.raises(ValueError):
        restored.predict_proba(np.ones((1, 384)))
    with pytest.raises(ValueError):
        fit(x, ["standing"] * 20, MODEL)
    with pytest.raises(ValueError):
        fit(x, labels, {**MODEL, "revision": "wrong"})
    assert metrics(model, [], 0.6)["status"] == "ölçülemedi"


def test_query_prefix_audit_does_not_infer_training_partition():
    original = {
        "refpoint_embed.weight": np.arange(3900 * 4).reshape(3900, 4),
        "query_feat.weight": np.arange(3900 * 256).reshape(3900, 256),
    }
    loaded = {k: v[:2600].copy() for k, v in original.items()}
    r = audit_query_rows(original, loaded, 200, 13)
    assert not r["checkpoint_factorisation_known"] and not r["training_teacher_accepted"]
    assert all(v["eval_prefix_equal_checkpoint"] for v in r["queries"].values())
    loaded["query_feat.weight"][300] = 0
    r = audit_query_rows(original, loaded, 200, 13)
    assert r["queries"]["query_feat.weight"]["eval_prefix_equal_checkpoint"]
    assert not r["queries"]["query_feat.weight"]["all_loaded_rows_equal_checkpoint_prefix"]
    loaded["query_feat.weight"][0] = 0
    assert not audit_query_rows(original, loaded, 200, 13)["queries"]["query_feat.weight"][
        "eval_prefix_equal_checkpoint"
    ]
    with pytest.raises(ValueError):
        audit_query_rows(original, {k: v[:20] for k, v in loaded.items()}, 200, 13)


def test_first_six_balanced_existing_candidates_only(posture_package):
    p, _ = posture_package
    rows = [
        {
            "id": f"{light}-{band}",
            "source_id": "1",
            "frame_id": f"f-{band}",
            "crop_xyxy": [0, 0, 4, 4],
            "light": light,
            "image_band_proxy": band,
        }
        for light in ("day", "night")
        for band in ("near", "middle", "far")
    ]
    (p / "queue.json").write_text(json.dumps({"crops": rows, "full_frames": []}))
    before = checksum(p / "queue.json")
    result = prepare_session(p, p.parent / "six")
    assert len(set(result["crop_ids"])) == 6
    assert [r["light"] for r in result["items"]] == ["day", "night"] * 3
    assert result["human_labels"] == 0 and checksum(p / "queue.json") == before


def test_full_training_evaluation_cli_functions_on_synthetic_verified_schema(posture_package):
    """Temporary synthetic schema/feature fixtures, not delivered manual/gold data."""
    p, prop = posture_package
    m = json.loads((p / "manifest.json").read_text())
    for i, s in enumerate(m["sources"], 1):
        s.update(
            recording_day=f"2026-10-0{i}",
            recording_day_verified=True,
            recording_day_verification={
                "verified_by": "synthetic fixture",
                "verified_at": "2026-10-12T12:00:00Z",
                "evidence": "synthetic test only",
            },
        )
    base = m["frames"][0]
    frames, crops, props, features, events = [], [], [], [], []
    last = None
    for i in range(4):
        f = {
            **base,
            "id": f"f{i}",
            "source_id": "1" if i < 2 else "2",
            "light": "day" if i % 2 == 0 else "night",
            "file": f"images/f{i}.png",
        }
        Image.new("RGB", (40, 40), (100 + i, 30, 20)).save(p / f["file"])
        f["sha256"] = checksum(p / f["file"])
        pr = {**prop, "id": f"p{i}", "frame_id": f["id"], "source_id": f["source_id"]}
        c = {
            "id": pr["id"],
            "proposal_id": pr["id"],
            "frame_id": f["id"],
            "source_id": f["source_id"],
            "light": f["light"],
            "crop_xyxy": [2, 2, 40, 40],
            "frame_sha256": f["sha256"],
        }
        key = feature_key(MODEL, c)
        vec = np.zeros(1024)
        vec[0] = 1 if i % 2 == 0 else -1
        file = "feature-cache/" + key + ".npy"
        np.save(p / file, vec, allow_pickle=False)
        features.append(
            {
                "id": pr["id"],
                "cache_key": key,
                "file": file,
                "sha256": checksum(p / file),
                "crop_xyxy": c["crop_xyxy"],
                "frame_sha256": f["sha256"],
            }
        )
        e = crop_event(posture_package, label="standing" if i % 2 == 0 else "lying")
        e.update(
            id=str(uuid4()),
            parent_event_id=last,
            item_id="crop:" + pr["id"],
            proposal_id=pr["id"],
            frame_id=f["id"],
        )
        last = e["id"]
        events.append(e)
        frames.append(f)
        crops.append(c)
        props.append(pr)
    raw = p / "RFDETRSegLarge/proposals.jsonl"
    raw.write_text("".join(json.dumps(pr) + "\n" for pr in props))
    m["runs"][0]["proposals_sha256"] = checksum(raw)
    m["frames"] = frames
    (p / "manifest.json").write_text(json.dumps(m))
    (p / "queue.json").write_text(json.dumps({"crops": crops, "full_frames": frames}))
    (p / "features.json").write_text(json.dumps({"status": "success", "model": MODEL, "features": features}))
    store = p.parent / "reviews"
    save_revision(p, revision(posture_package, events), store)
    splits = p.parent / "splits.json"
    splits.write_text(
        json.dumps({"sources": m["sources"], "assignments": {"1": "train", "2": "valid", "3": "test"}})
    )
    report = build_dataset(p, store, p.parent / "dataset", splits)
    assert report["training_allowed"] and report["verified_independent_labelled_groups"] == 2
    manifest = train(p.parent / "dataset/dataset.json", p.parent / "model", hidden=4)
    assert manifest["train_rows"] == 2 and manifest["valid_rows"] == 2 and manifest["test_used"] is False
    result = evaluate_saved(p.parent / "model", p.parent / "dataset/dataset.json", p.parent / "evaluation")
    assert np.sum(result["confusion_matrix"]) == 2
    assert result["day_night"]["day"]["class_counts"]["standing"] == 1
    assert result["unknown_detection_performance"] == "ölçülemedi"
    # Test data are absent; no successful empty test accuracy.
    result = evaluate_saved(
        p.parent / "model", p.parent / "dataset/dataset.json", p.parent / "test-evaluation", "test"
    )
    assert result["status"] == "ölçülemedi"
