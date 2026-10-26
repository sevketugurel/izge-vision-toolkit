#!/usr/bin/env python3
"""Quality audit of a COCO detection JSON (manual, model or pseudo labels). Exit 1 when problems are found."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.annotation_audit import audit_file  # noqa: E402

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("coco_json", type=Path)
p.add_argument("--image-root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
report = audit_file(a.coco_json, a.image_root)
text = json.dumps(report, indent=2, ensure_ascii=False)
if a.output:
    a.output.write_text(text)
print(text if not a.output else json.dumps({k: report[k] for k in ("images", "annotations", "origins", "problem_counts", "ok")}))
sys.exit(0 if report["ok"] else 1)
