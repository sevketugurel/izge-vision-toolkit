"""Hand-computed check of the MOT scoring CLI on a synthetic scene (not real cattle data).

One ground-truth track over 10 frames; the prediction follows it perfectly but changes ID after frame 5.
  MOTA = 1 - (FN + FP + IDSW) / GT = 1 - 1/10 = 0.9
  IDTP = IDFN = IDFP = 5  ->  IDF1 = 2*5 / (2*5 + 5 + 5) = 0.5
  DetA = 1, AssA = 0.5    ->  HOTA = sqrt(1 * 0.5)
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_score_tracks_matches_hand_computed_values(tmp_path):
    gt = [f'{f},1,{100 + f},200,50,80,1,1,1' for f in range(1, 11)]
    pred = [f'{f},{1 if f <= 5 else 2},{100 + f},200,50,80,0.9,-1,-1' for f in range(1, 11)]
    (tmp_path / 'gt.txt').write_text('\n'.join(gt) + '\n')
    (tmp_path / 'pred.txt').write_text('\n'.join(pred) + '\n')
    out = tmp_path / 'score.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/score_tracks.py'), '--gt', str(tmp_path / 'gt.txt'),
                    '--pred', str(tmp_path / 'pred.txt'), '--data-origin', 'synthetic', '--out', str(out)],
                   check=True, capture_output=True)
    r = json.loads(out.read_text())
    assert r['data_origin'] == 'synthetic'
    assert r['clear_mot']['id_switches'] == 1
    assert abs(r['clear_mot']['mota'] - 0.9) < 1e-9
    assert abs(r['identity']['idf1'] - 0.5) < 1e-9
    assert abs(r['hota']['hota'] - 0.5 ** 0.5) < 1e-9
