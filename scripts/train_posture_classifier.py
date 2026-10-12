#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.posture_classifier import evaluate_saved, train  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--dataset", type=Path, required=True)
p.add_argument("--output-dir", type=Path, required=True)
p.add_argument("--evaluate-model", type=Path)
p.add_argument("--split", choices=("valid", "test"), default="valid")
p.add_argument("--hidden", type=int, default=16)
p.add_argument("--alpha", type=float, default=0.1)
p.add_argument("--max-iterations", type=int, default=500)
p.add_argument("--abstain-threshold", type=float, default=0.6)
a = p.parse_args()
try:
    result = (
        evaluate_saved(a.evaluate_model, a.dataset, a.output_dir, a.split)
        if a.evaluate_model
        else train(a.dataset, a.output_dir, a.abstain_threshold, a.hidden, a.alpha, a.max_iterations)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
except (ValueError, KeyError, FileNotFoundError) as exc:
    print(f"Posture training/evaluation rejected: {exc}", file=sys.stderr)
    sys.exit(2)
