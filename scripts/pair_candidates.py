#!/usr/bin/env python3
"""Rank pair-interaction candidates from tracklets and write a review-queue JSON.

Input JSON: {"source_sha256": "...", "observations": [{"tracklet_id": 1, "offset_s": 0.1, "box": [x1,y1,x2,y2]}, ...]}
with PTS-derived offsets. `--data-origin` is required so synthetic runs cannot pass as real measurements.
Candidates are origin=model review prompts; they make no identity or behaviour-duration claim.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.pair_candidates import (  # noqa: E402
    Observation, PairConfig, build_review_queue, find_pair_candidates)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--tracks", required=True, type=Path)
p.add_argument("--data-origin", required=True, choices=["synthetic", "real"])
p.add_argument("--out", required=True, type=Path)
p.add_argument("--near-distance", type=float, default=PairConfig.near_distance)
p.add_argument("--max-gap-s", type=float, default=PairConfig.max_gap_s)
p.add_argument("--min-span-s", type=float, default=PairConfig.min_span_s)
p.add_argument("--min-samples", type=int, default=PairConfig.min_samples)
a = p.parse_args()

data = json.loads(a.tracks.read_text(encoding="utf-8"))
sha = str(data.get("source_sha256", ""))
if not re.fullmatch(r"[0-9a-f]{64}", sha):
    sys.exit("source_sha256 must be a 64-char lowercase hex digest")
obs = [Observation(int(o["tracklet_id"]), float(o["offset_s"]), tuple(float(v) for v in o["box"]))
       for o in data["observations"]]
config = PairConfig(a.near_distance, a.max_gap_s, a.min_span_s, a.min_samples)
queue = build_review_queue(sha, find_pair_candidates(obs, config), config, a.data_origin)
a.out.parent.mkdir(parents=True, exist_ok=True)
a.out.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{len(queue['candidates'])} candidate(s) -> {a.out} (data_origin={a.data_origin})")
