#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.low_label_features import extract_features, select_queue  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
p.add_argument("--access", type=Path, required=True)
a = p.parse_args()
r = extract_features(a.package, a.access)
q = select_queue(a.package)
print(
    r["status"],
    r["features_extracted"],
    "features;",
    len(q["crops"]),
    "crops,",
    len(q["full_frames"]),
    "full frames",
)
