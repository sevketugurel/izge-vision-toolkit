#!/usr/bin/env python3
"""Fetch only the pinned official Small checkpoint; never overwrite different weights."""
import argparse
import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / 'experiments/rfdetr_seg/model.lock.json'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(path: Path):
    model = json.loads(LOCK.read_text())
    if path.exists():
        if not path.is_file() or sha(path) != model['weights_sha256']:
            raise ValueError('Var olan ağırlık checksum eşleşmiyor; üzerine yazılmadı')
        return 'verified_existing'
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.part')
    created = False
    try:
        with partial.open('xb') as target:
            created = True
            with urllib.request.urlopen(model['weights_url'], timeout=60) as source:
                shutil.copyfileobj(source, target)
        if sha(partial) != model['weights_sha256']:
            raise ValueError('İndirilen ağırlık checksum eşleşmiyor')
        os.link(partial, path)  # Atomic no-overwrite, including another process creating the target.
    finally:
        # Only remove the partial file created by this call, never a pre-existing partial.
        if created:
            partial.unlink(missing_ok=True)
    return 'downloaded_verified'


def main():
    parser = argparse.ArgumentParser(description='Sabit resmî RF-DETR-Seg Small ağırlığını indir')
    parser.add_argument('--output', type=Path, default=Path('models/rfdetr/rf-detr-seg-small.pt'))
    args = parser.parse_args()
    try:
        print(fetch(args.output))
    except Exception:
        print('Model indirilemedi/doğrulanamadı; mevcut dosya ezilmedi. Erişim ve checksum kontrolü gerekli.')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
