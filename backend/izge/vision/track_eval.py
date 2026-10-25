"""CLEAR-MOT style tracking metrics (MOTA, ID switches, fragmentation) for labelled sequences.

Boxes are xyxy per frame. Matching keeps the previous correspondence when it still overlaps
(as in CLEAR-MOT) so an identity switch is only counted when the match really changes.
"""
from collections import defaultdict

import numpy as np
from scipy.optimize import linear_sum_assignment  # scipy comes with scikit-learn

from .tracking import greedy_match, iou_matrix


def evaluate_tracking(truth: list[dict], predicted: list[dict], iou_threshold: float = 0.5) -> dict:
    """`truth[i]` / `predicted[i]` map object/tracklet id -> xyxy box for frame i (same frame order)."""
    if len(truth) != len(predicted):
        raise ValueError('Truth and prediction need the same number of frames')
    previous: dict = {}   # truth id -> tracklet id of the last match
    tp = fn = fp = switches = fragments = 0
    gt_total = 0
    tracked_frames: dict = defaultdict(int)
    lifetime: dict = defaultdict(int)
    was_tracked: dict = {}
    for gt, pred in zip(truth, predicted, strict=True):
        gt_ids, pr_ids = list(gt), list(pred)
        gt_total += len(gt_ids)
        iou = iou_matrix(np.array([gt[i] for i in gt_ids]), np.array([pred[i] for i in pr_ids]))
        matched: dict = {}
        for gi, g in enumerate(gt_ids):  # keep earlier correspondences first
            p = previous.get(g)
            if p in pred and iou[gi, pr_ids.index(p)] >= iou_threshold:
                matched[g] = p
        used_g = {gt_ids.index(g) for g in matched}
        used_p = {pr_ids.index(p) for p in matched.values()}
        free_g = [i for i in range(len(gt_ids)) if i not in used_g]
        free_p = [j for j in range(len(pr_ids)) if j not in used_p]
        pairs, _, _ = greedy_match(iou[np.ix_(free_g, free_p)] if free_g and free_p else np.zeros((0, 0)),
                                   iou_threshold)
        for a, b in pairs:
            matched[gt_ids[free_g[a]]] = pr_ids[free_p[b]]
        for g, p in matched.items():
            if g in previous and previous[g] != p:
                switches += 1
            previous[g] = p
        tp += len(matched)
        fn += len(gt_ids) - len(matched)
        fp += len(pr_ids) - len(matched)
        for g in gt_ids:
            lifetime[g] += 1
            hit = g in matched
            tracked_frames[g] += hit
            if hit and was_tracked.get(g) is False:
                fragments += 1
            was_tracked[g] = hit
    mostly = sum(1 for g, n in lifetime.items() if tracked_frames[g] / n >= 0.8)
    return {'frames': len(truth), 'gt_boxes': gt_total, 'true_positives': tp, 'false_negatives': fn,
            'false_positives': fp, 'id_switches': switches, 'fragmentations': fragments,
            'mota': None if gt_total == 0 else 1 - (fn + fp + switches) / gt_total,
            'recall': None if gt_total == 0 else tp / gt_total,
            'precision': None if tp + fp == 0 else tp / (tp + fp),
            'gt_tracks': len(lifetime), 'mostly_tracked_tracks': mostly, 'iou_threshold': iou_threshold}


def _frame_similarities(truth: list[dict], predicted: list[dict]):
    if len(truth) != len(predicted):
        raise ValueError('Truth and prediction need the same number of frames')
    for gt, pred in zip(truth, predicted, strict=True):
        gt_ids, pr_ids = list(gt), list(pred)
        sim = iou_matrix(np.array([gt[i] for i in gt_ids]), np.array([pred[i] for i in pr_ids]))
        yield gt_ids, pr_ids, sim


def evaluate_identity(truth: list[dict], predicted: list[dict], iou_threshold: float = 0.5) -> dict:
    """IDF1 (Ristani et al. 2016): one global one-to-one assignment of truth to predicted trajectories.

    Unlike MOTA, a track that changes id halfway is penalised by how many frames the best single id covers.
    """
    frames = list(_frame_similarities(truth, predicted))
    gt_len: dict = defaultdict(int)
    pr_len: dict = defaultdict(int)
    overlap: dict = defaultdict(int)  # (truth id, predicted id) -> frames with IoU >= threshold
    for gt_ids, pr_ids, sim in frames:
        for g in gt_ids:
            gt_len[g] += 1
        for p in pr_ids:
            pr_len[p] += 1
        for gi, pi in zip(*np.nonzero(sim >= iou_threshold), strict=True):
            overlap[(gt_ids[gi], pr_ids[pi])] += 1
    g_list, p_list = list(gt_len), list(pr_len)
    gain = np.zeros((len(g_list), len(p_list)))
    for (g, p), n in overlap.items():
        gain[g_list.index(g), p_list.index(p)] = n
    idtp = 0
    if gain.size:
        rows, cols = linear_sum_assignment(-gain)
        idtp = int(gain[rows, cols].sum())
    gt_total, pr_total = sum(gt_len.values()), sum(pr_len.values())
    idfn, idfp = gt_total - idtp, pr_total - idtp
    denom = 2 * idtp + idfp + idfn
    return {'frames': len(frames), 'gt_boxes': gt_total, 'pred_boxes': pr_total, 'idtp': idtp, 'idfn': idfn,
            'idfp': idfp, 'idf1': None if denom == 0 else 2 * idtp / denom,
            'idp': None if pr_total == 0 else idtp / pr_total,
            'idr': None if gt_total == 0 else idtp / gt_total, 'iou_threshold': iou_threshold}


