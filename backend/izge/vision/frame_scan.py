"""PTS-sampled quality scan of one video for annotation planning. Read-only; never modifies the source."""
from fractions import Fraction
from pathlib import Path

from ..domain import Frame, SourceGap
from ..video import FileVideoSource
from .frame_quality import frame_stats, select_diverse
from .timing import PTSSampler


def scan_video(path: Path, hz: float = 0.5, select: int = 12, min_sharpness: float = 0.0,
               min_gap_s: float = 0.0, max_samples: int = 10_000) -> dict:
    sampler, rows, gaps = PTSSampler(hz), [], []
    last_offset = None
    for event in FileVideoSource(path).read_events():
        if isinstance(event, SourceGap):
            gaps.append(event.reason)
            continue
        assert isinstance(event, Frame)
        if last_offset is not None and event.offset <= last_offset:
            gaps.append('non_monotonic_pts')
            continue
        last_offset = event.offset
        if not sampler.select(Fraction(event.pts) * event.time_base):
            continue
        rows.append({'pts': event.pts, 'time_base': str(event.time_base), 'offset_s': event.offset,
                     **frame_stats(event.image.to_ndarray(format='rgb24')).to_dict()})
        if len(rows) >= max_samples:
            gaps.append('max_samples_reached')
            break
    chosen = select_diverse(rows, select, min_sharpness, min_gap_s)
    appearance: dict = {}
    for r in rows:
        appearance[r['appearance']] = appearance.get(r['appearance'], 0) + 1
    return {'schema_version': 'frame-scan-1', 'source': path.name, 'sampling_hz': hz, 'samples': rows,
            'selected_indices': chosen, 'appearance_counts': appearance, 'source_problems': gaps,
            'note': 'Appearance is a pixel heuristic, not camera mode or clock evidence; sampling is by PTS bucket.'}
