import numpy as np
import pytest

from izge.vision.frame_quality import frame_stats, select_diverse


def textured(color=True, level=128):
    rng = np.random.default_rng(1)
    base = rng.integers(max(level - 60, 0), min(level + 60, 255), (40, 40, 1))
    return np.repeat(base, 3, axis=2).astype(np.uint8) if not color else np.dstack(
        [base[..., 0], base[..., 0] // 2, base[..., 0] // 4]).astype(np.uint8)


def test_appearance_labels():
    assert frame_stats(textured(True)).appearance == 'color_daylight'
    assert frame_stats(textured(False)).appearance == 'monochrome_night_appearance'
    assert frame_stats(np.zeros((8, 8, 3), np.uint8)).appearance == 'dark'


def test_blur_lowers_sharpness_and_bad_shape_rejected():
    sharp = textured(False)
    blurred = ((sharp.astype(float) + np.roll(sharp, 1, 0) + np.roll(sharp, 1, 1) + np.roll(sharp, (1, 1), (0, 1))) / 4).astype(np.uint8)
    assert frame_stats(blurred).sharpness < frame_stats(sharp).sharpness
    with pytest.raises(ValueError):
        frame_stats(np.zeros((8, 8), np.uint8))


def rows():
    return [{'offset_s': t, 'sharpness': s, 'brightness': b, 'saturation': 0.3}
            for t, s, b in ((0, .2, .5), (1, .001, .5), (2, .3, .2), (3, .25, .8), (4, .28, .5))]


def test_selection_is_deterministic_filters_blur_and_respects_gap():
    first = select_diverse(rows(), 3, min_sharpness=0.01)
    assert first == select_diverse(rows(), 3, min_sharpness=0.01)
    assert 1 not in first and first == sorted(first) and len(first) == 3
    spaced = select_diverse(rows(), 5, min_sharpness=0.01, min_gap_s=2.5)
    assert all(b - a >= 2.5 for a, b in zip([rows()[i]['offset_s'] for i in spaced], [rows()[i]['offset_s'] for i in spaced][1:]))
    assert select_diverse(rows(), 0) == [] and select_diverse([], 3) == []
