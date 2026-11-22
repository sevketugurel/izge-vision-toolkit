"""Measure stall pipeline throughput (video seconds processed per wall-clock second).

    uv run --extra ml python scripts/bench_feeding.py [--video file.mp4] [--device mps|cpu] [--random-weights]

With HF_TOKEN the real DINOv3 ViT-S/16 weights are used. Without it, the measurement is skipped
unless --random-weights is given: that builds the same ViT-S/16 architecture with random weights,
which measures compute cost only and produces NO meaningful embeddings.
Frames: 1 Hz PTS sampling, every stall of synthetic_feeding cropped (synthetic geometry).
"""
import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from izge.feeding import camera_config  # noqa: E402
from izge.feeding.backbone import Dinov3Backbone, DINOV3_ID  # noqa: E402
from izge.feeding.crops import geometries, crop  # noqa: E402
from izge.feeding.pipeline import _frames  # noqa: E402


def synthetic(path: Path, seconds: int):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    f'testsrc2=size=1920x1080:rate=25:duration={seconds}', '-c:v', 'libx264', '-g', '50',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', type=Path)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--device')
    parser.add_argument('--random-weights', action='store_true')
    args = parser.parse_args()
    if os.getenv('HF_TOKEN'):
        backbone, kind = Dinov3Backbone(DINOV3_ID, device=args.device), 'gerçek DINOv3 ağırlıkları'
    elif args.random_weights:
        import torch
        from transformers import DINOv3ViTConfig, DINOv3ViTModel
        config = DINOv3ViTConfig(hidden_size=384, intermediate_size=1536, num_hidden_layers=12, num_attention_heads=6,
                                 num_register_tokens=4, patch_size=16, image_size=224)
        torch.manual_seed(0)
        backbone = Dinov3Backbone('random-weights/vits16-architecture', model=DINOv3ViTModel(config), device=args.device)
        kind = 'RASTGELE ağırlık, aynı ViT-S/16 mimarisi (yalnız hız; embedding anlamsız)'
    else:
        print('HF_TOKEN yok: gerçek DINOv3 ölçümü atlandı. Yalnız hız tahmini için --random-weights kullanın.')
        return 2
    cfg = camera_config.load('synthetic_feeding')
    with tempfile.TemporaryDirectory() as tmp:
        video = args.video or Path(tmp) / 'bench.mp4'
        if not args.video:
            synthetic(video, args.seconds)
        import av
        with av.open(str(video)) as c:
            s = c.streams.video[0]
            width, height = s.width, s.height
            duration = float(c.duration / 1e6)
        geoms = geometries(cfg, width, height)
        backbone.embed([crop(__import__('numpy').zeros((height, width, 3), 'uint8'), geoms[0])])   # warm-up
        t0, crops = time.perf_counter(), 0
        batch = []
        for second, frame in _frames(video, 0, duration, threading.Event()):
            image = frame.image.to_ndarray(format='rgb24')
            batch += [crop(image, g) for g in geoms]
            if len(batch) >= 64:
                backbone.embed(batch)
                crops += len(batch)
                batch = []
        if batch:
            backbone.embed(batch)
            crops += len(batch)
        elapsed = time.perf_counter() - t0
    print(f'backbone: {kind}; cihaz: {backbone.device}; video: {width}x{height}, {duration:.0f} sn, '
          f'{len(geoms)} bölme')
    print(f'{crops} kesit, {elapsed:.1f} sn → {duration / elapsed:.2f} video-sn/sn '
          f'({crops / elapsed:.1f} kesit/sn; kod çözme + kesit + embedding dahil)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
