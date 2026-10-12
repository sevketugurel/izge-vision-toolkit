#!/usr/bin/env python3
"""Native published model audit; no checkpoint/config rewrite, training, resume or detections."""

import argparse
import inspect
import json
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.artifact_io import checksum, prepare_output, write_json  # noqa: E402
from izge.vision.checkpoint_audit import audit_query_rows  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--lock", type=Path, default=Path("experiments/rfdetr_seg/large.model.lock.json"))
p.add_argument("--weights", type=Path, default=Path("models/rfdetr/rf-detr-seg-large.pt"))
p.add_argument("--raw-frame", type=Path, required=True)
p.add_argument("--output-dir", type=Path, required=True)
a = p.parse_args()
lock = json.loads(a.lock.read_text())
if version("rfdetr") != lock["package_version"] or checksum(a.weights) != lock["weights_sha256"]:
    raise ValueError("Pinned package/official checkpoint checksum mismatch")
import torch  # noqa: E402
from PIL import Image  # noqa: E402
from rfdetr import RFDETRSegLarge  # noqa: E402
from torchvision.transforms import functional as F  # noqa: E402

started = perf_counter()
original = torch.load(a.weights, map_location="cpu", weights_only=False)
facade = RFDETRSegLarge(pretrain_weights=str(a.weights.resolve()), device="cpu")
native = facade.model.model.cpu().eval()
state = native.state_dict()
ckpt = original["model"]
report = audit_query_rows(
    {k: ckpt[k].numpy() for k in ("refpoint_embed.weight", "query_feat.weight")},
    {k: state[k].numpy() for k in ("refpoint_embed.weight", "query_feat.weight")},
    facade.model_config.num_queries,
    facade.model_config.group_detr,
)
shared = set(state) & set(ckpt)
shape_changes = {
    k: {"checkpoint": list(ckpt[k].shape), "loaded": list(state[k].shape)}
    for k in sorted(shared)
    if hasattr(ckpt[k], "shape") and ckpt[k].shape != state[k].shape
}
value_changes = [
    k
    for k in sorted(shared)
    if hasattr(ckpt[k], "shape") and ckpt[k].shape == state[k].shape and not torch.equal(ckpt[k], state[k])
]
hook_result = {}


def capture(module, args):
    for i, key in ((3, "refpoint_embed.weight"), (4, "query_feat.weight")):
        hook_result[key] = {
            "shape_passed_to_transformer": list(args[i].shape),
            "exact_checkpoint_prefix": torch.equal(
                args[i].cpu(), ckpt[key][: facade.model_config.num_queries]
            ),
        }


handle = native.transformer.register_forward_pre_hook(capture)
with Image.open(a.raw_frame) as image:
    tensor = F.normalize(
        F.resize(F.to_tensor(image.convert("RGB")), [504, 504], antialias=False), facade.means, facade.stds
    )
with torch.inference_mode():
    output = native(tensor.unsqueeze(0))
handle.remove()
args = original.get("args")
get = args.get if isinstance(args, dict) else lambda k: getattr(args, k, None)
unexplained_missing = sorted(set(state) - set(ckpt) - {"_kp_active_mask"})
unexpected = sorted(set(ckpt) - set(state))
passed = (
    all(q["eval_prefix_equal_checkpoint"] for q in report["queries"].values())
    and all(q["exact_checkpoint_prefix"] for q in hook_result.values())
    and not unexplained_missing
    and not unexpected
    and not value_changes
    and set(shape_changes) <= {"refpoint_embed.weight", "query_feat.weight"}
)
report.update(
    schema_version="large-query-audit-2",
    status="native_load_and_eval_probe_passed" if passed else "audit_failed",
    package_version=version("rfdetr"),
    lock_sha256=checksum(a.lock),
    weights_sha256=checksum(a.weights),
    raw_frame_sha256=checksum(a.raw_frame),
    device="cpu",
    resolution=facade.model_config.resolution,
    checkpoint_num_queries=get("num_queries"),
    checkpoint_group_detr=get("group_detr"),
    checkpoint_keys=sorted(original),
    missing_keys=sorted(set(state) - set(ckpt)),
    unexplained_missing_keys=unexplained_missing,
    unexpected_keys=unexpected,
    shape_changes=shape_changes,
    same_shape_value_changes=value_changes,
    eval_transformer_probe=hook_result,
    eval_output_shapes={k: list(v.shape) for k, v in output.items() if torch.is_tensor(v)},
    native_forward_source_sha256=checksum(Path(inspect.getfile(type(native)))),
    total_seconds=perf_counter() - started,
    training_forward_run=False,
    resume_run=False,
    weights_file_unchanged=checksum(a.weights) == lock["weights_sha256"],
    unsupported_factorisation_override=False,
    warning="Official legacy flat-slice warning retained. Fewer detections are not proved to be caused by this warning.",
)
prepare_output(a.output_dir)
write_json(a.output_dir / "audit.json", report)
print(json.dumps(report, indent=2))
if not passed:
    sys.exit(2)
