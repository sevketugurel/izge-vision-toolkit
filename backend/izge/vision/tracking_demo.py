"""Synthetic tracking demo: generated video (never committed) -> PTS-timed frames -> motion blobs -> tracker.

Everything here is SIMULATED data (`origin` = demo): rectangles on a flat background. Metrics describe
the tracker plumbing on this toy scene only and say nothing about cattle accuracy.
"""
import json
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from ..domain import Frame
from ..video import FileVideoSource
from .motion_baseline import MotionBlobDetector
from .track_eval import evaluate_tracking
from .tracking import ByteTrackLite, TrackerConfig

WIDTH, HEIGHT, BACKGROUND, OBJECT_VALUE = 320, 180, 60, 200
# id -> (x0, y0, vx, vy, w, h, start_s); positions are functions of PTS time, not of frame index.
OBJECTS = {1: (-100.0, 40.0, 80.0, 0.0, 32, 24, 0.0), 2: (420.0, 50.0, -80.0, 0.0, 32, 24, 0.0),
           3: (150.0, -100.0, 0.0, 50.0, 28, 28, 0.0)}
OCCLUDER = (140, 90, 190, 130)   # x1, y1, x2, y2: object 3 vanishes behind it (drawn in background colour)


def timestamps_ms(duration_s: float = 8.0, seed: int = 0) -> list[int]:
    """Irregular, strictly increasing frame times (~10 fps with jitter), as in a variable-rate NVR export."""
    rng = np.random.default_rng(seed)
    t, out = 0, []
    while t < duration_s * 1000:
        out.append(t)
        t += int(rng.integers(70, 131))
    return out


def object_box(oid: int, t_s: float) -> tuple[float, float, float, float]:
    x0, y0, vx, vy, w, h, start = OBJECTS[oid]
    x, y = x0 + vx * (t_s - start), y0 + vy * (t_s - start)
    return (x, y, x + w, y + h)


def render(t_s: float) -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), BACKGROUND, np.uint8)
    for oid in OBJECTS:
        x1, y1, x2, y2 = (int(round(v)) for v in object_box(oid, t_s))
        image[max(y1, 0):max(min(y2, HEIGHT), 0), max(x1, 0):max(min(x2, WIDTH), 0)] = OBJECT_VALUE
    ox1, oy1, ox2, oy2 = OCCLUDER
    image[oy1:oy2, ox1:ox2] = BACKGROUND
    return image


def visible_truth(t_s: float) -> dict[int, tuple[float, float, float, float]]:
    out = {}
    for oid in OBJECTS:
        x1, y1, x2, y2 = object_box(oid, t_s)
        cx1, cy1, cx2, cy2 = max(x1, 0), max(y1, 0), min(x2, WIDTH), min(y2, HEIGHT)
        area, full = max(cx2 - cx1, 0) * max(cy2 - cy1, 0), (x2 - x1) * (y2 - y1)
        occ = max(min(cx2, OCCLUDER[2]) - max(cx1, OCCLUDER[0]), 0) * max(min(cy2, OCCLUDER[3]) - max(cy1, OCCLUDER[1]), 0)
        if full > 0 and (area - occ) / full >= 0.5:
            out[oid] = (cx1, cy1, cx2, cy2)
    return out


def write_video(path: Path, times_ms: list[int]) -> Path:
    """Lossless FFV1/Matroska with 1/1000 time base and the exact irregular PTS."""
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('ffv1')
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, 'yuv420p'
        stream.codec_context.time_base = stream.time_base = Fraction(1, 1000)
        for ms in times_ms:
            frame = av.VideoFrame.from_ndarray(render(ms / 1000), format='rgb24').reformat(format='yuv420p')
            frame.pts, frame.time_base = ms, stream.time_base
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return path


def run(video: Path, config: TrackerConfig = TrackerConfig(), truth_fn=visible_truth) -> dict:
    detector, tracker = MotionBlobDetector(), ByteTrackLite(config)
    truth, predicted, tracks = [], [], {}
    for event in FileVideoSource(video).read_events():
        if not isinstance(event, Frame):
            raise ValueError('Demo video must decode cleanly')
        rgb = event.image.to_ndarray(format='rgb24')
        detections = detector.detect(rgb)
        observed = tracker.update(event.offset, detections)
        truth.append(truth_fn(event.offset))
        predicted.append({o.tracklet_id: o.box for o in observed if o.confirmed})
        for o in observed:
            row = tracks.setdefault(o.tracklet_id, {'tracklet_id': o.tracklet_id, 'first_offset_s': o.offset_s,
                                                    'observations': 0})
            row['last_offset_s'], row['observations'] = o.offset_s, row['observations'] + 1
    metrics = evaluate_tracking(truth, predicted)
    return {'schema_version': 'tracking-demo-1', 'origin': 'demo', 'data': 'synthetic rectangles',
            'detector': 'MotionBlobDetector (moving regions, not a cattle model)',
            'tracker': 'ByteTrackLite', 'tracker_config': config.__dict__ | {}, 'metrics': metrics,
            'tracklets': sorted(tracks.values(), key=lambda r: r['tracklet_id'])}


def main(output: Path, seed: int = 0) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    video = write_video(output / 'synthetic_scene.mkv', timestamps_ms(seed=seed))
    result = run(video)
    (output / 'tracking_demo.json').write_text(json.dumps(result, indent=2))
    return result
