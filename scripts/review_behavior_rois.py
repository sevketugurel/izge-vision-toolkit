#!/usr/bin/env python3
"""Separate draft renderer: preflight still draws verified geometry only."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from izge.vision.roi_review import review_rois  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='İnsan doğrulaması olmayan ROI taslağını önizle')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--notes', type=Path)
    args = parser.parse_args()
    try:
        result = review_rois(args.source, args.config, args.output_dir, args.notes)
    except Exception:
        print('ROI taslağı üretilemedi: kaynak/config geçersiz veya çıktı klasörü dolu.', file=sys.stderr)
        return 2
    print(f'TASLAK — İNSAN DOĞRULAMASI YOK: {len(result["samples"])} önizleme; ROI analizi kapalı')
    return 0 if result['status'] == 'draft' else 2


if __name__ == '__main__':
    raise SystemExit(main())
