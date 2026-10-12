#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.low_label import comparison, prepare_candidates  # noqa: E402

p = argparse.ArgumentParser(description="Eight matched raw AHIR2 frames; held-out AHIR3 excluded")
p.add_argument("--pilot", type=Path, required=True)
p.add_argument("--source-dir", type=Path, required=True)
p.add_argument("--output-dir", type=Path, required=True)
p.add_argument("--device", choices=["mps", "cpu", "auto"], default="mps")
a = p.parse_args()
r = comparison(
    a.pilot,
    a.source_dir,
    a.output_dir,
    [Path("experiments/rfdetr_seg/model.lock.json"), Path("experiments/rfdetr_seg/large.model.lock.json")],
    [Path("models/rfdetr/rf-detr-seg-small.pt"), Path("models/rfdetr/rf-detr-seg-large.pt")],
    a.device,
)
prepare_candidates(a.output_dir)
print([(x["model_class"], x["status"], [f["count"] for f in x["frames"]]) for x in r["runs"]])
raise SystemExit(0 if all(x["status"] == "success" for x in r["runs"]) else 2)
