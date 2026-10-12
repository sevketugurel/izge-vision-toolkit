#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from izge.vision.annotation_data import export_coco, validate_training_dataset  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Human-only COCO export / integrity gate; never trains')
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--annotations', type=Path)
    parser.add_argument('--splits', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--task', choices=['detection', 'segmentation'], default='segmentation')
    parser.add_argument('--validate-export', action='store_true')
    parser.add_argument('--image-selection', type=Path, help='Explicit JSON list for a smaller completely masked subset')
    args = parser.parse_args()
    try:
        if args.validate_export:
            result = validate_training_dataset(args.package, args.task)
        else:
            if None in (args.annotations, args.splits, args.output_dir):
                parser.error('Export needs --annotations, --splits and --output-dir')
            selection = json.loads(args.image_selection.read_text()) if args.image_selection else None
            result = export_coco(args.package, args.annotations, args.splits, args.output_dir,
                                 args.task == 'segmentation', selection)
        print(result)
    except (ValueError, OSError, KeyError) as exc:
        print(f'Etiket/veri kabulü geçmedi: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
