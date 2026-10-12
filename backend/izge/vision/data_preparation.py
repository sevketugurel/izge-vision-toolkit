"""Bounded PTS frame/clip preparation; annotations deliberately remain empty."""
import json
import math
from fractions import Fraction
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from ..domain import Frame, SourceGap
from ..video import FileVideoSource
from .config import load
from .inspection import prepare_output, write_json
from .preflight import checksum
from .roi_review import render_draft


def frame_at(reader: FileVideoSource, target: float) -> Frame:
    events = reader.read_events(start=target)
    try:
        for event in events:
            if isinstance(event, SourceGap):
                raise ValueError(f'Source gap: {event.reason}')
            if event.offset >= target:
                return event
    finally:
        events.close()
    raise ValueError('Target timestamp unavailable')


def pilot_targets(duration: float, count: int) -> list[float]:
    if not math.isfinite(duration) or duration <= 0 or not 4 <= count <= 200:
        raise ValueError('Invalid duration or bounded sample budget')
    end = max(0., duration - min(1., duration/100))
    points = {round(end*i/(count-1), 6) for i in range(count)}
    # Include the user's initial review point without adding to the budget.
    if end > 60:
        closest = min((x for x in points if x not in (0., end)), key=lambda x: abs(x-60))
        points.remove(closest)
        points.add(60.)
    return sorted(points)


