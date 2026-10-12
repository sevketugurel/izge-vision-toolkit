#!/usr/bin/env python3
"""Download a pinned Apache Seg candidate without touching existing mismatched files."""

import argparse
import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--lock", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
m = json.loads(a.lock.read_text())
if m["model_class"] not in {"RFDETRSegSmall", "RFDETRSegLarge"} or m["license"] != "Apache-2.0":
    raise ValueError("Explicit Apache Seg candidate required")


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


if a.output.exists():
    if sha(a.output) != m["weights_sha256"]:
        raise ValueError("Existing weight differs; not overwritten")
    print("verified_existing")
    raise SystemExit(0)
a.output.parent.mkdir(parents=True, exist_ok=True)
temp = a.output.with_name(a.output.name + ".part")
with temp.open("xb") as target:
    with urllib.request.urlopen(m["weights_url"], timeout=60) as source:
        shutil.copyfileobj(source, target)
if sha(temp) != m["weights_sha256"]:
    raise ValueError("Downloaded weight checksum mismatch; partial left for inspection")
os.link(temp, a.output)
temp.unlink()
print("downloaded_verified")
