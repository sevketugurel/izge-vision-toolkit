#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from izge.vision.video_experiment import run_video_experiment  # noqa: E402


def main():
    p = argparse.ArgumentParser(description='Bounded real diagnostic inference; no behaviour or human labels')
    for name in ('source', 'config', 'model', 'output-dir'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--device', choices=['cpu', 'mps', 'auto'], default='mps')
    p.add_argument('--targets', help='Comma-separated PTS-normalised seconds; maximum 12')
    p.add_argument('--start', type=float, default=60.)
    p.add_argument('--duration', type=float, default=180.)
    p.add_argument('--period', type=float, default=20.)
    p.add_argument('--max-inferences', type=int, default=9)
    args = p.parse_args()
    targets = [float(x) for x in args.targets.split(',')] if args.targets else None
    result = run_video_experiment(args.source, args.config, args.model, args.output_dir,
                                  args.device, targets, args.start, args.duration, args.period, args.max_inferences)
    print(f'{result["status"]}: {len(result["frames"])} gerçek çıkarım; kaynak bütün dosya kabulü yok.')
    return 0 if result['status'] == 'success' else 2


if __name__ == '__main__':
    raise SystemExit(main())
