"""BORIS "aggregated events" import (Decision 7). Pure parsing; no ORM, HTTP or codec code.

Subject = stall, Behavior = state, Start/Stop in seconds from the media start. Column and name
mappings come from config/boris.yaml. Ambiguity is an error, never silently resolved.
"""
import csv
import io
from dataclasses import dataclass
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field
from .states import STATES, State


class Columns(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    subject: str = 'Subject'
    behavior: str = 'Behavior'
    start: str = 'Start (s)'
    stop: str = 'Stop (s)'
    behavior_type: str | None = 'Behavior type'


class BorisMapping(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    columns: Columns = Columns()
    behaviors: dict[str, State] = Field(default_factory=dict)
    subjects: dict[str, str] = Field(default_factory=dict)

    def state(self, behavior: str) -> State | None:
        key = behavior.strip().casefold()
        found = {k.strip().casefold(): v for k, v in self.behaviors.items()}.get(key)
        return found or (key if key in STATES else None)

    def stall(self, subject: str) -> str:
        return self.subjects.get(subject.strip(), subject.strip())


def default_mapping_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'config' / 'boris.yaml'


def load_mapping(path: Path | None = None) -> BorisMapping:
    return BorisMapping.model_validate(yaml.safe_load((path or default_mapping_path()).read_text(encoding='utf-8')) or {})


@dataclass(frozen=True)
class GoldEvent:
    stall: str
    state: State
    start: float
    end: float


def parse(text: str, mapping: BorisMapping, stalls: set[str], duration: float | None = None) -> list[GoldEvent]:
    text = text.lstrip('﻿')
    first = text.splitlines()[0] if text.strip() else ''
    delimiter = '\t' if first.count('\t') >= first.count(',') else ','
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    c = mapping.columns
    missing = [col for col in (c.subject, c.behavior, c.start, c.stop) if col not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f'BORIS dosyasında sütun yok: {", ".join(missing)} (bulunan: {", ".join(reader.fieldnames or [])})')
    events, errors = [], []
    for line, row in enumerate(reader, start=2):
        if c.behavior_type and row.get(c.behavior_type, '').strip().upper() == 'POINT':
            errors.append(f'satır {line}: POINT olay desteklenmez; durum (STATE) olayı kullanın')
            continue
        state = mapping.state(row[c.behavior] or '')
        stall = mapping.stall(row[c.subject] or '')
        if state is None:
            errors.append(f'satır {line}: bilinmeyen davranış "{row[c.behavior]}" (config/boris.yaml behaviors)')
            continue
        if stall not in stalls:
            errors.append(f'satır {line}: bilinmeyen bölme "{row[c.subject]}" (kamera config bölmeleri: {", ".join(sorted(stalls))})')
            continue
        try:
            start, end = float(row[c.start]), float(row[c.stop])
        except (TypeError, ValueError):
            errors.append(f'satır {line}: Start/Stop sayı değil')
            continue
        if not 0 <= start < end:
            errors.append(f'satır {line}: geçersiz aralık {start}–{end}')
            continue
        if duration is not None and end > duration + 1:
            errors.append(f'satır {line}: {end} sn video süresini ({duration:.1f} sn) aşıyor')
            continue
        events.append(GoldEvent(stall, state, start, end))
    by_stall: dict[str, list[GoldEvent]] = {}
    for e in sorted(events, key=lambda e: (e.stall, e.start)):
        by_stall.setdefault(e.stall, []).append(e)
    for stall, rows in by_stall.items():
        for a, b in zip(rows, rows[1:]):
            if b.start < a.end - 1e-6:
                errors.append(f'{stall}: çakışan olaylar {a.state} {a.start}–{a.end} ve {b.state} {b.start}–{b.end}')
    if errors:
        raise ValueError('BORIS içe aktarma reddedildi:\n' + '\n'.join(errors[:50]))
    if not events:
        raise ValueError('BORIS dosyasında olay yok')
    return [e for rows in by_stall.values() for e in rows]
