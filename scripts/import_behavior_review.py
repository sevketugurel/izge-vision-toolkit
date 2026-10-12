#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.proposal_review import save_revision, human_bridge  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
p.add_argument("--review", type=Path, required=True)
p.add_argument("--store", type=Path, required=True)
p.add_argument("--human-bridge", type=Path)
a = p.parse_args()
raw = a.review.read_bytes()
d = json.loads(raw)
out = save_revision(a.package, d, a.store, raw)
if a.human_bridge:
    human_bridge(a.package, d, a.human_bridge)
print(out, "saved; training remains independently gated")
