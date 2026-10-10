"""Pure smoothing, segmentation and daily summaries; no ORM, HTTP or codec dependencies.

Each 1 Hz sample at offset t covers [t, t+1), clipped to the analysed range end; the last sample
is never extended to the end of the file. Seconds without a decoded sample stay unobserved,
except inside a merged feeding gap (Decision 3), which becomes part of the bout.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from .states import STATES, FEEDING, NOT_FEEDING, NOT_VISIBLE, State


@dataclass(frozen=True)
class SmoothingParams:
    window_seconds: int = 5
    enter: float = .6
    exit: float = .4
    gap_merge_seconds: float = 30


@dataclass(frozen=True)
class Segment:
    state: State
    start: float
    end: float
    confidence: float | None  # mean probability of the assigned state over sampled seconds

    @property
    def seconds(self) -> float:
        return self.end - self.start


def moving_average(offsets: list[float], probs: list[tuple[float, float, float]], window: int):
    """Centered mean over samples whose offset lies within ±window/2 seconds."""
    half, result, lo, hi = window / 2, [], 0, 0
    n = len(offsets)
    for i, t in enumerate(offsets):
        while offsets[lo] < t - half:
            lo += 1
        while hi < n and offsets[hi] <= t + half:
            hi += 1
        span = probs[lo:hi]
        result.append(tuple(sum(p[k] for p in span) / len(span) for k in range(3)))
    return result


def classify(probs: list[tuple[float, float, float]], params: SmoothingParams) -> list[State]:
    """Hysteresis on P(yemde); otherwise the more likely of yemde_degil / gorunmuyor."""
    states, feeding = [], False
    for p in probs:
        feeding = p[0] >= params.exit if feeding else p[0] >= params.enter
        states.append(FEEDING if feeding else (NOT_FEEDING if p[1] >= p[2] else NOT_VISIBLE))
    return states


def smooth(offsets: list[float], probs: list[tuple[float, float, float]], end_offset: float,
           params: SmoothingParams) -> list[Segment]:
    if len(offsets) != len(probs):
        raise ValueError('offsets and probs differ in length')
    order = sorted(range(len(offsets)), key=lambda i: offsets[i])
    offsets = [offsets[i] for i in order]
    probs = [probs[i] for i in order]
    if not offsets:
        return []
    averaged = moving_average(offsets, probs, params.window_seconds)
    states = classify(averaged, params)
    runs: list[_Run] = []
    for t, state, p in zip(offsets, states, averaged):
        if t >= end_offset:
            continue
        end = min(t + 1, end_offset)
        d = end - t
        if runs and runs[-1].state == state and abs(runs[-1].end - t) < 1e-6:
            runs[-1].extend(end, p, d)
        else:
            runs.append(_Run(state, t, end, [p[0] * d, p[1] * d, p[2] * d], d))
    merged: list[_Run] = []
    for run in runs:
        bout = next((m for m in reversed(merged) if m.state == FEEDING), None)
        if run.state == FEEDING and bout is not None and run.start - bout.end < params.gap_merge_seconds:
            # Short gap between two feeding runs (Decision 3): the gap becomes part of the bout.
            index = merged.index(bout)
            for gap in merged[index + 1:]:
                bout.absorb(gap)
            bout.absorb(run)
            del merged[index + 1:]
        else:
            merged.append(run)
    return [r.segment() for r in merged]


class _Run:
    def __init__(self, state: State, start: float, end: float, p_sum: list[float], sampled: float):
        self.state, self.start, self.end, self.p_sum, self.sampled = state, start, end, p_sum, sampled

    def extend(self, end, p, d):
        self.end = end
        self.sampled += d
        for k in range(3):
            self.p_sum[k] += p[k] * d

    def absorb(self, other: '_Run'):
        self.end = other.end
        self.sampled += other.sampled
        for k in range(3):
            self.p_sum[k] += other.p_sum[k]

    def segment(self) -> Segment:
        confidence = self.p_sum[STATES.index(self.state)] / self.sampled if self.sampled else None
        return Segment(self.state, self.start, self.end, confidence)


@dataclass(frozen=True)
class DailySummary:
    day: str
    feeding_seconds: float
    not_feeding_seconds: float
    not_visible_seconds: float
    observed_seconds: float
    bout_count: int
    mean_confidence: float | None


def daily_summaries(segments: list[Segment], recorded_at: datetime, timezone: str) -> list[DailySummary]:
    """Split absolute segments at local midnights; a bout counts on the day it starts."""
    zone = ZoneInfo(timezone)
    days: dict[str, dict] = {}
    for seg in segments:
        start = recorded_at + timedelta(seconds=seg.start)
        end = recorded_at + timedelta(seconds=seg.end)
        first = True
        while start < end:
            local = start.astimezone(zone)
            midnight = datetime.combine(local.date() + timedelta(days=1), time.min, zone)
            piece_end = min(end, midnight)
            seconds = (piece_end - start).total_seconds()
            d = days.setdefault(local.date().isoformat(), dict(yemde=0., yemde_degil=0., gorunmuyor=0., bouts=0, w=0.))
            d[seg.state] += seconds
            if seg.confidence is not None:
                d['w'] += seconds * seg.confidence
            if first and seg.state == FEEDING:
                d['bouts'] += 1
            first, start = False, piece_end
    result = []
    for day, d in sorted(days.items()):
        observed = d[FEEDING] + d[NOT_FEEDING] + d[NOT_VISIBLE]
        result.append(DailySummary(day, d[FEEDING], d[NOT_FEEDING], d[NOT_VISIBLE], observed, d['bouts'],
                                   d['w'] / observed if observed else None))
    return result
