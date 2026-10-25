"""Read-only inventory with explicit provenance and ffprobe metadata."""
import json
import math
import subprocess
from pathlib import Path

from PIL import Image

from ..vision.artifact_io import checksum, write_json
from ..vision.data_inventory import source_family

VIDEOS = {'.mp4', '.mkv', '.avi', '.mov', '.dav', '.h264'}
IMAGES = {'.png', '.jpg', '.jpeg'}
LABELS = {'.json', '.jsonl', '.csv', '.tsv'}


def probe(path: Path) -> dict:
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-select_streams', 'v:0',
        '-show_streams', '-show_format', '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=60, check=True)
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    # Report the rate only. Never reconstruct PTS or frame counts from nominal FPS.
    duration = float(stream.get('duration') or data['format']['duration'])
    if not math.isfinite(duration) or duration <= 0 or stream['width'] <= 0 or stream['height'] <= 0:
        raise ValueError('Invalid video metadata')
    return dict(width=stream['width'], height=stream['height'],
                fps=stream.get('avg_frame_rate'), time_base=stream.get('time_base'),
                duration=duration)


def inventory(root: Path, metadata: dict | None = None) -> dict:
    root = root.resolve(strict=True)
    metadata = metadata or {}
    records, errors = [], []
    for path in sorted(root.rglob('*')):
        if path.suffix.lower() not in VIDEOS | IMAGES | LABELS or not path.is_file():
            continue
        if any(part in {'manifests', '.dvc', '.git'} for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        try:
            if not path.resolve().is_relative_to(root):
                raise ValueError('External symlink')
            digest = checksum(path)
            kind = 'video' if path.suffix.lower() in VIDEOS else (
                'frame' if path.suffix.lower() in IMAGES else 'label')
            row = dict(path=relative, id=relative, kind=kind, size=path.stat().st_size,
                       sha256=digest, camera=None, day=None, light='unknown',
                       source_video=relative if kind == 'video' else None,
                       family_id=source_family(path, digest), session_id=source_family(path, digest),
                       recording_day_verified=False)
            info = metadata.get('sources', {}).get(relative, {})
            for key in ('camera', 'day', 'light', 'source_video', 'family_id', 'session_id',
                        'recording_day_verified', 'recording_day_verification', 'recording_days',
                        'overlap_groups', 'label_path', 'distance', 'visibility'):
                if key in info:
                    row[key] = info[key]
            row['recording_day'] = row['day']
            if kind == 'video':
                row['media'] = probe(path)
            elif kind == 'frame':
                with Image.open(path) as image:
                    row['media'] = dict(width=image.width, height=image.height)
                    image.verify()
            elif path.suffix.lower() == '.json':
                json.loads(path.read_text())
            records.append(row)
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
            errors.append(dict(path=relative, error='unreadable_or_invalid_metadata'))
    return dict(schema_version='izge-inventory-1', root=str(root), items=records,
                annotations=metadata.get('annotations', []), errors=errors,
                note='Metadata readability only; full video decode and recording clock not verified.')


def save_inventory(path: Path, value: dict) -> None:
    if path.suffix == '.parquet':
        import pyarrow as pa
        import pyarrow.parquet as pq
        # One self-contained inventory per row preserves annotations/errors alongside media.
        if path.exists():
            raise ValueError('Output exists')
        pq.write_table(pa.table({'inventory_json': [json.dumps(value, allow_nan=False)]}), path)
    else:
        write_json(path, value)


def read_inventory(path: Path) -> dict:
    if path.suffix == '.parquet':
        import pyarrow.parquet as pq
        return json.loads(pq.read_table(path)['inventory_json'][0].as_py())
    return json.loads(path.read_text())
