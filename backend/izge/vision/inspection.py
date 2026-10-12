"""Shared local inspection helpers; raw frame selection always uses decoded PTS."""
import json
import subprocess
from fractions import Fraction
from pathlib import Path

from ..domain import Frame
from ..video import FileVideoSource
from .preflight import checksum
from .timing import TimingDiagnostics


def prepare_output(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Çıktı klasörü yeni veya boş olmalı')
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write('\n')


def code_provenance() -> dict:
    root = Path(__file__).resolve().parents[3]
    names = ['backend/izge/domain.py', 'backend/izge/video.py',
             'scripts/inspect_behavior_detection.py', 'scripts/review_behavior_rois.py',
             'scripts/fetch_behavior_model.py', 'experiments/rfdetr_seg/audit_environment.py',
             *[f'backend/izge/vision/{n}.py' for n in
               ('config', 'geometry', 'timing', 'preflight', 'inspection', 'roi_review', 'detector',
                'detection_inspection')]]
    try:
        commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True,
                                capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = bool(subprocess.run(['git', '-C', str(root), 'status', '--porcelain'], check=True,
                                   capture_output=True, text=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError):
        commit, dirty = None, None
    return {'commit': commit, 'working_tree_dirty': dirty,
            'files_sha256': {n: checksum(root / n) for n in names if (root / n).is_file()}}


def reference_frames(reader: FileVideoSource, config, metadata: dict):
    """Two passes, four bounded frame references, no seek/frame-count/FPS arithmetic."""
    base = metadata['time_base']
    origin = metadata['normalization_origin_pts'] * Fraction(base['numerator'], base['denominator'])

    def diagnostic():
        return TimingDiagnostics(config.sampling.detection_hz, config.sampling.posture_hz,
                                 config.sampling.source_gap_threshold_s, origin)

    diag = diagnostic()
    for event in reader.read_events():
        if isinstance(event, Frame) and (event.width, event.height) != (metadata['width'], metadata['height']):
            raise ValueError('Akış içinde çözünürlük değişti')
        diag.consume(event)
    timing = diag.summary(metadata['video_stream_end_offset_s'])
    if timing['status'] not in ('complete', 'warning') or diag.first_time is None:
        raise ValueError('Geçerli ve tutarlı kaynak zaman çizelgesi gerekli')
    targets = [diag.first_time + (diag.last_time - diag.first_time) * Fraction(i, 3) for i in range(4)]
    nearest, distances = [None] * 4, [None] * 4
    second = diagnostic()
    for event in reader.read_events():
        row = second.consume(event)
        if not isinstance(event, Frame) or row.source_status not in ('valid', 'timestamp_discontinuity'):
            continue
        at = event.pts * event.time_base - origin
        for i, target in enumerate(targets):
            distance = abs(at - target)
            if distances[i] is None or distance < distances[i]:
                nearest[i], distances[i] = event, distance
    if second.summary(metadata['video_stream_end_offset_s']) != timing:
        raise ValueError('Tekrar çözme zaman çizelgesi tutarsız')
    return nearest, targets, timing
