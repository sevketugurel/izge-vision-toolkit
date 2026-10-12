#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from izge.vision.data_preparation import prepare_pilot  # noqa: E402


def main():
    p = argparse.ArgumentParser(description='Prepare unlabelled PTS pilot and camera-specific draft previews')
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    result = prepare_pilot(args.inventory, args.review, args.output_dir)
    print(f'{len(result["images"])} etiketsiz kare, {len(result["clips"])} bağlam klibi; split kabulü bekliyor.')


if __name__ == '__main__':
    main()
