"""Stall feeding pipeline units: config validation, crop extraction, smoothing, summaries, classifier.
No backbone weights are downloaded; a stub backbone stands in for DINOv3."""
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pytest
from izge.feeding import camera_config
from izge.feeding.camera_config import CameraConfig, parse_config
from izge.feeding.classifier import LinearClassifier, fit
from izge.feeding.crops import crop, geometries, letterbox, FILL
from izge.feeding.pipeline import _frames, sample_times
from izge.feeding.smoothing import SmoothingParams, smooth, daily_summaries, Segment
from synthetic import stall_video, truth, StubBackbone

IST = ZoneInfo('Europe/Istanbul')
BASE = dict(camera_id='cam', name='Kamera', barn='Ahır', reference_resolution={'width': 320, 'height': 180},
            stalls=[{'id': 'A', 'sira': 'orta', 'mesafe': 'yakin', 'box': [0, 0, 160, 180]}])


def config(**changes):
    return CameraConfig.model_validate(BASE | changes)


# --- config -------------------------------------------------------------------------------------

def test_sample_synthetic_feeding_config_is_valid():
    cfg = camera_config.load('synthetic_feeding')
    assert cfg.placeholder is False
    assert {s.sira for s in cfg.stalls} == {'orta', 'sag'}
    assert len(cfg.stalls) == 14
    assert cfg.reference_resolution.width == 2560 and cfg.reference_resolution.height == 1440
    assert cfg.recorded_at_from_filename('synthetic_feeding_20261010_143000.mp4') == datetime(2026, 10, 10, 14, 30, tzinfo=IST)


@pytest.mark.parametrize('changes, message', [
    ({'stalls': [{'id': 'A', 'sira': 'orta', 'mesafe': 'yakin', 'box': [0, 0, 10, 10]}] * 2}, 'benzersiz'),
    ({'stalls': [{'id': 'A', 'sira': 'orta', 'mesafe': 'yakin', 'box': [0, 0, 400, 10]}]}, 'dışında'),
    ({'stalls': [{'id': 'A', 'sira': 'orta', 'mesafe': 'yakin'}]}, 'box veya polygon'),
    ({'stalls': [{'id': 'A', 'sira': 'orta', 'mesafe': 'yakin', 'box': [10, 0, 5, 10]}]}, 'x0<x1'),
    ({'stalls': [{'id': 'A', 'sira': 'orta', 'mesafe': 'cok_uzak', 'box': [0, 0, 10, 10]}]}, 'mesafe'),
    ({'stalls': [{'id': 'A', 'sira': 'orta', 'mesafe': 'yakin', 'polygon': [[0, 0], [20, 10], [20, 0], [0, 4]]}]}, 'kesişiyor'),
    ({'filename_time': {'pattern': r'(?P<Y>\d{4})'}}, 'adlı grupları'),
    ({'smoothing': {'enter': .4, 'exit': .6}}, 'Histerezis'),
    ({'timezone': 'Mars/Olympus'}, 'saat dilimi'),
    ({'surprise': 1}, 'Extra inputs'),
])
def test_config_rejects_invalid(changes, message):
    with pytest.raises(ValueError, match=message):
        config(**changes)


def test_invalid_file_is_reported_not_hidden(tmp_path, monkeypatch):
    (tmp_path / 'bad.yaml').write_text('camera_id: bad\nname: x\n')
    (tmp_path / 'good.json').write_text(__import__('json').dumps(BASE))
    monkeypatch.setenv('IZGE_CAMERA_CONFIG_DIRS', str(tmp_path))
    found = camera_config.load_all()
    assert isinstance(found['cam'], CameraConfig)
    assert isinstance(found['bad'], str) and 'bad.yaml' in found['bad']
    with pytest.raises(ValueError, match='geçersiz'):
        camera_config.load('bad')
    with pytest.raises(LookupError):
        camera_config.load('missing')


