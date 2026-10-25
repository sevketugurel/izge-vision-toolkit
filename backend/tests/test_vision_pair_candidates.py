"""Pair candidate known-answer tests. All tracks are SYNTHETIC boxes, not cattle footage."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from izge.vision.pair_candidates import (
    Observation, PairConfig, build_review_queue, candidate_id, find_pair_candidates)

ROOT = Path(__file__).resolve().parents[2]
SHA = 'ab' * 32
H = 40.0


def box(cx, cy=100.0):
    return (cx - 15, cy - H / 2, cx + 15, cy + H / 2)


def track(tid, times, cx_fn, cy=100.0):
    return [Observation(tid, t, box(cx_fn(t), cy)) for t in times]


TIMES = [round(0.1 * i, 3) for i in range(31)]  # irregular spacing is exercised below


def test_close_pair_found_far_pair_ignored():
    obs = track(1, TIMES, lambda t: 100) + track(2, TIMES, lambda t: 120) + track(3, TIMES, lambda t: 900)
    cands = find_pair_candidates(obs)
    assert [(c.tracklet_a, c.tracklet_b) for c in cands] == [(1, 2)]
    c = cands[0]
    assert c.span_s == pytest.approx(3.0) and c.sample_count == 31
    assert c.min_distance == pytest.approx(0.5)          # 20 px apart / 40 px height
    assert c.max_overlap == pytest.approx(10 / 30)       # 30-wide boxes shifted by 20 px
    assert c.mean_relative_speed == pytest.approx(0) and not c.approach


def test_approach_and_relative_speed():
    # B closes in from 2.0 to 0.2 heights over 2 s at constant speed 0.9 heights/s.
    ts = [i * 0.25 for i in range(9)]
    obs = track(1, ts, lambda t: 100) + track(2, ts, lambda t: 100 + H * (2.0 - 0.9 * t))
    cfg = PairConfig(near_distance=2.0, min_span_s=1.0)
    (c,) = find_pair_candidates(obs, cfg)
    assert c.approach and c.mean_relative_speed == pytest.approx(0.9)
    assert c.min_distance == pytest.approx(0.2)


def test_uses_pts_offsets_not_sample_count():
    # Same samples, irregular PTS: 3 samples spanning 0.3 s fail min_span; spanning 3 s pass.
    quick = [0.0, 0.1, 0.3]
    slow = [0.0, 0.9, 3.0]
    mk = lambda ts: track(1, ts, lambda t: 100) + track(2, ts, lambda t: 110)  # noqa: E731
    assert find_pair_candidates(mk(quick)) == []
    assert find_pair_candidates(mk(slow), PairConfig(max_gap_s=3.0))[0].span_s == pytest.approx(3.0)


def test_gap_splits_segments_and_gap_time_is_not_counted():
    ts = [0, 0.5, 1.0, 1.5, 5.0, 5.5, 6.0, 6.5]  # 3.5 s hole in observation
    obs = track(1, ts, lambda t: 100) + track(2, ts, lambda t: 110)
    cands = find_pair_candidates(obs)
    assert sorted((c.start_s, c.end_s) for c in cands) == [(0, 1.5), (5.0, 6.5)]
    assert all(c.span_s == pytest.approx(1.5) for c in cands)


def test_last_observation_not_extended_and_single_track_leaves_nothing():
    obs = track(1, TIMES, lambda t: 100) + track(2, TIMES[:11], lambda t: 110)
    (c,) = find_pair_candidates(obs)
    assert c.end_s == pytest.approx(1.0)                  # not extended to the end of track 1
    assert find_pair_candidates(track(1, TIMES, lambda t: 100)) == []


def test_ranking_prefers_longer_closer_pair():
    ts = [i * 0.25 for i in range(21)]
    obs = (track(1, ts, lambda t: 100) + track(2, ts, lambda t: 110)       # 5 s, very close
           + track(3, ts[:9], lambda t: 100, 600.0) + track(4, ts[:9], lambda t: 130, 600.0))  # 2 s, further
    cands = find_pair_candidates(obs)
    assert [(c.tracklet_a, c.tracklet_b) for c in cands] == [(1, 2), (3, 4)]
    assert cands[0].rank_score > cands[1].rank_score


def test_review_queue_contract():
    obs = track(1, TIMES, lambda t: 100) + track(2, TIMES, lambda t: 120)
    cfg = PairConfig()
    q = build_review_queue(SHA, find_pair_candidates(obs, cfg), cfg, 'synthetic')
    assert q['data_origin'] == 'synthetic' and q['source_sha256'] == SHA
    (item,) = q['candidates']
    assert item['origin'] == 'model' and item['review_status'] == 'unreviewed'
    assert item['tracklet_ids'] == [1, 2] and item['tracklet_ids_local'] is True
    assert (item['pts_start_s'], item['pts_end_s']) == (0.0, 3.0)
    assert item['duration_beyond_span'] == 'unknown' and item['rank'] == 1
    assert len(item['candidate_id']) == 16
    assert json.loads(json.dumps(q)) == q
    c = find_pair_candidates(obs, cfg)[0]
    assert candidate_id(SHA, c) == item['candidate_id'] != candidate_id('cd' * 32, c)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        PairConfig(near_distance=0)


def test_cli_roundtrip(tmp_path):
    rows = [{'tracklet_id': tid, 'offset_s': t, 'box': list(box(100 if tid == 1 else 120))}
            for tid in (1, 2) for t in TIMES]
    src, out = tmp_path / 'tracks.json', tmp_path / 'q.json'
    src.write_text(json.dumps({'source_sha256': SHA, 'observations': rows}))
    cmd = [sys.executable, str(ROOT / 'scripts/pair_candidates.py'), '--tracks', str(src), '--out', str(out)]
    assert subprocess.run(cmd, capture_output=True).returncode != 0   # --data-origin is required
    r = subprocess.run(cmd + ['--data-origin', 'synthetic'], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())['candidates'][0]['origin'] == 'model'
