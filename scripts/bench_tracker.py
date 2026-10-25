#!/usr/bin/env python3
"""Tracker throughput on synthetic random-walk boxes (CPU). Measures tracker cost only, not detection."""
import argparse
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.tracking import ByteTrackLite  # noqa: E402


class Det:
    def __init__(self, box, score):
        self.box, self.score = box, score


p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--objects", type=int, default=20)
p.add_argument("--frames", type=int, default=2000)
a = p.parse_args()
rng = np.random.default_rng(0)
pos = rng.uniform(100, 2300, (a.objects, 2))
vel = rng.normal(0, 40, (a.objects, 2))
tracker, spent = ByteTrackLite(), 0.0
ids = set()
for i in range(a.frames):
    t = i * 0.1
    xy = pos + vel * t
    dets = [Det((x, y, x + 120, y + 80), 0.9) for x, y in xy]
    start = time.perf_counter()
    out = tracker.update(t, dets)
    spent += time.perf_counter() - start
    ids |= {o.tracklet_id for o in out}
print(json.dumps({"objects": a.objects, "frames": a.frames, "ms_per_update": round(1000 * spent / a.frames, 3),
                  "distinct_tracklet_ids": len(ids), "platform": platform.platform(), "python": platform.python_version()}))