def contact_sheet(items: list[dict], folder: Path, destination: Path, title: str):
    width, height, columns = 640, 400, 3
    canvas = Image.new('RGB', (width*columns, 48+height*((len(items)+columns-1)//columns)), '#171717')
    draw = ImageDraw.Draw(canvas)
    font_path = Path('/System/Library/Fonts/Supplemental/Arial.ttf')
    font = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default()
    draw.text((12, 10), title, font=font, fill='white')
    for i, item in enumerate(items):
        x, y = i % columns * width, 48+i//columns*height
        with Image.open(folder / item['file']) as image:
            canvas.paste(image.resize((640, 360)), (x, y+36))
        draw.text((x+8, y+5), f'{item["id"]} | PTS {item["pts"]} | {item["offset_s"]:.3f}s',
                  font=font, fill='white')
    canvas.save(destination)


def context_clip(reader: FileVideoSource, start: float, end: float, destination: Path) -> dict:
    """Lossless FFV1/Matroska context with source PTS sidecar; no x264 encoder."""
    import av
    frames = reader.read_events(start=start)
    first, last, count = None, None, 0
    with av.open(str(destination), 'w', format='matroska') as out, destination.with_suffix('.pts.jsonl').open('x') as sidecar:
        stream = out.add_stream('ffv1')
        stream.width, stream.height, stream.pix_fmt = 960, 540, 'bgr0'
        stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
        try:
            for frame in frames:
                if isinstance(frame, SourceGap):
                    raise ValueError('Context decode gap')
                if frame.offset >= end:
                    break
                if last is not None and frame.offset <= last:
                    raise ValueError('Context nonmonotonic PTS')
                first = frame.offset if first is None else first
                image = av.VideoFrame.from_image(frame.image.to_image().resize((960, 540))).reformat(format='bgr0')
                image.pts = round((frame.offset-first)*1000)
                image.time_base = Fraction(1, 1000)
                for packet in stream.encode(image):
                    out.mux(packet)
                sidecar.write(json.dumps({'source_pts': frame.pts, 'source_time_base': str(frame.time_base),
                                          'source_offset_s': frame.offset, 'output_pts': image.pts,
                                          'output_time_base': '1/1000'})+'\n')
                last, count = frame.offset, count+1
            for packet in stream.encode():
                out.mux(packet)
        finally:
            frames.close()
    if count == 0:
        raise ValueError('Empty context clip')
    return {'file': str(destination.name), 'pts_sidecar': destination.with_suffix('.pts.jsonl').name,
            'requested_start_s': start, 'requested_end_s': end, 'first_source_offset_s': first,
            'last_source_offset_s': last, 'frames': count, 'sha256': checksum(destination),
            'encoding': 'FFV1 lossless Matroska, resized 960x540, original PTS sidecar',
            'behavior_event_verified': False, 'reason': 'head_motion_and_posture_context_review_candidate'}


def prepare_pilot(inventory_path: Path, review_path: Path, output: Path) -> dict:
    inventory = json.loads(inventory_path.read_text())
    review = yaml.safe_load(review_path.read_text())['sources']
    root = Path(inventory['root'])
    if sum(r.get('pilot_frames', 0) for r in review.values()) > 200:
        raise ValueError('Pilot exceeds 200 frame budget')
    prepare_output(output)
    for name in ('images', 'contact-sheets', 'roi-review', 'clips'):
        (output / name).mkdir()
    selected, sources, clips, visual_records = [], [], [], []
    for original in inventory['sources']:
        spec = review.get(original['path'])
        if spec is None or original['kind'] != 'video':
            continue
        row = dict(original)
        row.update({'observed_camera_label': spec['camera_label'], 'camera_id': spec['camera_id'],
                    'observed_light': spec['light'], 'camera_position_revision': spec['position_revision'],
                    'observation_origin': 'visual_review_not_human_annotation',
                    'session_id': row['family_id'], 'split': 'unassigned',
                    'recording_day_verified': False,
                    'recording_day_verification': None,
                    'leakage_group': 'unverified-recording-day-pool',
                    'reserved_test_candidate': spec['camera_id'] == 'barn3'})
        sources.append(row)
        if not spec.get('canonical'):
            continue
        path = root / row['path']
        if checksum(path) != row['sha256']:
            raise ValueError('Source changed since inventory')
        reader = FileVideoSource(path)
        duration = row['metadata']['video_stream_duration_s']
        points = pilot_targets(duration, spec['pilot_frames'])
        source_items = []
        config = load(Path(spec['roi_config']))
        if config.camera_id != spec.get('roi_camera_id',spec['camera_id']):
            raise ValueError('ROI draft belongs to a different camera')
        indices = sorted({round(i*(len(points)-1)/8) for i in range(9)})
        for i, target in enumerate(points):
            frame = frame_at(reader, target)
            if frame.offset-target > 1:
                raise ValueError('Seek landed too far from requested PTS')
            name = f'{row["id"]}-{i+1:03d}'
            image_path = output / 'images' / f'{name}.png'
            frame.image.to_image().save(image_path)
            item = {'id': name, 'source_id': row['id'], 'session_id': row['session_id'],
                    'family_id': row['family_id'], 'camera_id': row['camera_id'],
                    'camera_position_revision': spec['position_revision'], 'light_observation': spec['light'],
                    'recording_day': None, 'clock_verified': False, 'split': 'unassigned',
                    'leakage_group': row['leakage_group'], 'reserved_test_candidate': row['reserved_test_candidate'],
                    'file': f'images/{name}.png', 'sha256': checksum(image_path),
                    'width': frame.width, 'height': frame.height, 'pts': frame.pts,
                    'time_base': str(frame.time_base), 'offset_s': frame.offset, 'target_offset_s': target,
                    'selection_reason': 'PTS_stratified_start_middle_end_and_60s; human_strata_pending',
                    'mask_subset_candidate': i in indices[:6], 'annotation_status': 'unlabeled'}
            selected.append(item)
            source_items.append(item)
            if i in indices:
                preview = output / 'roi-review' / f'{name}-roi.png'
                render_draft(frame.image.to_image(), config, frame.pts, frame.offset).save(preview)
                visual_records.append({**item, 'roi_preview': str(preview.relative_to(output)),
                                       'geometry_verification': None, 'roi_verified': False,
                                       'roi_analysis_allowed': False, 'config_path': spec['roi_config'],
                                       'config_file_sha256': checksum(Path(spec['roi_config']))})
        contact_sheet([source_items[i] for i in indices], output,
                      output / 'contact-sheets' / f'{row["id"]}.jpg',
                      f'{spec["camera_label"]} | {spec["light"]} | RAW REVIEW; NO HUMAN LABELS')
        if duration > 30:
            for n, center in enumerate((60., duration/2), 1):
                destination = output / 'clips' / f'{row["id"]}-{n}.mkv'
                clip = context_clip(reader, max(0, center-6), min(duration, center+6), destination)
                clips.append({**clip, 'source_id': row['id'], 'session_id': row['session_id'],
                              'camera_id': row['camera_id'], 'split': 'unassigned',
                              'leakage_group': row['leakage_group'], 'annotation_status': 'unlabeled'})
        row['sha256_after'] = checksum(path)
        row['unchanged'] = row['sha256_after'] == row['sha256']
        if not row['unchanged']:
            raise ValueError('Source changed during preparation')
    manifest = {'schema_version': 'behavior-annotation-pilot-1', 'sources': sources, 'images': selected,
                'clips': clips, 'roi_reviews': visual_records, 'human_annotations': 0,
                'roi_verified': False, 'roi_analysis_allowed': False, 'experiment_only': True,
                'split_status': 'blocked_unknown_recording_days_and_overlaps',
                'locked_test_candidates': [r['id'] for r in sources if r['reserved_test_candidate']],
                'inventory_sha256': checksum(inventory_path), 'review_spec_sha256': checksum(review_path)}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'annotations.template.json', {
        'schema_version': 'behavior-human-annotations-1',
        'frames': [{'image_id': item['id'], 'status': 'unlabeled', 'origin': None, 'annotator': None,
                    'annotated_at': None, 'objects': []} for item in selected],
        'events': [{'clip_file': clip['file'], 'status': 'unlabeled', 'events': []} for clip in clips]})
    return manifest
