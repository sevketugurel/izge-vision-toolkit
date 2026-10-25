"""Cheap per-frame statistics and diverse-frame selection for annotation queues (no model)."""
from dataclasses import asdict, dataclass

import numpy as np

MONOCHROME_SATURATION = 0.04   # IR/night appearance heuristic; a hint, never camera-mode evidence
DARK_BRIGHTNESS = 0.08


@dataclass(frozen=True)
class FrameStats:
    sharpness: float    # variance of the 4-neighbour Laplacian on luma (0..1 scaled image)
    brightness: float   # mean luma, 0..1
    saturation: float   # mean (max-min)/max over pixels, 0..1
    appearance: str     # color_daylight | monochrome_night_appearance | dark

    def to_dict(self) -> dict:
        return asdict(self)


def frame_stats(rgb: np.ndarray) -> FrameStats:
    if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 3:
        raise ValueError('Expected an HxWx3 RGB array of at least 3x3 pixels')
    x = rgb.astype(np.float32) / 255.0
    luma = x @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lap = (luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:] - 4 * luma[1:-1, 1:-1])
    hi, lo = x.max(-1), x.min(-1)
    sat = np.divide(hi - lo, hi, out=np.zeros_like(hi), where=hi > 0)
    brightness, saturation = float(luma.mean()), float(sat.mean())
    appearance = ('dark' if brightness < DARK_BRIGHTNESS else
                  'monochrome_night_appearance' if saturation < MONOCHROME_SATURATION else 'color_daylight')
    return FrameStats(float(lap.var()), brightness, saturation, appearance)


def select_diverse(rows: list[dict], count: int, min_sharpness: float = 0.0, min_gap_s: float = 0.0) -> list[int]:
    """Greedy farthest-point choice over (offset, brightness, saturation, sharpness) of sharp-enough rows.

    `rows`: dicts with offset_s and the FrameStats fields. Returns indices into `rows` in time order.
    Deterministic: starts from the sharpest candidate, ties break on lower index.
    """
    if count < 0 or min_gap_s < 0:
        raise ValueError('Invalid selection request')
    pool = [i for i, r in enumerate(rows) if r['sharpness'] >= min_sharpness]
    if not pool or count == 0:
        return []
    feats = np.array([[rows[i]['offset_s'], rows[i]['brightness'], rows[i]['saturation'],
                       rows[i]['sharpness']] for i in pool], dtype=float)
    span = np.where(np.ptp(feats, axis=0) > 0, np.ptp(feats, axis=0), 1.0)
    feats = (feats - feats.min(axis=0)) / span
    chosen = [int(np.argmax(feats[:, 3]))]
    while len(chosen) < min(count, len(pool)):
        dist = np.min(np.linalg.norm(feats[:, None] - feats[chosen][None], axis=-1), axis=1)
        for c in range(len(pool)):
            if min(abs(rows[pool[c]]['offset_s'] - rows[pool[k]]['offset_s']) for k in chosen) < min_gap_s:
                dist[c] = -1
        nxt = int(np.argmax(dist))
        if dist[nxt] <= 0:
            break
        chosen.append(nxt)
    return sorted(pool[c] for c in chosen)
