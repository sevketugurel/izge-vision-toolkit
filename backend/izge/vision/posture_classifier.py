"""CPU-only regularised MLP on frozen DINOv3-L; independent of V1 feeding."""

import io
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .artifact_io import checksum, prepare_output, write_json
from .posture_data import POSTURES, validate_backbone, validate_dataset, validate_vector


@dataclass(frozen=True)
class PostureMLP:
    backbone: dict
    mean: np.ndarray
    scale: np.ndarray
    weights_in: np.ndarray
    bias_hidden: np.ndarray
    weights_out: np.ndarray
    bias_out: np.ndarray

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        x = np.asarray(features, dtype=float)
        if x.ndim != 2 or x.shape[1] != 1024 or not np.isfinite(x).all():
            raise ValueError("Finite (N,1024) DINOv3-L features required")
        hidden = np.maximum(0, ((x - self.mean) / self.scale) @ self.weights_in + self.bias_hidden)
        z = (hidden @ self.weights_out + self.bias_out).ravel()
        # Stable binary sigmoid: column order standing, lying.
        p = np.empty_like(z)
        positive = z >= 0
        p[positive] = 1 / (1 + np.exp(-z[positive]))
        ez = np.exp(z[~positive])
        p[~positive] = ez / (1 + ez)
        return np.column_stack((1 - p, p))

    def to_bytes(self) -> bytes:
        meta = {
            "schema_version": "posture-mlp-1",
            "classes": list(POSTURES),
            "backbone": self.backbone,
            "activation": "relu",
            "feature_input": "RGB_square224_bilinear_ImageNet;CLS_L2",
        }
        buffer = io.BytesIO()
        np.savez(
            buffer,
            mean=self.mean,
            scale=self.scale,
            weights_in=self.weights_in,
            bias_hidden=self.bias_hidden,
            weights_out=self.weights_out,
            bias_out=self.bias_out,
            meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8),
        )
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "PostureMLP":
        with np.load(io.BytesIO(raw), allow_pickle=False) as f:
            meta = json.loads(f["meta"].tobytes())
            if (
                meta.get("schema_version") != "posture-mlp-1"
                or meta.get("classes") != list(POSTURES)
                or meta.get("activation") != "relu"
            ):
                raise ValueError("Posture model/classes incompatible")
            validate_backbone(meta["backbone"])
            arrays = [f[k] for k in ("mean", "scale", "weights_in", "bias_hidden", "weights_out", "bias_out")]
            mean, scale, wi, bh, wo, bo = arrays
            if (
                mean.shape != (1024,)
                or scale.shape != (1024,)
                or wi.ndim != 2
                or wi.shape[0] != 1024
                or bh.shape != (wi.shape[1],)
                or wo.shape != (wi.shape[1], 1)
                or bo.shape != (1,)
                or not all(np.isfinite(a).all() for a in arrays)
                or (scale <= 0).any()
            ):
                raise ValueError("Invalid posture model tensor shapes/values")
            return cls(meta["backbone"], *arrays)


