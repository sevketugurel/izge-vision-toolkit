#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.posture_data import prepare_session  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
p.add_argument("--output-dir", type=Path, required=True)
a = p.parse_args()
print(json.dumps(prepare_session(a.package, a.output_dir), ensure_ascii=False, indent=2))