def test_filename_time_and_daylight():
    cfg = config(filename_time={'pattern': r'(?P<Y>\d{4})(?P<m>\d{2})(?P<d>\d{2})_(?P<H>\d{2})(?P<M>\d{2})'})
    assert cfg.recorded_at_from_filename('x_20261010_2359.mp4') == datetime(2026, 10, 10, 23, 59, tzinfo=IST)
    assert cfg.recorded_at_from_filename('x_20261310_2359.mp4') is None   # month 13
    assert cfg.recorded_at_from_filename('kayit.mp4') is None
    assert cfg.is_daytime(datetime(2026, 1, 1, 12, tzinfo=IST))
    assert not cfg.is_daytime(datetime(2026, 1, 1, 2, tzinfo=IST))
    night_shift = config(daylight={'start': '20:00', 'end': '04:00'})
    assert night_shift.is_daytime(datetime(2026, 1, 1, 1, tzinfo=IST))


def test_yaml_parse():
    cfg = parse_config('camera_id: c\nname: n\nbarn: b\nreference_resolution: {width: 100, height: 50}\n'
                       'stalls:\n  - {id: X, sira: s, mesafe: orta, polygon: [[0,0],[50,0],[50,50]]}\n')
    assert cfg.stalls[0].points() == [(0, 0), (50, 0), (50, 50)]


# --- crops --------------------------------------------------------------------------------------

def test_geometry_scaling_and_aspect_check():
    cfg = config()
    assert geometries(cfg, 640, 360)[0].bbox == (0, 0, 320, 360)
    with pytest.raises(ValueError, match='en-boy'):
        geometries(cfg, 400, 400)


def test_polygon_mask_fills_outside():
    cfg = config(stalls=[{'id': 'T', 'sira': 'orta', 'mesafe': 'yakin', 'polygon': [[0, 0], [100, 0], [0, 100]]}])
    frame = np.full((180, 320, 3), 200, dtype=np.uint8)
    out = crop(frame, geometries(cfg, 320, 180)[0])
    assert out.shape == (100, 100, 3)
    assert tuple(out[5, 5]) == (200, 200, 200) and tuple(out[95, 95]) == FILL
    assert letterbox(out[:, :50], 32).shape == (32, 32, 3)


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    return stall_video(tmp_path_factory.mktemp('video') / 'SENTETIK_20261010_143000.mp4')


def test_synthetic_video_crops_follow_truth(video):
    cfg = camera_config.load('sentetik_test')
    geoms = geometries(cfg, 320, 180)
    seen = []
    for second, frame in _frames(video, 0, 90, threading.Event()):
        image = frame.image.to_ndarray(format='rgb24')
        for g in geoms:
            r, _, b = crop(image, g).reshape(-1, 3).mean(0)
            observed = 'yemde' if r > 128 else 'yemde_degil' if b > 128 else 'gorunmuyor'
            seen.append(observed == truth(g.stall_id, second))
    # 1 Hz on PTS buckets: one sample per whole second, never past the end of the file.
    assert len(seen) == 2 * 90
    assert sum(seen) >= len(seen) - 4   # codec transitions may blur a boundary frame