def fit(
    features: np.ndarray,
    labels: list[str],
    backbone: dict,
    hidden: int = 16,
    alpha: float = 0.1,
    maximum_iterations: int = 500,
    seed: int = 42,
) -> tuple[PostureMLP, dict]:
    from sklearn.neural_network import MLPClassifier
    from sklearn.exceptions import ConvergenceWarning
    import warnings

    validate_backbone(backbone)
    if (
        features.ndim != 2
        or features.shape != (len(labels), 1024)
        or not np.isfinite(features).all()
        or set(labels) != set(POSTURES)
    ):
        raise ValueError("Both real posture classes and matching finite frozen features required")
    if not 1 <= hidden <= 64 or not np.isfinite(alpha) or alpha <= 0 or not 1 <= maximum_iterations <= 2000:
        raise ValueError("Bounded regularised MLP parameters required")
    mean, scale = features.mean(axis=0), features.std(axis=0)
    scale[scale < 1e-8] = 1
    estimator = MLPClassifier(
        hidden_layer_sizes=(hidden,),
        activation="relu",
        solver="lbfgs",
        alpha=alpha,
        max_iter=maximum_iterations,
        random_state=seed,
        early_stopping=False,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        estimator.fit((features - mean) / scale, [POSTURES.index(label) for label in labels])
    model = PostureMLP(
        backbone,
        mean,
        scale,
        estimator.coefs_[0],
        estimator.intercepts_[0],
        estimator.coefs_[1],
        estimator.intercepts_[1],
    )
    if not np.allclose(model.predict_proba(features), estimator.predict_proba((features - mean) / scale)):
        raise ValueError("Saved MLP probability contract differs from fitted estimator")
    return model, {
        "hidden": hidden,
        "alpha": alpha,
        "solver": "lbfgs",
        "maximum_iterations": maximum_iterations,
        "seed": seed,
        "iterations": int(estimator.n_iter_),
        "loss": float(estimator.loss_),
        "convergence_warning": any(issubclass(w.category, ConvergenceWarning) for w in caught),
        "standardisation": "train only",
        "device": "cpu",
        "backbone_frozen": True,
    }


def metrics(model: PostureMLP, rows: list[dict], threshold: float, unknown_review_items: int = 0) -> dict:
    from sklearn.metrics import classification_report, confusion_matrix

    if not 0.5 <= threshold < 1:
        raise ValueError("Fixed abstention threshold must be in [0.5,1)")
    if not rows:
        return {"status": "ölçülemedi", "labelled_samples": 0, "unknown_review_items": unknown_review_items}
    x = np.asarray([validate_vector(Path(r["feature_file"]), r["feature_sha256"]) for r in rows])
    p = model.predict_proba(x)
    y = np.asarray([POSTURES.index(r["label"]) for r in rows])
    pred = p.argmax(axis=1)
    accepted = p.max(axis=1) >= threshold
    report = {
        "status": "measured_on_small_human_split",
        "labelled_samples": len(rows),
        "class_counts": {c: sum(r["label"] == c for r in rows) for c in POSTURES},
        "classes": list(POSTURES),
        "confusion_matrix": confusion_matrix(y, pred, labels=[0, 1]).tolist(),
        "per_class": classification_report(
            y, pred, labels=[0, 1], target_names=POSTURES, output_dict=True, zero_division=0
        ),
        "abstention_threshold": threshold,
        "threshold_policy": "fixed a priori; not optimised on test",
        "abstained_samples": int((~accepted).sum()),
        "decision_coverage": float(accepted.mean()),
        "selective_confusion_matrix": confusion_matrix(y[accepted], pred[accepted], labels=[0, 1]).tolist(),
        "unknown_review_items": unknown_review_items,
        "unknown_detection_performance": "ölçülemedi",
        "source_groups": sorted({r["group_id"] for r in rows}),
        "sources": sorted({r["source_id"] for r in rows}),
        "caveat": "Tiny correlated samples; no field accuracy, tracking, persistent identity or duration claim",
    }
    return report


def evaluate(model: PostureMLP, data: dict, split: str, threshold: float) -> dict:
    rows = [r for r in data["rows"] if r["split"] == split]
    result = metrics(
        model,
        rows,
        threshold,
        sum(r.get("label") == "unknown" and r.get("split") == split for r in data["unresolved"]),
    )
    result["day_night"] = {
        light: metrics(model, [r for r in rows if r["light"] == light], threshold)
        for light in ("day", "night")
    }
    result["split"] = split
    return result


def train(
    dataset: Path,
    output: Path,
    threshold: float = 0.6,
    hidden: int = 16,
    alpha: float = 0.1,
    maximum_iterations: int = 500,
) -> dict:
    data = validate_dataset(dataset)
    if data["blockers"]:
        raise ValueError("Training gate closed: " + "; ".join(data["blockers"]))
    if not 0.5 <= threshold < 1:
        raise ValueError("Fixed abstention threshold must be in [0.5,1)")
    rows = [r for r in data["rows"] if r["split"] == "train"]
    x = np.asarray([validate_vector(Path(r["feature_file"]), r["feature_sha256"]) for r in rows])
    model, params = fit(x, [r["label"] for r in rows], data["model"], hidden, alpha, maximum_iterations)
    prepare_output(output)
    path = output / "posture_mlp.npz"
    path.write_bytes(model.to_bytes())
    reloaded = PostureMLP.from_bytes(path.read_bytes())
    if not np.allclose(model.predict_proba(x), reloaded.predict_proba(x)):
        raise ValueError("Model save/load probability contract mismatch")
    validation = evaluate(reloaded, data, "valid", threshold)
    write_json(output / "validation.json", validation)
    report = {
        "schema_version": "posture-training-run-1",
        "status": "trained_experimental_baseline",
        "origin": "model",
        "dataset_file": str(dataset.resolve()),
        "dataset_sha256": checksum(dataset),
        "review_revisions": data["review_revisions"],
        "feature_model": data["model"],
        "params": params,
        "train_rows": len(rows),
        "valid_rows": sum(r["split"] == "valid" for r in data["rows"]),
        "training_groups": sorted({r["group_id"] for r in rows}),
        "test_used": False,
        "model_sha256": checksum(path),
        "validation_sha256": checksum(output / "validation.json"),
        "abstention_threshold": threshold,
        "roi_verified": False,
        "production_accepted": False,
        "features_sha256": {r["cache_key"]: r["feature_sha256"] for r in data["rows"]},
        "environment": {
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "scikit-learn": version("scikit-learn"),
        },
    }
    write_json(output / "manifest.json", report)
    return report


def evaluate_saved(model_dir: Path, dataset: Path, output: Path, split: str = "valid") -> dict:
    if split not in ("valid", "test"):
        raise ValueError("Independent valid/test evaluation only")
    proof = json.loads((model_dir / "manifest.json").read_text())
    if (
        checksum(model_dir / "posture_mlp.npz") != proof["model_sha256"]
        or checksum(dataset) != proof["dataset_sha256"]
    ):
        raise ValueError("Model/dataset evaluation provenance mismatch")
    data = validate_dataset(dataset)
    if data["blockers"]:
        raise ValueError("Evaluation gate closed: " + "; ".join(data["blockers"]))
    model = PostureMLP.from_bytes((model_dir / "posture_mlp.npz").read_bytes())
    if model.backbone != data["model"]:
        raise ValueError("Model/feature mismatch")
    result = evaluate(model, data, split, proof["abstention_threshold"])
    prepare_output(output)
    write_json(output / "evaluation.json", result)
    return result
