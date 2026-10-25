"""Frame-difference moving-blob detector: a dependency-free smoke baseline for the tracking pipeline.

It finds moving regions, not cattle: it has no class knowledge and is used only for synthetic demos,
CPU smoke tests and tracker plumbing. Never report its output as animal detection.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BlobDetection:
    box: tuple[float, float, float, float]
    score: float


def _components(mask: np.ndarray):
    """4-connected components via iterative flood fill; yields (ys, xs)."""
    seen = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    for sy, sx in zip(*np.nonzero(mask), strict=True):
        if seen[sy, sx]:
            continue
        stack, ys, xs = [(sy, sx)], [], []
        seen[sy, sx] = True
        while stack:
            y, x = stack.pop()
            ys.append(y), xs.append(x)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        yield np.array(ys), np.array(xs)


class MotionBlobDetector:
    def __init__(self, threshold: float = 25.0, min_area: int = 40, background_rate: float = 0.02):
        self.threshold, self.min_area, self.rate = threshold, min_area, background_rate
        self._background: np.ndarray | None = None

    def detect(self, rgb: np.ndarray) -> list[BlobDetection]:
        gray = rgb.astype(np.float32).mean(-1)
        if self._background is None:
            self._background = gray.copy()
            return []
        diff = np.abs(gray - self._background)
        self._background += self.rate * (gray - self._background)
        out = []
        for ys, xs in _components(diff > self.threshold):
            if len(ys) >= self.min_area:
                box = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
                fill = len(ys) / ((box[2] - box[0]) * (box[3] - box[1]))
                out.append(BlobDetection(box, float(min(1.0, fill))))
        return out
