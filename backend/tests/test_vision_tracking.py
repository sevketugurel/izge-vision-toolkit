import numpy as np
import pytest

from izge.vision.motion_baseline import MotionBlobDetector
from izge.vision.track_eval import evaluate_tracking
from izge.vision.tracking import ByteTrackLite, TrackerConfig, greedy_match, iou_matrix


class Det:
    def __init__(self, box, score=0.9):
        self.box, self.score = box, score


def moving(t, vx=20.0, x0=10.0):
    x = x0 + vx * t
    return (x, 50.0, x + 30, 80.0)


def test_iou_matrix_and_greedy_match():
    iou = iou_matrix([(0, 0, 10, 10)], [(0, 0, 10, 10), (5, 0, 15, 10), (50, 50, 60, 60)])
    assert iou[0, 0] == 1 and iou[0, 1] == pytest.approx(1 / 3) and iou[0, 2] == 0
    assert iou_matrix([], [(0, 0, 1, 1)]).shape == (0, 1)
    matches, ut, ud = greedy_match(np.array([[0.9, 0.8], [0.85, 0.1]]), 0.3)
    assert matches == [(0, 0)] and ut == [1] and ud == [1]


def test_identity_survives_jittered_timestamps_and_ids_are_not_reused():
    tracker = ByteTrackLite()
    times = [0.0, 0.09, 0.2, 0.31, 0.4, 0.52, 0.6]
    ids = [tracker.update(t, [Det(moving(t))])[0].tracklet_id for t in times]
    assert len(set(ids)) == 1
    assert tracker.update(10.0, [Det(moving(10.0))])[0].tracklet_id != ids[0]  # lost for > max_lost_s


def test_low_score_extends_but_never_starts_a_track():
    tracker = ByteTrackLite()
    assert tracker.update(0.0, [Det(moving(0), 0.2)]) == []
    first = tracker.update(0.1, [Det(moving(0.1), 0.9)])[0].tracklet_id
    low = tracker.update(0.2, [Det(moving(0.2), 0.2)])
    assert [o.tracklet_id for o in low] == [first]
    assert tracker.update(0.3, [Det(moving(0.3), 0.05)]) == []  # below low_score: ignored


def test_confirmation_needs_min_hits_and_predicted_only_tracks_are_not_reported():
    tracker = ByteTrackLite(TrackerConfig(min_hits=3))
    flags = [tracker.update(t, [Det(moving(t))])[0].confirmed for t in (0.0, 0.1, 0.2)]
    assert flags == [False, False, True]
    assert tracker.update(0.3, []) == []


def test_two_objects_keep_separate_ids():
    tracker = ByteTrackLite()
    for t in np.arange(0, 1, 0.1):
        out = tracker.update(float(t), [Det((10 + 20 * t, 10, 40 + 20 * t, 40)), Det((300 - 20 * t, 100, 330 - 20 * t, 130))])
    assert len({o.tracklet_id for o in out}) == 2


def test_offsets_must_increase_and_config_validates():
    tracker = ByteTrackLite()
    tracker.update(1.0, [])
    with pytest.raises(ValueError):
        tracker.update(1.0, [])
    with pytest.raises(ValueError):
        TrackerConfig(high_score=0.1, low_score=0.5)


def test_evaluation_counts_switch_fragment_and_perfect_case():
    box = (0, 0, 10, 10)
    truth = [{1: box}] * 6
    assert evaluate_tracking(truth, [{7: box}] * 6)['mota'] == 1
    switched = evaluate_tracking(truth, [{7: box}] * 3 + [{8: box}] * 3)
    assert switched['id_switches'] == 1 and switched['fragmentations'] == 0
    frag = evaluate_tracking(truth, [{7: box}, {7: box}, {}, {}, {7: box}, {7: box}])
    assert frag['fragmentations'] == 1 and frag['false_negatives'] == 2 and frag['id_switches'] == 0
    assert evaluate_tracking([{}], [{3: box}])['mota'] is None
    with pytest.raises(ValueError):
        evaluate_tracking(truth, [])


def test_motion_blob_detector_finds_moving_square_only():
    detector = MotionBlobDetector()
    empty = np.full((60, 80, 3), 60, np.uint8)
    assert detector.detect(empty) == []
    frame = empty.copy()
    frame[10:30, 20:50] = 220
    found = detector.detect(frame)
    assert len(found) == 1 and found[0].box == (20.0, 10.0, 50.0, 30.0)