HOTA_ALPHAS = tuple(round(0.05 * k, 2) for k in range(1, 20))


def evaluate_hota(truth: list[dict], predicted: list[dict], alphas: tuple[float, ...] = HOTA_ALPHAS) -> dict:
    """HOTA, DetA and AssA (Luiten et al. 2021) averaged over IoU thresholds `alphas`.

    Follows the TrackEval reference: a global truth/prediction alignment score weights the per-frame
    Hungarian matching so association quality influences which detections count as matched.
    """
    frames = list(_frame_similarities(truth, predicted))
    gt_count: dict = defaultdict(int)
    pr_count: dict = defaultdict(int)
    potential: dict = defaultdict(float)
    for gt_ids, pr_ids, sim in frames:
        for g in gt_ids:
            gt_count[g] += 1
        for p in pr_ids:
            pr_count[p] += 1
        for gi, g in enumerate(gt_ids):
            for pi, p in enumerate(pr_ids):
                potential[(g, p)] += sim[gi, pi]
    gt_total, pr_total = sum(gt_count.values()), sum(pr_count.values())
    per_alpha = []
    for alpha in alphas:
        tpa: dict = defaultdict(int)
        matches = []
        tp = 0
        for gt_ids, pr_ids, sim in frames:
            if not gt_ids or not pr_ids:
                continue
            score = np.zeros(sim.shape)
            for gi, g in enumerate(gt_ids):
                for pi, p in enumerate(pr_ids):
                    if sim[gi, pi] >= alpha - 1e-10:
                        score[gi, pi] = potential[(g, p)] / (gt_count[g] + pr_count[p] - potential[(g, p)]) * sim[gi, pi]
            rows, cols = linear_sum_assignment(-score)
            for r, c in zip(rows, cols, strict=True):
                if sim[r, c] >= alpha - 1e-10:
                    tpa[(gt_ids[r], pr_ids[c])] += 1
                    matches.append((gt_ids[r], pr_ids[c]))
                    tp += 1
        fn, fp = gt_total - tp, pr_total - tp
        det_a = tp / (tp + fn + fp) if tp + fn + fp else 0.0
        ass_a = 0.0
        if tp:
            ass_a = sum(tpa[m] / (gt_count[m[0]] + pr_count[m[1]] - tpa[m]) for m in matches) / tp
        per_alpha.append((det_a, ass_a))
    if gt_total == 0 and pr_total == 0:
        return {'hota': None, 'deta': None, 'assa': None, 'gt_boxes': 0, 'pred_boxes': 0, 'alphas': list(alphas)}
    det = np.array([d for d, _ in per_alpha])
    ass = np.array([a for _, a in per_alpha])
    return {'hota': float(np.sqrt(det * ass).mean()), 'deta': float(det.mean()), 'assa': float(ass.mean()),
            'hota_at': {str(a): float(np.sqrt(d * s)) for a, d, s in zip(alphas, det, ass, strict=True)},
            'gt_boxes': gt_total, 'pred_boxes': pr_total, 'alphas': list(alphas)}


def load_mot_file(path, is_truth: bool, num_frames: int | None = None) -> list[dict]:
    """Read a MOTChallenge txt (`frame,id,x,y,w,h,conf,...`, frame 1-based, xywh) into per-frame xyxy dicts.

    For ground truth, rows with conf == 0 are "ignore" regions and are skipped (MOT convention).
    """
    rows: dict[int, dict] = defaultdict(dict)
    last = 0
    with open(path, encoding='utf-8') as handle:
        for n, line in enumerate(handle, 1):
            if not line.strip():
                continue
            parts = line.strip().split(',')
            if len(parts) < 6:
                raise ValueError(f'{path}:{n}: expected at least 6 comma-separated fields')
            try:
                frame, oid = int(float(parts[0])), int(float(parts[1]))
                x, y, w, h = (float(v) for v in parts[2:6])
                conf = float(parts[6]) if len(parts) > 6 and parts[6].strip() else 1.0
            except ValueError as exc:
                raise ValueError(f'{path}:{n}: non-numeric field') from exc
            if frame < 1:
                raise ValueError(f'{path}:{n}: frame numbers start at 1')
            if w <= 0 or h <= 0:
                raise ValueError(f'{path}:{n}: box width/height must be positive')
            last = max(last, frame)
            if is_truth and conf == 0:
                continue
            if oid in rows[frame]:
                raise ValueError(f'{path}:{n}: duplicate id {oid} in frame {frame}')
            rows[frame][oid] = (x, y, x + w, y + h)
    total = last if num_frames is None else num_frames
    if num_frames is not None and last > num_frames:
        raise ValueError(f'{path}: frame {last} exceeds {num_frames} frames')
    return [rows.get(f, {}) for f in range(1, total + 1)]


def score_mot_files(gt_path, pred_path, iou_threshold: float = 0.5) -> dict:
    """MOTA/ID switches, IDF1 and HOTA for one labelled sequence and one tracker output."""
    truth = load_mot_file(gt_path, is_truth=True)
    predicted = load_mot_file(pred_path, is_truth=False)
    frames = max(len(truth), len(predicted))
    truth += [{} for _ in range(frames - len(truth))]
    predicted += [{} for _ in range(frames - len(predicted))]
    return {'clear_mot': evaluate_tracking(truth, predicted, iou_threshold),
            'identity': evaluate_identity(truth, predicted, iou_threshold), 'hota': evaluate_hota(truth, predicted)}
