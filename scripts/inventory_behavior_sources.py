#!/usr/bin/env python3
"""Inventory local sources without changing originals."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from izge.vision.data_inventory import inventory_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = inventory_sources(args.source_dir, args.output_dir)
    print(f'{len(result["sources"])} kaynak envantere alındı; kayıt saatleri doğrulanmadı.')


if __name__ == '__main__':
    main()
