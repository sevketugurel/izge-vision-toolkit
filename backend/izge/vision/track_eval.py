"""CLEAR-MOT style tracking metrics (MOTA, ID switches, fragmentation) for labelled sequences.

Boxes are xyxy per frame. Matching keeps the previous correspondence when it still overlaps
(as in CLEAR-MOT) so an identity switch is only counted when the match really changes.
"""
from collections import defaultdict

import numpy as np

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
