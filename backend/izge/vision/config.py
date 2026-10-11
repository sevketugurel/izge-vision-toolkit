"""Versioned behavior geometry, independent of the existing feeding camera config."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from ..domain import validate_polygon

KEY = r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class Region(Strict):
    id: str = Field(pattern=KEY)
    box: tuple[float, float, float, float] | None = None
    polygon: tuple[tuple[float, float], ...] | None = None

    @model_validator(mode='after')
    def one_shape(self):
        if (self.box is None) == (self.polygon is None):
            raise ValueError('ROI yalnız box veya polygon taşımalı')
        if self.box and not (self.box[0] < self.box[2] and self.box[1] < self.box[3]):
            raise ValueError('ROI box: x0<x1 ve y0<y1 olmalı')
        return self

    def points(self) -> list[tuple[float, float]]:
        if self.box is not None:
            x0, y0, x1, y1 = self.box
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return list(self.polygon or [])


class AnimalROI(Region):
    side: Literal['left', 'right']


class FeederROI(Region):
    animal_roi_id: str | None = Field(default=None, pattern=KEY)


class StallROI(Region):
    animal_roi_id: str = Field(pattern=KEY)
    feeder_roi_id: str | None = Field(default=None, pattern=KEY)


class GeometryVerification(Strict):
    revision: str = Field(pattern=KEY)
    camera_position_revision: str = Field(pattern=KEY)
    verified_by: str = Field(min_length=1, max_length=120)
    verified_at: datetime
    reference_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def aware_time(self):
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError('ROI doğrulama zamanı saat dilimi taşımalı')
        if not self.verified_by.strip():
            raise ValueError('ROI doğrulayan kişi boş olamaz')
        return self


class Sampling(Strict):
    detection_hz: float = Field(default=5, gt=0, le=120)
    posture_hz: float = Field(default=1, gt=0, le=120)
    source_gap_threshold_s: float = Field(default=1, gt=0, le=3600)


class StateThresholds(Strict):
    posture_support_s: float = Field(default=2, ge=0, le=600)
    transition_support_s: float = Field(default=3, ge=0, le=600)
    max_gap_factor: float = Field(default=1.5, gt=0, le=10)


class BehaviorConfig(Strict):
    schema_version: Literal['behavior-config-1']
    camera_id: str = Field(pattern=KEY)
    reference_width: StrictInt = Field(gt=0, le=8192)
    reference_height: StrictInt = Field(gt=0, le=8192)
    placeholder: StrictBool = True
    geometry_verification: GeometryVerification | None = None
    animal_rois: tuple[AnimalROI, ...] = Field(default=(), max_length=64)
    feeder_rois: tuple[FeederROI, ...] = Field(default=(), max_length=64)
    stalls: tuple[StallROI, ...] = Field(default=(), max_length=128)
    sampling: Sampling = Field(default_factory=Sampling)
    thresholds: StateThresholds = Field(default_factory=StateThresholds)

    @model_validator(mode='after')
    def geometry(self):
        regions = [*self.animal_rois, *self.feeder_rois, *self.stalls]
        if len({r.id for r in regions}) != len(regions):
            raise ValueError('ROI kimlikleri bütün gruplarda benzersiz olmalı')
        for region in regions:
            points = region.points()
            if any(not (0 <= x <= self.reference_width and 0 <= y <= self.reference_height)
                   for x, y in points):
                raise ValueError('ROI referans görüntü sınırı dışında')
            validate_polygon([[x / self.reference_width, y / self.reference_height] for x, y in points])
        animal_ids = {r.id for r in self.animal_rois}
        feeder_ids = {r.id for r in self.feeder_rois}
        for region in [*self.feeder_rois, *self.stalls]:
            if region.animal_roi_id is not None and region.animal_roi_id not in animal_ids:
                raise ValueError('ROI hayvan alanı ilişkisi bulunamadı')
        if any(r.feeder_roi_id is not None and r.feeder_roi_id not in feeder_ids for r in self.stalls):
            raise ValueError('Bölmenin yemlik ilişkisi bulunamadı')
        if not self.placeholder and (self.geometry_verification is None or
                                     not self.animal_rois or not self.feeder_rois):
            raise ValueError('Gerçek ROI için alanlar ve açık doğrulama kaydı gerekli')
        return self

    def validate_resolution(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0 or width * self.reference_height != height * self.reference_width:
            raise ValueError('Video en-boy oranı ROI referansı ile uyumsuz')

    def roi_verified_for(self, width: int, height: int) -> bool:
        self.validate_resolution(width, height)
        return (not self.placeholder and self.geometry_verification is not None and
                (width, height) == (self.reference_width, self.reference_height))

    def require_analysis_ready(self, width: int, height: int) -> None:
        if not self.roi_verified_for(width, height):
            raise ValueError('ROI doğrulanmadı; ROI tabanlı analiz engellendi')

    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode='json'), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode()).hexdigest()


def load(path: Path) -> BehaviorConfig:
    text = path.read_text(encoding='utf-8')
    data = json.loads(text) if path.suffix.lower() == '.json' else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError('Behavior config bir eşleme olmalı')
    return BehaviorConfig.model_validate(data)
