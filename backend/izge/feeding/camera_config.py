"""Per-camera stall crop configuration (YAML/JSON), validated with Pydantic.

Coordinates are pixels in `reference_resolution`. A video with a different resolution but the
same aspect ratio is scaled; any other resolution is rejected.
"""
import hashlib
import json
import os
import re
from datetime import datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ..domain import validate_polygon

KEY = r'^[a-z0-9][a-z0-9_-]{0,79}$'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Resolution(Strict):
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)


class FilenameTime(Strict):
    """Named groups Y, m, d, H, M and optional S; the time is local to the camera timezone."""
    pattern: str

    @field_validator('pattern')
    @classmethod
    def valid_pattern(cls, v):
        try:
            groups = set(re.compile(v).groupindex)
        except re.error as exc:
            raise ValueError(f'Geçersiz regex: {exc}')
        missing = {'Y', 'm', 'd', 'H', 'M'} - groups
        if missing:
            raise ValueError(f'Regex şu adlı grupları içermeli: {", ".join(sorted(missing))}')
        return v


class Daylight(Strict):
    start: time = time(6, 0)
    end: time = time(19, 0)


class Smoothing(Strict):
    window_seconds: int = Field(default=5, ge=1, le=61)
    enter: float = Field(default=.6, gt=0, lt=1)
    exit: float = Field(default=.4, gt=0, lt=1)
    gap_merge_seconds: float = Field(default=30, ge=0, le=600)

    @model_validator(mode='after')
    def hysteresis(self):
        if self.exit > self.enter:
            raise ValueError('Histerezis: exit eşiği enter eşiğinden büyük olamaz')
        return self


class Sampling(Strict):
    per_stratum: int = Field(default=8, ge=1, le=200)


class Stall(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$')
    sira: str = Field(min_length=1, max_length=40)
    mesafe: Literal['yakin', 'orta', 'uzak']
    box: tuple[float, float, float, float] | None = None
    polygon: list[tuple[float, float]] | None = None

    @model_validator(mode='after')
    def one_shape(self):
        if (self.box is None) == (self.polygon is None):
            raise ValueError(f'{self.id}: yalnız biri tanımlanmalı: box veya polygon')
        if self.box is not None and not (self.box[0] < self.box[2] and self.box[1] < self.box[3]):
            raise ValueError(f'{self.id}: box [x0, y0, x1, y1] ve x0<x1, y0<y1 olmalı')
        return self

    def points(self) -> list[tuple[float, float]]:
        if self.box is not None:
            x0, y0, x1, y1 = self.box
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return list(self.polygon or [])


class CameraConfig(Strict):
    camera_id: str = Field(pattern=KEY)
    name: str = Field(min_length=1, max_length=120)
    barn: str = Field(min_length=1, max_length=120)
    placeholder: bool = False
    timezone: str = 'Europe/Istanbul'
    reference_resolution: Resolution
    filename_time: FilenameTime | None = None
    daylight: Daylight = Daylight()
    smoothing: Smoothing = Smoothing()
    sampling: Sampling = Sampling()
    stalls: list[Stall] = Field(min_length=1, max_length=64)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, v):
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError('Geçersiz saat dilimi')
        return v

    @model_validator(mode='after')
    def stalls_inside_frame(self):
        ids = [s.id for s in self.stalls]
        if len(set(ids)) != len(ids):
            raise ValueError('Bölme kimlikleri benzersiz olmalı')
        w, h = self.reference_resolution.width, self.reference_resolution.height
        for stall in self.stalls:
            points = stall.points()
            if any(not (0 <= x <= w and 0 <= y <= h) for x, y in points):
                raise ValueError(f'{stall.id}: koordinatlar referans çözünürlük ({w}×{h}) dışında')
            try:
                validate_polygon([[x / w, y / h] for x, y in points])
            except ValueError as exc:
                raise ValueError(f'{stall.id}: {exc}')
        return self

    def stall(self, stall_id: str) -> Stall:
        return next(s for s in self.stalls if s.id == stall_id)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()

    def recorded_at_from_filename(self, filename: str) -> datetime | None:
        if not self.filename_time:
            return None
        match = re.search(self.filename_time.pattern, filename)
        if not match:
            return None
        g = match.groupdict()
        try:
            return datetime(int(g['Y']), int(g['m']), int(g['d']), int(g['H']), int(g['M']),
                            int(g.get('S') or 0), tzinfo=ZoneInfo(self.timezone))
        except ValueError:
            return None

    def is_daytime(self, local: datetime) -> bool:
        t = local.timetz().replace(tzinfo=None)
        start, end = self.daylight.start, self.daylight.end
        return start <= t < end if start < end else not (end <= t < start)


def config_dirs() -> list[Path]:
    default = Path(__file__).resolve().parents[3] / 'config' / 'cameras'
    raw = os.getenv('IZGE_CAMERA_CONFIG_DIRS')
    return [Path(p) for p in raw.split(os.pathsep) if p] if raw else [default]


def parse_config(text: str, suffix: str = '.yaml') -> CameraConfig:
    data = json.loads(text) if suffix == '.json' else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError('Config bir eşleme (mapping) olmalı')
    return CameraConfig.model_validate(data)


def load_all() -> dict[str, CameraConfig | str]:
    """Return configs by camera_id; invalid files map to a readable error string (key = file stem)."""
    result: dict[str, CameraConfig | str] = {}
    for directory in config_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix not in ('.yaml', '.yml', '.json'):
                continue
            try:
                config = parse_config(path.read_text(encoding='utf-8'), path.suffix)
            except Exception as exc:
                result[path.stem] = f'{path.name}: {exc}'
                continue
            if config.camera_id in result:
                result[config.camera_id] = f'{path.name}: camera_id tekrar ediyor ({config.camera_id})'
                continue
            result[config.camera_id] = config
    return result


def load(camera_key: str) -> CameraConfig:
    found = load_all().get(camera_key)
    if found is None:
        raise LookupError(f'Kamera config bulunamadı: {camera_key}')
    if isinstance(found, str):
        raise ValueError(f'Kamera config geçersiz: {found}')
    return found
