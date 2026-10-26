"""ByteTrack-style multi-object tracker (two-stage score association, constant-velocity Kalman).

Tracklet IDs are per-run, per-camera and ephemeral: they never identify an animal. Time comes from
PTS-derived offsets in seconds; nominal FPS is never used. Association is greedy by descending IoU
(reference ByteTrack uses an optimal LAP assignment), kept dependency-free on purpose.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TrackerConfig:
    high_score: float = 0.5      # first-stage detections; only these may start a track
    low_score: float = 0.1       # second-stage detections may only extend existing tracks
    match_iou: float = 0.3
    second_match_iou: float = 0.5
    max_lost_s: float = 2.0      # time based, so variable frame rate does not change behaviour
    min_hits: int = 2            # observations before a track is reported as confirmed

    def __post_init__(self):
        if not 0 <= self.low_score <= self.high_score <= 1 or self.max_lost_s < 0 or self.min_hits < 1:
            raise ValueError('Invalid tracker configuration')


@dataclass(frozen=True)
class TrackObservation:
    tracklet_id: int
    offset_s: float
    box: tuple[float, float, float, float]  # xyxy
    score: float
    confirmed: bool  # only measured detections are reported; predicted-only positions never are


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = np.asarray(a, float).reshape(-1, 4), np.asarray(b, float).reshape(-1, 4)
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(-1)
    area = lambda x: np.clip(x[:, 2] - x[:, 0], 0, None) * np.clip(x[:, 3] - x[:, 1], 0, None)  # noqa: E731
    union = area(a)[:, None] + area(b)[None, :] - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def greedy_match(iou: np.ndarray, threshold: float) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    pairs = sorted(((iou[i, j], i, j) for i in range(iou.shape[0]) for j in range(iou.shape[1])
                    if iou[i, j] >= threshold), reverse=True)
    used_r, used_c, matches = set(), set(), []
    for _, i, j in pairs:
        if i not in used_r and j not in used_c:
            used_r.add(i), used_c.add(j), matches.append((i, j))
    return (matches, [i for i in range(iou.shape[0]) if i not in used_r],
            [j for j in range(iou.shape[1]) if j not in used_c])


def _xyxy_to_cxcywh(box) -> np.ndarray:
    x1, y1, x2, y2 = (float(v) for v in box)
    return np.array([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1])


def _cxcywh_to_xyxy(z) -> tuple[float, float, float, float]:
    cx, cy, w, h = z
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


@dataclass
class _Track:
    tracklet_id: int
    x: np.ndarray            # [cx, cy, w, h, vcx, vcy, vw, vh]
    p: np.ndarray
    score: float
    last_measured_s: float
    hits: int = 1


class ByteTrackLite:
    def __init__(self, config: TrackerConfig = TrackerConfig()):
        self.config = config
        self._tracks: list[_Track] = []
        self._next_id = 1
        self._last_offset: float | None = None

    # -- Kalman (constant velocity, noise scaled by box size as in ByteTrack) ------------------------
    @staticmethod
    def _predict(t: _Track, dt: float):
        f = np.eye(8)
        f[:4, 4:] = np.eye(4) * dt
        scale = max(t.x[3], 1.0)
        q = np.diag([(scale / 20) ** 2] * 2 + [(scale / 20) ** 2] * 2 + [(scale / 160) ** 2] * 4) * max(dt, 1e-3)
        t.x = f @ t.x
        t.p = f @ t.p @ f.T + q
        t.x[2:4] = np.maximum(t.x[2:4], 1.0)

    @staticmethod
    def _update(t: _Track, z: np.ndarray):
        h = np.zeros((4, 8))
        h[:4, :4] = np.eye(4)
        scale = max(t.x[3], 1.0)
        r = np.diag([(scale / 20) ** 2] * 4)
        s = h @ t.p @ h.T + r
        k = t.p @ h.T @ np.linalg.inv(s)
        t.x = t.x + k @ (z - h @ t.x)
        t.p = (np.eye(8) - k @ h) @ t.p

    def _new(self, det_box, score: float, offset_s: float) -> _Track:
        z = _xyxy_to_cxcywh(det_box)
        scale = max(z[3], 1.0)
        track = _Track(self._next_id, np.concatenate([z, np.zeros(4)]),
                       np.diag([(scale / 10) ** 2] * 4 + [(scale / 2) ** 2] * 4), score, offset_s)
        self._next_id += 1
        return track

    def update(self, offset_s: float, detections) -> list[TrackObservation]:
        """`detections` need `.box` (xyxy) and `.score`. Offsets must strictly increase."""
        if self._last_offset is not None and offset_s <= self._last_offset:
            raise ValueError('Tracker offsets must be strictly increasing (PTS-derived seconds)')
        dt = 0.0 if self._last_offset is None else offset_s - self._last_offset
        self._last_offset = offset_s
        cfg = self.config
        self._tracks = [t for t in self._tracks if offset_s - t.last_measured_s <= cfg.max_lost_s]
        for t in self._tracks:
            self._predict(t, dt)
        boxes = [tuple(float(v) for v in d.box) for d in detections]
        scores = [float(d.score) for d in detections]
        high = [i for i, s in enumerate(scores) if s >= cfg.high_score]
        low = [i for i, s in enumerate(scores) if cfg.low_score <= s < cfg.high_score]
        # Most recently measured tracks are matched first by construction (greedy IoU is order-free;
        # this only breaks IoU ties deterministically).
        tracks = sorted(self._tracks, key=lambda t: -t.last_measured_s)
        measured: set[int] = set()

        def associate(candidates, det_idx, threshold):
            pred = np.array([_cxcywh_to_xyxy(t.x[:4]) for t in candidates]).reshape(-1, 4)
            matches, unmatched_t, unmatched_d = greedy_match(
                iou_matrix(pred, np.array([boxes[i] for i in det_idx]).reshape(-1, 4)), threshold)
            for ti, di in matches:
                t, i = candidates[ti], det_idx[di]
                self._update(t, _xyxy_to_cxcywh(boxes[i]))
                t.hits, t.score, t.last_measured_s = t.hits + 1, scores[i], offset_s
                measured.add(t.tracklet_id)
            return [candidates[i] for i in unmatched_t], [det_idx[j] for j in unmatched_d]

        rest_tracks, rest_high = associate(tracks, high, cfg.match_iou)
        recent = [t for t in rest_tracks if offset_s - t.last_measured_s <= cfg.max_lost_s]
        associate(recent, low, cfg.second_match_iou)
        for i in rest_high:
            track = self._new(boxes[i], scores[i], offset_s)
            self._tracks.append(track)
            measured.add(track.tracklet_id)
        return [TrackObservation(t.tracklet_id, offset_s, _cxcywh_to_xyxy(t.x[:4]), t.score,
                                 t.hits >= cfg.min_hits) for t in self._tracks if t.tracklet_id in measured]
