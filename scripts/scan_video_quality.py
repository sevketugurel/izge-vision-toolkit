#!/usr/bin/env python3
"""Sample a video by PTS, compute sharpness/brightness/saturation and propose diverse annotation frames."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.frame_scan import scan_video  # noqa: E402

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("video", type=Path)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--hz", type=float, default=0.5)
p.add_argument("--select", type=int, default=12)
p.add_argument("--min-sharpness", type=float, default=0.0)
p.add_argument("--min-gap-s", type=float, default=0.0)
a = p.parse_args()
result = scan_video(a.video, a.hz, a.select, a.min_sharpness, a.min_gap_s)
a.output.write_text(json.dumps(result, indent=2))
print(json.dumps({k: result[k] for k in ("source", "appearance_counts", "selected_indices", "source_problems")}))
