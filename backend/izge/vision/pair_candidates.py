"""Pair-interaction candidate filter over tracklets (pure domain: numpy-free, no ORM/HTTP/video libraries).

Input is ByteTrackLite output: per-observation `(tracklet_id, offset_s, box xyxy)`. Offsets are PTS-derived
seconds; nominal FPS is never used. Tracklet ids are run-local and never identify an animal.

A candidate is a time span in which two tracklets were *measured* close together. It is a prompt for a human
reviewer, not a behaviour label: nothing here names an interaction type, an animal, or a behaviour duration.
`span_s` is bracketed by two co-observed measurements; time before the first and after the last co-observation
is unknown and is never added. Co-observation gaps longer than `max_gap_s` end a segment (gap time is unknown).
`rank_score` is an ordering heuristic, not a probability.
"""
import hashlib
from dataclasses import dataclass
from itertools import combinations
from math import hypot


@dataclass(frozen=True)
class PairConfig:
    near_distance: float = 1.0   # centre distance / mean box height at or below which a sample is "near"
    max_gap_s: float = 1.0       # longer gaps between co-observed samples end a segment (unknown time)
    min_span_s: float = 1.0      # minimum measured span for a candidate
    min_samples: int = 3

    def __post_init__(self):
        if self.near_distance <= 0 or self.max_gap_s <= 0 or self.min_span_s < 0 or self.min_samples < 2:
            raise ValueError('Invalid pair candidate configuration')


@dataclass(frozen=True)
class Observation:
    tracklet_id: int
    offset_s: float
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class PairCandidate:
    tracklet_a: int
    tracklet_b: int
    start_s: float
    end_s: float
    sample_count: int
    min_distance: float
    mean_distance: float
    max_overlap: float
    mean_relative_speed: float | None  # box heights per second, from consecutive co-observed samples
    approach: bool                     # distance at the end is smaller than at the start
    rank_score: float

    @property
    def span_s(self) -> float:
        return self.end_s - self.start_s


def _centre(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _height(b):
    return max(b[3] - b[1], 1e-6)


def _overlap(a, b) -> float:
    """Intersection area over the smaller box area (0..1); robust to size differences."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if smaller > 0 else 0.0


def _sample(a: Observation, b: Observation):
    (ax, ay), (bx, by) = _centre(a.box), _centre(b.box)
    scale = (_height(a.box) + _height(b.box)) / 2
    return {'t': a.offset_s, 'dx': (bx - ax) / scale, 'dy': (by - ay) / scale,
            'dist': hypot(bx - ax, by - ay) / scale, 'overlap': _overlap(a.box, b.box)}


def _segments(samples: list[dict], config: PairConfig) -> list[list[dict]]:
    out, run = [], []
    for s in samples:
        if s['dist'] > config.near_distance or (run and s['t'] - run[-1]['t'] > config.max_gap_s):
            if run:
                out.append(run)
            run = []
        if s['dist'] <= config.near_distance:
            run.append(s)
    if run:
        out.append(run)
    return out


def _build(a: int, b: int, run: list[dict], config: PairConfig) -> PairCandidate | None:
    span = run[-1]['t'] - run[0]['t']
    if len(run) < config.min_samples or span < config.min_span_s:
        return None
    speeds = [hypot(q['dx'] - p['dx'], q['dy'] - p['dy']) / (q['t'] - p['t'])
              for p, q in zip(run, run[1:])]
    dists = [s['dist'] for s in run]
    closeness = sum(1 / (1 + d) for d in dists) / len(dists)
    max_overlap = max(s['overlap'] for s in run)
    return PairCandidate(a, b, run[0]['t'], run[-1]['t'], len(run), min(dists), sum(dists) / len(dists),
                         max_overlap, sum(speeds) / len(speeds), dists[-1] < dists[0],
                         round(closeness * span * (1 + max_overlap), 6))


def find_pair_candidates(observations: list[Observation], config: PairConfig = PairConfig()) -> list[PairCandidate]:
    """Ranked (descending `rank_score`, then earliest start) candidates. Only same-offset co-observations count."""
    by_track: dict[int, dict[float, Observation]] = {}
    for o in observations:
        by_track.setdefault(o.tracklet_id, {})[o.offset_s] = o
    out = []
    for a, b in combinations(sorted(by_track), 2):
        common = sorted(by_track[a].keys() & by_track[b].keys())
        samples = [_sample(by_track[a][t], by_track[b][t]) for t in common]
        for run in _segments(samples, config):
            c = _build(a, b, run, config)
            if c:
                out.append(c)
    return sorted(out, key=lambda c: (-c.rank_score, c.start_s, c.tracklet_a, c.tracklet_b))


def candidate_id(source_sha256: str, c: PairCandidate) -> str:
    key = f'{source_sha256}|{c.tracklet_a}|{c.tracklet_b}|{c.start_s:.6f}|{c.end_s:.6f}'
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def build_review_queue(source_sha256: str, candidates: list[PairCandidate], config: PairConfig,
                       data_origin: str) -> dict:
    """JSON-ready review queue. `data_origin` labels the input (`synthetic` or `real`); candidates are origin=model."""
    return {
        'schema': 'izge.pair_candidates.v1',
        'source_sha256': source_sha256,
        'data_origin': data_origin,
        'config': config.__dict__,
        'notes': ['Tracklet ids are run-local and are not animal identities.',
                  'span_s is bracketed by co-observed measurements; time outside it is unknown.',
                  'No interaction type or behaviour duration is claimed; rank_score is not a probability.'],
        'candidates': [{
            'candidate_id': candidate_id(source_sha256, c),
            'origin': 'model',
            'review_status': 'unreviewed',
            'source_sha256': source_sha256,
            'tracklet_ids': [c.tracklet_a, c.tracklet_b],
            'tracklet_ids_local': True,
            'pts_start_s': c.start_s,
            'pts_end_s': c.end_s,
            'measured_span_s': c.span_s,
            'duration_beyond_span': 'unknown',
            'sample_count': c.sample_count,
            'features': {'min_distance': c.min_distance, 'mean_distance': c.mean_distance,
                         'max_overlap': c.max_overlap, 'mean_relative_speed': c.mean_relative_speed,
                         'approach': c.approach},
            'rank_score': c.rank_score,
            'rank': i + 1,
        } for i, c in enumerate(candidates)],
    }
