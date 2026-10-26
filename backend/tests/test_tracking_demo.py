import json

from izge.vision import tracking_demo


def test_demo_is_deterministic_labelled_demo_and_meets_regression_floor(tmp_path):
    first = tracking_demo.main(tmp_path / 'a')
    second = tracking_demo.main(tmp_path / 'b')
    assert first == second
    assert first['origin'] == 'demo' and 'not a cattle model' in first['detector']
    m = first['metrics']
    # Floors from the observed run (recall 0.81, 1 ID switch); they guard against regressions only.
    assert m['gt_tracks'] == 3 and m['recall'] > 0.7 and m['id_switches'] <= 2
    assert json.loads((tmp_path / 'a' / 'tracking_demo.json').read_text()) == first


def test_timestamps_are_irregular_and_increasing():
    times = tracking_demo.timestamps_ms()
    gaps = {b - a for a, b in zip(times, times[1:])}
    assert all(g > 0 for g in gaps) and len(gaps) > 5


def test_written_video_preserves_exact_irregular_pts(tmp_path):
    from pathlib import Path

    from izge.video import FileVideoSource
    times = tracking_demo.timestamps_ms()
    video = tracking_demo.write_video(Path(tmp_path) / 'v.mkv', times)
    decoded = [round(e.offset * 1000) for e in FileVideoSource(video).read_events()]
    assert decoded == times   # encoder time base must not quantise PTS (it did before codec time_base was set)
