"""Pure domain contracts and algorithms; no ORM, HTTP or codec dependencies."""
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta, time
from fractions import Fraction
from typing import Protocol, Iterable, Literal, Any
from zoneinfo import ZoneInfo

Origin = Literal["manual", "model", "demo", "unavailable"]

@dataclass(frozen=True)
class Frame:
    pts: int
    time_base: Fraction
    offset: float
    width: int
    height: int
    image: Any
    recorded_at: datetime | None = None
    received_at: datetime | None = None

@dataclass(frozen=True)
class Observation:
    offset: float
    track_id: str
    animal_id: str | None = None
    stall_id: str | None = None
    identity: str = "unknown"
    body_visible: bool = False
    head_visible: bool = False
    feeder_visible: bool = False
    posture: str = "unknown"
    activity: str = "unknown"
    quality: float = 0
    origin: Origin = "unavailable"
    producer: str = "unavailable-1"
    signal: str | None = None
    actor_id: str | None = None
    actor_identity: str = "unknown"
    feed_present: bool | None = None
    confidence: float | None = None
    pts: int | None = None
    time_base: str | None = None

class VideoSource(Protocol):
    def frames(self, start: float = 0) -> Iterable[Frame]: ...
class Detector(Protocol):
    def detect(self, frame: Frame) -> list[dict]: ...
class IdentityResolver(Protocol):
    def resolve(self, stall_id: str, at: datetime | None) -> str | None: ...
class BehaviorAnalyzer(Protocol):
    version: str
    def observe(self, frame: Frame) -> list[Observation]: ...
class EventRule(Protocol):
    def evaluate(self, observations: list[Observation], config: dict) -> list[dict]: ...
class NotificationSink(Protocol):
    def notify(self, event_id: str, message: str) -> None: ...
class Storage(Protocol):
    def path(self, key: str) -> Any: ...


def validate_polygon(points: list[list[float]]) -> None:
    if not 3 <= len(points) <= 64 or any(len(p) != 2 or any(not 0 <= v <= 1 for v in p) for p in points):
        raise ValueError("Çokgen 3–64 köşeli ve [0,1] sınırları içinde olmalı")
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError("Tekrarlanan köşe")
    area = sum(p[0]*q[1]-q[0]*p[1] for p,q in zip(points, points[1:]+points[:1]))
    if abs(area) < 1e-6:
        raise ValueError("Çokgen alanı sıfır")
    def orient(a,b,c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def intersects(a,b,c,d):
        if max(a[0],b[0]) < min(c[0],d[0]) or max(c[0],d[0]) < min(a[0],b[0]):
            return False
        if max(a[1],b[1]) < min(c[1],d[1]) or max(c[1],d[1]) < min(a[1],b[1]):
            return False
        return orient(a,b,c)*orient(a,b,d) <= 0 and orient(c,d,a)*orient(c,d,b) <= 0
    n = len(points)
    for i in range(n):
        for j in range(i+1,n):
            if j == i+1 or (i == 0 and j == n-1):
                continue
            if intersects(points[i], points[(i+1)%n], points[j], points[(j+1)%n]):
                raise ValueError("Çokgen kendiyle kesişiyor")


def derive_intervals(obs: list[Observation], max_gap: float = 5, transition: float = 3, posture_hz: float = 1) -> list[dict]:
    """Only adjacent agreeing, visible samples support duration. No end-of-file extension."""
    result = []
    groups: dict[str, list[Observation]] = {}
    for o in obs:
        if o.animal_id and o.identity == "resolved":
            groups.setdefault(o.animal_id, []).append(o)
    for animal, rows in groups.items():
        rows.sort(key=lambda o: o.offset)
        for dim in ("posture", "activity"):
            candidate, since, accepted = None, 0., None
            samples = rows
            if dim == 'posture':
                sampled = {}
                for row in rows:
                    sampled.setdefault(int((row.offset + 1e-7) * posture_hz), row)
                samples = list(sampled.values())
            for a,b in zip(samples,samples[1:]):
                label = getattr(a, dim)
                visible = (a.body_visible and b.body_visible) if dim == "posture" else (a.head_visible and b.head_visible and a.feeder_visible and b.feeder_visible)
                supported = 0 < b.offset-a.offset <= max_gap and visible and label != "unknown" and label == getattr(b, dim)
                if dim == "activity" and label == "feeding":
                    supported = supported and a.feed_present is True and b.feed_present is True and a.body_visible and b.body_visible
                if not supported:
                    candidate, accepted = None, None
                    continue
                if candidate != label:
                    candidate, since = label, a.offset
                if dim == "posture" and b.offset-since < transition:
                    continue
                # Confirmation applies prospectively, never fills an unsupported earlier gap.
                if accepted != label:
                    accepted = label
                interval = dict(animal_id=animal, start_offset=a.offset, end_offset=b.offset,
                    dimension=dim, label=label, quality=min(a.quality,b.quality), origin=a.origin)
                previous = result[-1] if result else None
                if previous and previous['end_offset'] == a.offset and all(
                    previous[key] == interval[key] for key in ('animal_id', 'dimension', 'label', 'quality', 'origin')
                ):
                    previous['end_offset'] = b.offset
                else:
                    result.append(interval)
    return result


def day_bounds(day, timezone: str, now: datetime):
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, zone).astimezone(ZoneInfo("UTC"))
    end = datetime.combine(day+timedelta(days=1), time.min, zone).astimezone(ZoneInfo("UTC"))
    return start, max(start, min(end, now))


def union_seconds(spans: list[tuple[float,float]]) -> float:
    end, total = float("-inf"), 0.
    for a,b in sorted(spans):
        total += max(0., b-max(a,end))
        end = max(end,b)
    return total


