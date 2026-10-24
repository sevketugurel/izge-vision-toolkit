"""Local-only dataset preparation CLI."""
import argparse
import json
import os
import sys
from pathlib import Path

from .inventory import inventory, read_inventory, save_inventory
from .manifest import load_manifest, split_inventory, write_manifest
from .validation import labels_needed, validate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inventory')
    scan.add_argument('--root', type=Path, default=os.environ.get('IZGE_DATA_ROOT'))
    scan.add_argument('--metadata', type=Path, help='Explicit JSON source metadata and annotation adapters')
    scan.add_argument('--output', type=Path, required=True)
    for command in ('validate', 'split', 'labels-needed'):
        sub = commands.add_parser(command)
        sub.add_argument('--inventory', type=Path, required=True)
        if command == 'split':
            sub.add_argument('--dataset', required=True)
            sub.add_argument('--version', required=True)
            sub.add_argument('--seed', type=int, default=42)
            sub.add_argument('--holdout-camera', action='append', default=['barn3'])
            sub.add_argument('--group-by', choices=['camera-day', 'source-video'], default='camera-day')
    gate = commands.add_parser('check-manifest')
    gate.add_argument('path', type=Path)
    gate.add_argument('--split', choices=['train', 'val', 'test'])
    gate.add_argument('--final-eval', action='store_true')
    gate.add_argument('--data-root', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            if not args.root:
                parser.error('--root or IZGE_DATA_ROOT required')
            value = inventory(Path(args.root), json.loads(args.metadata.read_text()) if args.metadata else None)
            save_inventory(args.output, value)
            result = dict(items=len(value['items']), errors=value['errors'], output=str(args.output))
        elif args.command == 'check-manifest':
            value = load_manifest(args.path, final_eval=args.final_eval, split=args.split, data_root=args.data_root)
            result = dict(items=len(value['items']), stats=value['stats'], sealed_test=value['sealed_test'])
        else:
            value = read_inventory(args.inventory)
            if args.command == 'validate':
                result = validate(value)
            elif args.command == 'labels-needed':
                result = labels_needed(value)
            else:
                manifest = split_inventory(value, args.seed, tuple(args.holdout_camera), args.group_by)
                path = write_manifest(manifest, args.dataset, args.version)
                result = dict(manifest=str(path), stats=manifest['stats'], sealed_test=True)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result.get('errors') else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
