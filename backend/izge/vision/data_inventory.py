"""Read-only source inventory; file names are hints, never clock verification."""
import re
from pathlib import Path

from PIL import Image

from ..domain import Frame
from ..video import FileVideoSource
from .inspection import prepare_output, write_json
from .preflight import checksum

MEDIA_EXTENSIONS = {'.mp4', '.dav', '.png', '.jpg', '.jpeg'}


def source_family(path: Path, digest: str) -> str:
    match = re.match(r'^(\d{14,17})', path.stem)
    return f'filename-family-{match[1]}' if match else f'file-{digest[:16]}'


def inventory_sources(root: Path, output: Path) -> dict:
    prepare_output(output)
    records = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        digest = checksum(path)
        video = path.suffix.lower() in {'.mp4', '.dav'}
        row = {'id': f'src-{digest[:16]}', 'path': str(path.relative_to(root)),
               'bytes': path.stat().st_size, 'sha256': digest, 'sha256_after': None,
               'kind': 'video' if video else 'reference_or_qc_image',
               'family_id': source_family(path, digest), 'family_evidence': 'filename_hint_only',
               'derivative_equivalence_verified': False, 'recorded_at': None,
               'recording_day': None, 'clock_verified': False,
               'filename_time_hint': re.match(r'^(\d{14,17})', path.stem)[1]
                                     if re.match(r'^(\d{14,17})', path.stem) else None,
               'observed_camera_label': None, 'camera_position_revision': None,
               'observed_light': None, 'visual_review': [],
               'readability': 'unavailable', 'errors': []}
        if any(r['id'] == row['id'] for r in records):
            row['id'] += f'-copy-{len(records)}'
        try:
            if video:
                reader = FileVideoSource(path)
                row['metadata'] = reader.metadata()
                events = reader.read_events()
                try:
                    first = next((e for e in events if isinstance(e, Frame)), None)
                    if first is None:
                        raise ValueError('No readable timestamped frame')
                    row['first_frame'] = {'pts': first.pts, 'time_base': str(first.time_base),
                                          'offset_s': first.offset}
                    first.image.to_image().resize((640, 360)).save(output / f'{row["id"]}_first.jpg')
                    row['readability'] = 'metadata_and_first_frame_only'
                finally:
                    events.close()
            else:
                with Image.open(path) as im:
                    row['metadata'] = {'width': im.width, 'height': im.height, 'format': im.format}
                    im.verify()
                row['readability'] = 'image_readable'
        except Exception:
            row['errors'].append('metadata_or_decode_unavailable')
        row['sha256_after'] = checksum(path)
        row['unchanged'] = digest == row['sha256_after']
        records.append(row)
    manifest = {'schema_version': 'behavior-source-inventory-1', 'root': str(root),
                'sources': records, 'recording_days_verified': False,
                'split_status': 'blocked_pending_recording_day_and_session_verification',
                'notes': ['Filename/export date is not recorded_at.',
                          'Same filename family is conservatively kept together; equivalence not asserted.',
                          'Full-file decode not implied by first-frame readability.']}
    write_json(output / 'inventory.json', manifest)
    return manifest