def test_stratified_sample_times_cover_each_clock_hour():
    cfg = config(sampling={'per_stratum': 3})
    start = datetime(2026, 10, 10, 13, 50, tzinfo=IST)
    times = sample_times(cfg, start, 0, 3 * 3600, 'seed')
    hours = [(start.timestamp() + t) // 3600 for t in times]
    assert len(times) == 12 and len(set(hours)) == 4 and times == sorted(times)
    assert times == sample_times(cfg, start, 0, 3 * 3600, 'seed')   # deterministic per media


# --- smoothing (table-driven) -------------------------------------------------------------------

F, N, V = (.9, .05, .05), (.05, .9, .05), (.05, .05, .9)
P = SmoothingParams(window_seconds=1, enter=.6, exit=.4, gap_merge_seconds=30)


def seq(*parts):
    probs = []
    for p, n in parts:
        probs += [p] * n
    return list(range(len(probs))), probs


def spans(segments):
    return [(s.state, s.start, s.end) for s in segments]


@pytest.mark.parametrize('name, parts, params, expected', [
    ('steady feeding', [(F, 10)], P, [('yemde', 0, 10)]),
    ('short not-feeding gap merges', [(F, 10), (N, 29), (F, 5)], P, [('yemde', 0, 44)]),
    ('gap of exactly the limit does not merge', [(F, 10), (N, 30), (F, 5)], P,
     [('yemde', 0, 10), ('yemde_degil', 10, 40), ('yemde', 40, 45)]),
    ('short not-visible gap merges', [(F, 10), (V, 12), (F, 3)], P, [('yemde', 0, 25)]),
    ('long not-visible stays separate', [(F, 5), (V, 60), (F, 5)], P,
     [('yemde', 0, 5), ('gorunmuyor', 5, 65), ('yemde', 65, 70)]),
    ('trailing gap is not merged', [(F, 5), (N, 3)], P, [('yemde', 0, 5), ('yemde_degil', 5, 8)]),
    ('hysteresis keeps feeding at 0.5', [(F, 3), ((.5, .45, .05), 4), (N, 2)], P,
     [('yemde', 0, 7), ('yemde_degil', 7, 9)]),
    ('hysteresis needs enter threshold', [(N, 2), ((.5, .45, .05), 4)], P, [('yemde_degil', 0, 6)]),
    ('window smooths a one-second spike', [(N, 5), (F, 1), (N, 5)], SmoothingParams(window_seconds=5),
     [('yemde_degil', 0, 11)]),
    ('multiple gaps chain into one bout', [(F, 2), (N, 10), (F, 2), (V, 10), (F, 2)], P, [('yemde', 0, 26)]),
])
def test_smoothing_table(name, parts, params, expected):
    offsets, probs = seq(*parts)
    assert spans(smooth(offsets, probs, len(offsets), params)) == expected, name


def test_last_sample_is_clipped_to_range_end():
    assert spans(smooth([0, 1, 2], [F, F, F], 2.4, P)) == [('yemde', 0, 2.4)]


def test_missing_seconds_are_unobserved_unless_inside_a_merged_bout():
    segs = smooth([0, 1, 5, 6], [N, N, N, N], 7, P)
    assert spans(segs) == [('yemde_degil', 0, 2), ('yemde_degil', 5, 7)]
    merged = smooth([0, 1, 5, 6], [F, F, F, F], 7, P)
    assert spans(merged) == [('yemde', 0, 7)]


def test_merged_bout_confidence_reflects_gap():
    plain = smooth(*seq((F, 10)), 10, P)[0].confidence
    merged = smooth(*seq((F, 5), (N, 5), (F, 5)), 15, P)[0].confidence
    assert plain == pytest.approx(.9) and merged == pytest.approx((10 * .9 + 5 * .05) / 15)


# --- daily summaries ----------------------------------------------------------------------------

def test_daily_summary_splits_istanbul_midnight_and_counts_bouts_on_start_day():
    start = datetime(2026, 10, 10, 23, 50, tzinfo=IST)
    segments = [Segment('yemde', 0, 1200, .8), Segment('yemde_degil', 1200, 1500, .6),
                Segment('gorunmuyor', 1500, 1600, .5), Segment('yemde', 1600, 1700, .9)]
    days = {d.day: d for d in daily_summaries(segments, start, 'Europe/Istanbul')}
    assert days['2026-10-10'].feeding_seconds == 600 and days['2026-10-10'].bout_count == 1
    d = days['2026-10-11']
    assert (d.feeding_seconds, d.not_feeding_seconds, d.not_visible_seconds, d.bout_count) == (700, 300, 100, 1)
    assert d.observed_seconds == 1100
    assert d.mean_confidence == pytest.approx((600 * .8 + 300 * .6 + 100 * .5 + 100 * .9) / 1100)


# --- classifier ---------------------------------------------------------------------------------

def test_classifier_round_trip_and_class_requirement():
    rng = np.random.default_rng(0)
    centers = {'yemde': [1, 0, 0], 'yemde_degil': [0, 0, 1], 'gorunmuyor': [0, 0, 0]}
    labels = [k for k in centers for _ in range(20)]
    x = np.array([np.array(centers[k]) + rng.normal(0, .05, 3) for k in labels])
    model = fit(x, labels, 'stub')
    restored = LinearClassifier.from_bytes(model.to_bytes())
    pred = restored.predict_proba(x)
    assert np.allclose(pred.sum(1), 1) and (pred.argmax(1) == [['yemde', 'yemde_degil', 'gorunmuyor'].index(k) for k in labels]).all()
    with pytest.raises(ValueError, match='eksik'):
        fit(x[:40], labels[:40], 'stub')


def test_stub_backbone_shape():
    out = StubBackbone().embed([np.zeros((10, 20, 3), dtype=np.uint8)])
    assert out.shape == (1, 6)
