#!/usr/bin/env python3
"""Score a tracker output against a hand-labelled MOTChallenge clip (MOTA, ID switches, IDF1, HOTA).

Both files are MOT txt: `frame,id,x,y,w,h,conf,...` (frame 1-based). Ground truth rows with conf 0 are ignored.
The JSON result records the input paths and the --data-origin label so synthetic runs cannot be mistaken
for real measurements.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.track_eval import score_mot_files  # noqa: E402

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--gt", required=True, type=Path, help="hand-labelled MOT txt")
p.add_argument("--pred", required=True, type=Path, help="tracker output MOT txt")
p.add_argument("--data-origin", required=True, choices=["synthetic", "manual"],
               help="'manual' only for a human-labelled real clip")
p.add_argument("--iou", type=float, default=0.5)
p.add_argument("--out", type=Path)
a = p.parse_args()
result = {"data_origin": a.data_origin, "gt": str(a.gt), "pred": str(a.pred),
          **score_mot_files(a.gt, a.pred, a.iou)}
text = json.dumps(result, indent=2, ensure_ascii=False)
if a.out:
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text + "\n", encoding="utf-8")
print(text)
