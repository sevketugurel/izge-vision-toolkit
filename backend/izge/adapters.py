"""Analyzer registration is the extension point for the first real behavior model."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from .domain import BehaviorAnalyzer, Origin
from .video import UnavailableAnalyzer, DemoAnalyzer


class SnapshotIdentityResolver:
    """Resolve physical placement at the event time, never at upload time or 'now'."""
    def __init__(self, context: dict):
        self.context = context

    def resolve(self, stall_id: str, at: datetime | None) -> str | None:
        if at is None or not self.context['geometry_valid']:
            return None
        body_stalls = {z['stall_id'] for z in self.context['zones'] if z['role'] == 'body'}
        if stall_id not in body_stalls:
            return None
        matches = [p['animal_id'] for p in self.context['occupancies'] if p['stall_id'] == stall_id
                   and datetime.fromisoformat(p['start']) <= at
                   and (not p['end'] or at < datetime.fromisoformat(p['end']))]
        return matches[0] if len(matches) == 1 else None

    def at_offset(self, offset: float) -> list[tuple[str, str]]:
        if not self.context['recorded_at']:
            return []
        at = datetime.fromisoformat(self.context['recorded_at']) + timedelta(seconds=offset)
        result = []
        for stall in sorted({p['stall_id'] for p in self.context['occupancies']}):
            animal = self.resolve(stall, at)
            if animal:
                result.append((animal, stall))
        return result


@dataclass(frozen=True)
class AnalyzerSpec:
    origin: Origin
    factory: Callable[[dict], BehaviorAnalyzer]
    demo_only: bool = False


ANALYZERS = {
    'unavailable-1': AnalyzerSpec('unavailable', lambda context: UnavailableAnalyzer()),
    'demo-1': AnalyzerSpec('demo', lambda context: DemoAnalyzer(SnapshotIdentityResolver(context).at_offset), True),
}
