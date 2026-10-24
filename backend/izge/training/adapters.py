"""Adapters preserve the existing classifier and guarded RF-DETR recipe."""
import os
import platform
import subprocess
import shutil
import time

import numpy as np
from sklearn.metrics import log_loss, precision_recall_fscore_support

from .config import ROOT, TrainingPrerequisiteError, data_path, load_manifest


def classification_metrics(labels, probabilities):
    precision, recall, f1, _ = precision_recall_fscore_support(
        labels, probabilities.argmax(axis=1), average='macro', zero_division=0)
    return dict(val_loss=float(log_loss(labels, probabilities, labels=list(range(probabilities.shape[1])))),
                precision=float(precision), recall=float(recall), f1=float(f1))


def feeding(config, run_dir, resume, emit):
    from izge.feeding.classifier import fit, LinearClassifier
    from izge.feeding.states import STATES
    from izge.feeding.backbone import DINOV3_ID
    manifest = load_manifest(config)
    # Precomputed embeddings need no token. Never fetch a different backbone implicitly.
    if manifest.get('backbone') != DINOV3_ID:
        raise TrainingPrerequisiteError('feeding manifest requires the existing DINOv3 backbone embeddings')
    rows = manifest.get('samples', [])
    train = [r for r in rows if r.get('split') == 'train']
    val = [r for r in rows if r.get('split') == 'val']
    if not train or not val:
        raise TrainingPrerequisiteError('feeding needs labeled train/val embeddings; extract with HF_TOKEN before training')
    train_groups = {r['group_id'] for r in train}
    val_groups = {r['group_id'] for r in val}
    test_groups = {r['group_id'] for r in rows if r.get('split') == 'test'}
    if train_groups & val_groups or train_groups & test_groups or val_groups & test_groups:
        raise TrainingPrerequisiteError('recording-day/session groups overlap across splits')
    if any(r.get('origin') != 'manual' or r.get('gold', False) for r in train + val):
        raise TrainingPrerequisiteError('feeding requires manual labels and excludes gold samples')
    def arrays(selected):
        return (np.stack([np.load(data_path(r['embedding']), allow_pickle=False) for r in selected]),
                [r['label'] for r in selected])
    x, labels = arrays(train)
    vx, vlabels = arrays(val)
    start = time.monotonic()
    checkpoint = run_dir / 'checkpoints/last.pt'
    model = LinearClassifier.from_bytes(checkpoint.read_bytes()) if resume else fit(
        x, labels, DINOV3_ID, config.params.c)
    checkpoint.write_bytes(model.to_bytes())  # Existing safe NPZ format, despite common checkpoint filename.
    (checkpoint.parent / 'best.pt').write_bytes(model.to_bytes())
    train_probs = model.predict_proba(x)
    emit(dict(epoch=1, train_loss=float(log_loss([STATES.index(v) for v in labels], train_probs,
                                               labels=list(range(3)))),
              **classification_metrics([STATES.index(v) for v in vlabels], model.predict_proba(vx)),
              duration_s=time.monotonic() - start))


def rfdetr(config, run_dir, resume, emit):
    manifest = load_manifest(config)
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise TrainingPrerequisiteError('existing RF-DETR recipe requires separate Linux x86_64 Python 3.12 CUDA environment')
    if not config.params.weights or not data_path(config.params.weights).is_file():
        raise TrainingPrerequisiteError('RF-DETR approved weights missing under IZGE_DATA_ROOT')
    if 'dataset_dir' not in manifest:
        raise TrainingPrerequisiteError('RF-DETR manifest needs dataset_dir for guarded human COCO export')
    uv = shutil.which('uv')
    if uv is None:
        raise TrainingPrerequisiteError('uv is required for the isolated RF-DETR Python 3.12 environment')
    command = [uv, 'run', '--project', str(ROOT / 'experiments/rfdetr_training'), '--frozen',
               'python', str(ROOT / 'experiments/rfdetr_training/train.py'),
               '--dataset', str(data_path(manifest['dataset_dir'])), '--weights', str(data_path(config.params.weights)),
               '--output-dir', str(run_dir / 'rfdetr'), '--epochs', str(config.params.epochs),
               '--batch-size', str(config.params.batch_size), '--grad-accum-steps',
               str(config.params.grad_accum_steps), '--allow-training']
    if resume:
        checkpoints = sorted((run_dir / 'rfdetr').glob('checkpoint_*.ckpt'), key=os.path.getmtime)
        if not checkpoints:
            raise TrainingPrerequisiteError('full RF-DETR optimizer checkpoint missing')
        command += ['--resume', str(checkpoints[-1])]
    # Inherit process group so the runner kills the entire tree. Test split remains disabled.
    subprocess.run(command, check=True)
    # Preserve native full-state resume checkpoints and inference-best weights.
    checkpoints = sorted((run_dir / 'rfdetr').glob('checkpoint_*.ckpt'), key=os.path.getmtime)
    if checkpoints:
        shutil.copyfile(checkpoints[-1], run_dir / 'checkpoints/last.pt')
    best = run_dir / 'rfdetr/checkpoint_best_total.pth'
    if best.is_file():
        shutil.copyfile(best, run_dir / 'checkpoints/best.pt')
    # Native validation AP is not classification precision/F1; keep the original report.
