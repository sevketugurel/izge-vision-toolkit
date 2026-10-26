"""IDF1 / HOTA known-answer tests. All sequences are SYNTHETIC boxes, not cattle footage."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from izge.vision.track_eval import evaluate_hota, evaluate_identity, load_mot_file, score_mot_files

ROOT = Path(__file__).resolve().parents[2]


def box(t, x0=10.0):
    return (x0 + 2 * t, 50.0, x0 + 2 * t + 30, 80.0)


def seq(n, ids):
    """`ids[t]` is the predicted/truth id for frame t, or None for no box."""
    return [{} if i is None else {i: box(t)} for t, i in enumerate(ids)]


TRUTH = seq(10, [1] * 10)


def test_perfect_tracking_scores_one():
    assert evaluate_identity(TRUTH, TRUTH)['idf1'] == pytest.approx(1)
    h = evaluate_hota(TRUTH, TRUTH)
    assert (h['hota'], h['deta'], h['assa']) == (pytest.approx(1),) * 3


def test_id_switch_halfway():
    pred = seq(10, [7] * 5 + [8] * 5)
    assert evaluate_identity(TRUTH, pred)['idf1'] == pytest.approx(0.5)  # 2*5 / (2*5+5+5)
    h = evaluate_hota(TRUTH, pred)
    assert h['deta'] == pytest.approx(1) and h['assa'] == pytest.approx(0.5)
    assert h['hota'] == pytest.approx(0.5 ** 0.5)


def test_missed_half_without_switch():
    pred = seq(10, [7] * 5 + [None] * 5)
    r = evaluate_identity(TRUTH, pred)
    assert r['idf1'] == pytest.approx(2 * 5 / 15) and r['idp'] == pytest.approx(1) and r['idr'] == pytest.approx(0.5)
    h = evaluate_hota(TRUTH, pred)
    assert h['deta'] == pytest.approx(0.5) and h['assa'] == pytest.approx(0.5) and h['hota'] == pytest.approx(0.5)


def test_false_positive_track_lowers_detection_not_association():
    pred = [{7: box(t), 9: box(t, x0=500.0)} for t in range(10)]
    h = evaluate_hota(TRUTH, pred)
    assert h['deta'] == pytest.approx(0.5) and h['assa'] == pytest.approx(1)
    assert evaluate_identity(TRUTH, pred)['idf1'] == pytest.approx(2 * 10 / (20 + 10))


def test_hota_drops_with_looser_localisation():
    shifted = [{7: (b[0] + 12, b[1], b[2] + 12, b[3])} for b in (f[1] for f in TRUTH)]  # IoU ~0.43
    h = evaluate_hota(TRUTH, shifted)
    assert h['hota_at']['0.3'] == pytest.approx(1) and h['hota_at']['0.5'] == 0


def test_empty_inputs_are_none_not_zero():
    assert evaluate_identity([{}, {}], [{}, {}])['idf1'] is None
    assert evaluate_hota([{}], [{}])['hota'] is None
    with pytest.raises(ValueError):
        evaluate_hota([{}], [{}, {}])


def write_mot(path, rows):
    path.write_text('\n'.join(','.join(str(v) for v in r) for r in rows) + '\n')


def test_loader_and_scoring(tmp_path):
    gt, pred = tmp_path / 'gt.txt', tmp_path / 'pred.txt'
    write_mot(gt, [(f, 1, 10 + 2 * f, 50, 30, 30, 1, 1, 1) for f in range(1, 6)] + [(1, 2, 0, 0, 5, 5, 0, 1, 1)])
    write_mot(pred, [(f, 4, 10 + 2 * f, 50, 30, 30, 0.9) for f in range(1, 4)])
    assert load_mot_file(gt, is_truth=True)[0].keys() == {1}  # conf 0 row ignored
    assert len(load_mot_file(gt, is_truth=True)) == 5
    r = score_mot_files(gt, pred)
    assert r['identity']['idtp'] == 3 and r['identity']['idfn'] == 2
    assert r['clear_mot']['false_negatives'] == 2 and r['hota']['assa'] == pytest.approx(0.6)


def test_loader_rejects_bad_rows(tmp_path):
    bad = tmp_path / 'bad.txt'
    write_mot(bad, [(1, 1, 0, 0, 10, 10), (1, 1, 5, 5, 10, 10)])
    with pytest.raises(ValueError, match='duplicate id'):
        load_mot_file(bad, is_truth=True)
    write_mot(bad, [(0, 1, 0, 0, 10, 10)])
    with pytest.raises(ValueError, match='start at 1'):
        load_mot_file(bad, is_truth=False)
    write_mot(bad, [(1, 1, 0, 0, 0, 10)])
    with pytest.raises(ValueError, match='positive'):
        load_mot_file(bad, is_truth=False)


@pytest.mark.parametrize('bad_value', ['nan', 'inf', '-inf'])
def test_loader_rejects_non_finite_values(tmp_path, bad_value):
    bad = tmp_path / 'bad.txt'
    bad.write_text(f'1,1,{bad_value},0,10,10,1\n1,2,0,0,{bad_value},10,1\n', encoding='utf-8')
    with pytest.raises(ValueError, match='non-finite'):
        load_mot_file(bad, is_truth=False)


def test_loader_caps_frame_number(tmp_path):
    bad = tmp_path / 'bad.txt'
    write_mot(bad, [(50_000_000, 1, 0, 0, 10, 10)])
    with pytest.raises(ValueError, match='frame limit'):
        load_mot_file(bad, is_truth=False)


def test_cli_labels_origin(tmp_path):
    gt = tmp_path / 'gt.txt'
    write_mot(gt, [(f, 1, 10 + 2 * f, 50, 30, 30, 1) for f in range(1, 6)])
    out = tmp_path / 'r.json'
    done = subprocess.run([sys.executable, str(ROOT / 'scripts/score_tracks.py'), '--gt', str(gt), '--pred', str(gt),
                           '--data-origin', 'synthetic', '--out', str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    data = json.loads(out.read_text())
    assert data['data_origin'] == 'synthetic' and data['identity']['idf1'] == 1 and data['hota']['hota'] == pytest.approx(1)