def fuse(spans: list[dict], scopes: list[tuple[float,float]]) -> dict:
    """Sweep clipped spans. Unverified camera clocks cannot create combined evidence."""
    labels: dict[str,float] = {}
    reasons: dict[str,float] = {}
    edges = sorted({x for a,b in scopes for x in (a,b)} | {x for s in spans for x in (s['start'],s['end'])})
    for a,b in zip(edges,edges[1:]):
        if b <= a or not any(x <= a and b <= y for x,y in scopes):
            continue
        active = [s for s in spans if s['start'] <= a and b <= s['end']]
        label, reason = "unknown", "no_supported_observation"
        if active:
            cameras = {s['camera_id'] for s in active}
            if len(cameras) > 1 and not all(s['clock_verified'] for s in active):
                # Use a stable single camera for the whole report, selected by caller.
                reason = "clock_unverified"
            else:
                active.sort(key=lambda s:s['quality'], reverse=True)
                best = active[0]
                if any(s['label'] != best['label'] and best['quality']-s['quality'] <= .1 for s in active[1:]):
                    reason = "camera_conflict"
                else:
                    label = best['label']
                    if label == 'unknown':
                        reason = best.get('reason') or 'unknown_observation'
        labels[label] = labels.get(label,0) + b-a
        if label == "unknown":
            reasons[reason] = reasons.get(reason,0) + b-a
    denominator = union_seconds(scopes)
    unknown = labels.get("unknown", 0)
    return dict(seconds=labels, denominator_seconds=denominator, measured_seconds=denominator-unknown,
                unknown_seconds=unknown, unknown_reasons=reasons, coverage=(denominator-unknown)/denominator if denominator else 0)


class FeedingAccessRule:
    """Evidence candidate after a completed sequence; notification only at pilot threshold."""
    def evaluate(self, observations: list[Observation], config: dict) -> list[dict]:
        states, output, last_notified = {}, [], {}
        for o in sorted(observations, key=lambda x:x.offset):
            if not o.animal_id or not o.signal:
                continue
            s = states.setdefault(o.animal_id, {"stage":None, "sequences":[], "event":None})
            if s['event'] and o.offset - s['event']['end_offset'] > config['close_silence']:
                s['event'], s['sequences'] = None, []
            if o.signal == "approach":
                s['stage'] = [o, None, None, 0.]
            elif o.signal == "interference" and s['stage']:
                if o.offset-s['stage'][0].offset <= config['sequence_seconds']:
                    previous = s['stage'][2]
                    if previous and 0 < o.offset-previous.offset <= config['max_gap']:
                        valid_contact = all(x.head_visible and x.feeder_visible and x.feed_present is True
                            and (x.origin == 'manual' or (x.confidence is not None and x.confidence >= config['confidence']))
                            for x in (previous, o))
                        if valid_contact:
                            s['stage'][3] += o.offset-previous.offset
                    if s['stage'][1] is None:
                        s['stage'][1] = o
                    elif (s['stage'][1].actor_id, s['stage'][1].actor_identity) != (o.actor_id, o.actor_identity):
                        s['stage'][1] = replace(s['stage'][1], actor_id=None, actor_identity='unknown')
                    s['stage'][2] = o
            elif o.signal in ("withdrawal", "feeding_interrupted") and s['stage']:
                approach, intervention, _last_intervention, interference_duration = s['stage']
                s['stage'] = None
                if not intervention or o.offset-approach.offset > config['sequence_seconds']:
                    continue
                valid = all(x.head_visible and x.feeder_visible and x.feed_present is True and
                    (x.origin == "manual" or (x.confidence is not None and x.confidence >= config['confidence']))
                    for x in (approach,intervention,o))
                s['sequences'] = [x for x in s['sequences'] if o.offset-x[0] <= config['repeat_window']]
                s['sequences'].append((o.offset,valid))
                sufficient = valid and (sum(v for _,v in s['sequences']) >= config['repeat_count'] or
                                        interference_duration >= config['interference_seconds'])
                actor = intervention.actor_id if intervention.actor_identity == 'resolved' else None
                if not s['event']:
                    event = dict(event_key=f"{o.animal_id}:{approach.offset:.6f}", start_offset=approach.offset,
                        end_offset=o.offset, kind="possible_feed_access_obstruction", origin=o.origin,
                        confidence=None if o.origin == 'manual' else min(x.confidence or 0 for x in (approach, intervention, o)), sufficient=sufficient,
                        victim=o.animal_id, actor=actor,
                        details={"rule_version":config['version'], "sequences":1, "notify":False, "reason":"threshold_pending" if valid else "insufficient_feed_or_visibility"})
                    output.append(event)
                    s['event'] = event
                else:
                    event = s['event']
                    event['end_offset'] = o.offset
                    event['sufficient'] |= sufficient
                    event['details']['sequences'] += 1
                    if event['actor'] != actor:
                        event['actor'] = None
                if event['sufficient']:
                    event['details']['reason'] = "pilot_threshold_met"
                    if not event['details']['notify'] and o.offset-last_notified.get(o.animal_id,float('-inf')) >= config['notification_cooldown']:
                        event['details']['notify'] = True
                        last_notified[o.animal_id] = o.offset
        return output


def observation_dict(o: Observation) -> dict:
    return asdict(o)


def subtract_spans(span: tuple[float, float], blockers: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = [span]
    for left, right in blockers:
        next_result = []
        for a, b in result:
            if right <= a or left >= b:
                next_result.append((a, b))
            else:
                if a < left:
                    next_result.append((a, left))
                if right < b:
                    next_result.append((right, b))
        result = next_result
    return result
