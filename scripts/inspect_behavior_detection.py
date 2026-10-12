#!/usr/bin/env python3
"""Run with experiments/rfdetr_seg/.venv/bin/python; never mutates the main environment."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from izge.vision.detection_inspection import inspect_detection  # noqa: E402
from izge.vision.detector import MODES  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='RF-DETR Seg Small gerçek tespit deneyi')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--model', required=True, type=Path, help='model.lock.json ile eşleşen resmî ağırlık')
    parser.add_argument('--device', choices=('cpu', 'mps', 'cuda', 'auto'), default='auto')
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--threshold', type=float, default=.5)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = inspect_detection(args.source, args.config, args.model, args.output_dir,
                                   args.device, args.mode, args.threshold)
    except Exception:
        print('Deney başlatılamadı: çıktı klasörü yeni veya boş olmalı.', file=sys.stderr)
        return 2
    print(f'Durum: {result["status"]}; model_loaded={result["model_loaded"]}; '
          f'roi_analysis_allowed={result["roi_analysis_allowed"]}')
    for error in result['errors']:
        print(f'Hata kodu: {error["code"]}', file=sys.stderr)
    for frame in result['frames']:
        print(f'PTS {frame["pts"]}: {frame["count"]} model tespiti; {frame["inference_seconds"]:.3f} sn')
    return 0 if result['status'] == 'success' else 2


if __name__ == '__main__':
    raise SystemExit(main())
