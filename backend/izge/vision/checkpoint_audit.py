"""Read-only query row audit: tensor evidence does not infer training-time factorisation."""

import numpy as np


def audit_query_rows(checkpoint: dict, loaded: dict, num_queries: int, group_detr: int) -> dict:
    if type(num_queries) is not int or type(group_detr) is not int or min(num_queries, group_detr) <= 0:
        raise ValueError("Explicit native query/group configuration required")
    result = {}
    for name in ("refpoint_embed.weight", "query_feat.weight"):
        original, current = np.asarray(checkpoint[name]), np.asarray(loaded[name])
        if original.ndim != 2 or current.shape != (num_queries * group_detr, original.shape[1]):
            raise ValueError("Loaded native query tensor shape mismatch")
        if not np.isfinite(original).all() or not np.isfinite(current).all():
            raise ValueError("Non-finite query tensor")
        result[name] = {
            "checkpoint_shape": list(original.shape),
            "loaded_shape": list(current.shape),
            "all_loaded_rows_equal_checkpoint_prefix": bool(
                np.array_equal(current, original[: len(current)])
            ),
            "eval_rows": [0, num_queries],
            "eval_prefix_equal_checkpoint": bool(
                np.array_equal(current[:num_queries], original[:num_queries])
            ),
        }
    return {
        "queries": result,
        "native_num_queries": num_queries,
        "native_group_detr": group_detr,
        "checkpoint_factorisation_known": False,
        "training_teacher_accepted": False,
        "inference_interpretation": "Eval reads first native num_queries rows, not all packed training groups; prefix equality does not establish field accuracy",
        "training_interpretation": "All native query groups participate in training; missing checkpoint partition metadata leaves warm-start group layout unproven",
    }
