#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.posture_data import build_dataset, refresh_features  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path)
p.add_argument("--review-store", type=Path)
p.add_argument("--splits", type=Path)
p.add_argument("--output-dir", type=Path, required=True)
p.add_argument("--refresh-dataset", type=Path)
p.add_argument("--access", type=Path)
p.add_argument("--max-new-features", type=int, default=6)
a = p.parse_args()
try:
    if a.refresh_dataset:
        if not a.access:
            p.error("--access required for bounded local feature refresh")
        result = refresh_features(a.refresh_dataset, a.access, a.output_dir, a.max_new_features)
    else:
        if not a.package:
            p.error("--package required")
        result = build_dataset(a.package, a.review_store, a.output_dir, a.splits)
    print(json.dumps(result, ensure_ascii=False, indent=2))
except (ValueError, KeyError, FileNotFoundError) as exc:
    print(f"Dataset rejected: {exc}", file=sys.stderr)
    sys.exit(2)
