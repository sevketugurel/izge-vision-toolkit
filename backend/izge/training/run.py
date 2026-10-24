"""Training entrypoint. Smoke scores prove infrastructure, never livestock performance."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
import threading

from .config import TrainingPrerequisiteError, load_config


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(path)


def append_json(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value, allow_nan=False) + '\n')


@contextmanager
def monitor_resources(run_dir, device):
    import psutil
    import torch
    process = psutil.Process()
    process.cpu_percent()
    stopped = threading.Event()
    def sample():
        values = dict(timestamp=time.time(), cpu_percent=process.cpu_percent(),
                      rss_bytes=process.memory_info().rss,
                      ram_available_bytes=psutil.virtual_memory().available)
        if device == 'mps':
            values.update(mps_allocated_bytes=torch.mps.current_allocated_memory(),
                          mps_driver_bytes=torch.mps.driver_allocated_memory())
        append_json(run_dir / 'resources.jsonl', values)
    def collect():
        while not stopped.wait(0.5):
            sample()
    sample()
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
        sample()


def pick_device(wanted='auto'):
    import torch
    available = {'cpu': True, 'mps': torch.backends.mps.is_available(), 'cuda': torch.cuda.is_available()}
    if wanted == 'auto':
        return next(device for device in ('mps', 'cuda', 'cpu') if available[device])
    if not available[wanted]:
        raise TrainingPrerequisiteError(f'requested device unavailable: {wanted}')
    return wanted


def smoke(config, run_dir, device, resume, emit):
    import torch
    from sklearn.datasets import make_classification
    from sklearn.model_selection import train_test_split
    from .adapters import classification_metrics
    torch.set_num_threads(1)
    x, y = make_classification(n_samples=384, n_features=16, n_informative=12, n_classes=3,
                               n_clusters_per_class=1, random_state=config.seed)
    tx, vx, ty, vy = train_test_split(x, y, test_size=0.25, stratify=y, random_state=config.seed)
    tx, vx = [torch.tensor(v, dtype=torch.float32, device=device) for v in (tx, vx)]
    ty_tensor = torch.tensor(ty, dtype=torch.long, device=device)
    model = torch.nn.Linear(16, 3).to(device)
    optimizer = torch.optim.SGD(model.parameters(), lr=config.params.learning_rate, momentum=0.9)
    last = run_dir / 'checkpoints/last.pt'
    first, best = 1, float('inf')
    if resume:
        state = torch.load(last, map_location=device, weights_only=True)
        model.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        first, best = state['epoch'] + 1, state['best']
        torch.set_rng_state(state['rng'].cpu())
    for epoch in range(first, config.params.epochs + 1):
        start = time.monotonic()
        model.train()
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(tx), ty_tensor)
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            probabilities = model(vx).softmax(dim=1).cpu().numpy()
        values = classification_metrics(vy, probabilities)
        improved = values['val_loss'] < best
        best = min(best, values['val_loss'])
        state = dict(model=model.state_dict(), optimizer=optimizer.state_dict(), epoch=epoch,
                     best=best, rng=torch.get_rng_state())
        temporary = last.with_suffix('.tmp')
        torch.save(state, temporary)
        temporary.replace(last)
        if improved:
            torch.save(state, last.parent / 'best.pt')
        emit(dict(epoch=epoch, train_loss=float(loss.detach().cpu()), **values,
                  duration_s=time.monotonic() - start))


def execute(config_path: Path, run_dir: Path, resume=False):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'checkpoints').mkdir(exist_ok=True)
    previous = run_dir / 'status.json'
    if previous.exists() and not resume:
        print('Run directory already used; choose a new directory or --resume', flush=True)
        return 1
    status = dict(status='running', infrastructure_smoke=False)
    try:
        import mlflow
        import torch
        config = load_config(config_path)
        fingerprint = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
        previous = run_dir / 'status.json'
        if resume:
            old = json.loads(previous.read_text())
            if old.get('config_sha256') != fingerprint:
                print('Resume config differs from original run', flush=True)
                return 1
        device = pick_device(config.device)
        random.seed(config.seed)
        torch.manual_seed(config.seed)
        status.update(device=device, config_sha256=fingerprint, infrastructure_smoke=config.kind == 'smoke')
        runs_root = Path(os.getenv('IZGE_RUNS_ROOT', Path.home() / '.izge-runner/runs')).resolve()
        tracking = os.getenv('MLFLOW_TRACKING_URI', (runs_root / 'mlruns').as_uri())
        if tracking.startswith('file:'):
            os.environ.setdefault('MLFLOW_ALLOW_FILE_STORE', 'true')
        mlflow.set_tracking_uri(tracking)
        mlflow.set_experiment('izge-training-infrastructure')
        old_id = old.get('mlflow_run_id') if resume else None
        with mlflow.start_run(run_id=old_id) as tracked, monitor_resources(run_dir, device):
            status['mlflow_run_id'] = tracked.info.run_id
            write_json(previous, status)
            mlflow.set_tag('purpose', 'infrastructure_smoke_not_izge_performance' if config.kind == 'smoke' else config.kind)
            mlflow.log_params({'kind': config.kind, 'dataset_version': config.dataset.version,
                               'seed': config.seed, 'device': device, **config.params.model_dump()})
            def emit(values):
                append_json(run_dir / 'metrics.jsonl', values)
                mlflow.log_metrics({k: v for k, v in values.items() if k != 'epoch'}, step=values['epoch'])
                with (run_dir / 'train.log').open('a') as log:
                    log.write(json.dumps(values) + '\n')
                print(json.dumps(values), flush=True)
            if config.kind == 'smoke':
                smoke(config, run_dir, device, resume, emit)
            else:
                from .adapters import feeding, rfdetr
                (feeding if config.kind == 'feeding_linear' else rfdetr)(config, run_dir, resume, emit)
            status['status'] = 'completed'
            write_json(previous, status)
            for name in ('status.json', 'metrics.jsonl', 'resources.jsonl', 'train.log', 'checkpoints', 'rfdetr'):
                path = run_dir / name
                if path.is_dir():
                    mlflow.log_artifacts(str(path), artifact_path=name)
                elif path.exists():
                    mlflow.log_artifact(str(path))
        return 0
    except Exception as exc:
        # Exception text can contain provider credentials. Persist only a stable type and safe local validation errors.
        status.update(status='failed', error_type=type(exc).__name__)
        if isinstance(exc, TrainingPrerequisiteError):
            status['error'] = str(exc)
        elif isinstance(exc, (ValueError, FileNotFoundError)):
            status['error'] = 'Configuration, dataset, device or resume prerequisite failed; inspect approved recipe.'
        write_json(run_dir / 'status.json', status)
        with (run_dir / 'train.log').open('a') as log:
            log.write(f'Training failed: {type(exc).__name__}\n')
            if isinstance(exc, TrainingPrerequisiteError):
                log.write(str(exc) + '\n')
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--wait-for-runner', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.wait_for_runner and sys.stdin.readline() != 'start\n':
        raise SystemExit(1)
    raise SystemExit(execute(args.config, args.run_dir, args.resume))


if __name__ == '__main__':
    main()
