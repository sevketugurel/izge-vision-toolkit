#!/usr/bin/env python3
"""CPU smoke demo: synthetic scene -> motion blobs -> ByteTrack-style tracker -> MOT metrics (demo origin)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.tracking_demo import main  # noqa: E402

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output-dir", type=Path, required=True)
p.add_argument("--seed", type=int, default=0)
a = p.parse_args()
result = main(a.output_dir, a.seed)
print(json.dumps({"origin": result["origin"], "metrics": result["metrics"]}, indent=2))
