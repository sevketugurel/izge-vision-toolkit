"""Pluggable stall-state analyzers (Decision 4).

An analyzer maps 1 Hz stall crops to probabilities over STATES. The production analyzer is a
frozen image backbone (DINOv3 ViT-S/16) with a small trained classifier on its embeddings.
The demo analyzer produces synthetic probabilities and is only allowed on demo farms/media.
"""
import math
from dataclasses import dataclass
from typing import Protocol
import numpy as np
from .backbone import Backbone
from .classifier import LinearClassifier


@dataclass(frozen=True)
class StallCrop:
    stall_id: str
    offset: float
    image: np.ndarray  # RGB uint8 crop from crops.crop()


class StallStateAnalyzer(Protocol):
    version: str
    def predict(self, crops: list[StallCrop]) -> np.ndarray: ...   # shape (N, 3), rows sum to 1


class EmbeddingClassifierAnalyzer:
    def __init__(self, backbone: Backbone, classifier: LinearClassifier, version: str):
        if classifier.backbone != backbone.id:
            raise ValueError(f'Sınıflandırıcı {classifier.backbone} için eğitildi; yüklenen backbone {backbone.id}')
        self.backbone, self.classifier, self.version = backbone, classifier, version

    def predict(self, crops: list[StallCrop]) -> np.ndarray:
        if not crops:
            return np.zeros((0, 3))
        return self.classifier.predict_proba(self.backbone.embed([c.image for c in crops]))


class DemoStallAnalyzer:
    """Deterministic synthetic pattern: ~20 min feeding cycles with occasional occlusion."""
    version = 'feeding-demo-1'

    def predict(self, crops: list[StallCrop]) -> np.ndarray:
        rows = []
        for c in crops:
            phase = (c.offset / 60 + 3 * (sum(map(ord, c.stall_id)) % 7)) % 20
            if phase < 7:
                p = (.85, .1, .05)
            elif 15 <= phase < 16:
                p = (.1, .2, .7)
            else:
                p = (.1, .85, .05)
            jitter = .05 * math.sin(c.offset * 1.7 + len(c.stall_id))
            row = np.clip(np.array(p) + [jitter, -jitter, 0], .01, None)
            rows.append(row / row.sum())
        return np.array(rows).reshape(-1, 3)
