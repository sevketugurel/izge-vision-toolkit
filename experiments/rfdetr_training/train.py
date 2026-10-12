"""Guarded Seg Small training entry point, separate from Mac inference."""
import argparse
import json
import platform
import sys
import tempfile
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from izge.vision.annotation_data import validate_training_dataset  # noqa: E402
from izge.vision.artifact_io import checksum, prepare_output, write_json  # noqa: E402

WEIGHTS_SHA256 = '6de3da31b2572cac214a1c76cce4a92a13966d56390ac2b3a3de9a8dc2b2bca3'


def check_environment() -> dict:
    import torch
    from rfdetr import RFDETRSegSmall
    from rfdetr.config import RFDETRSegSmallConfig, SegmentationTrainConfig
    from rfdetr.training import RFDETRDataModule, RFDETRModelModule, build_trainer
    from rfdetr.datasets.coco import CocoDetection
    from PIL import Image
    import numpy as np
    packages = {d.metadata['Name'].lower(): d.version for d in distributions()}
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('Training environment targets Linux x86_64, not the Mac inference interpreter')
    if packages.get('rfdetr') != '1.11.2':
        raise ValueError('Pinned rfdetr 1.11.2 required')
    if any(name in packages for name in ('supervision', 'roboflow', 'opencv-python', 'opencv-python-headless', 'ultralytics', 'rfdetr-plus')):
        raise ValueError('Unexpected excluded service/codec/Plus dependency installed')
    mc = RFDETRSegSmallConfig()
    tc = SegmentationTrainConfig(dataset_dir=None, batch_size=1, grad_accum_steps=4,
                                 augmentation_backend='torchvision', tensorboard=False, wandb=False,
                                 save_dataset_grids=False, multi_scale=False, eval_backend='faster_coco_eval')
    # Exercise the real pinned COCO loader using an explicitly synthetic software fixture, no model/fit.
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder)
        Image.new('RGB', (32, 24)).save(p / 'fixture.png')
        data = {'images': [{'id': 1, 'file_name': 'fixture.png', 'width': 32, 'height': 24}],
                'categories': [{'id': 1, 'name': 'cow', 'supercategory': 'animal'}],
                'annotations': [{'id': 1, 'image_id': 1, 'category_id': 1, 'bbox': [4, 4, 12, 10],
                                 'area': 120, 'iscrowd': 0, 'segmentation': [[4,4,16,4,16,14,4,14]]}]}
        mask = np.zeros((24,32),dtype=bool)
        mask[3:8,20:24] = True
        from izge.vision.annotation_data import validate_object
        Image.fromarray(mask.astype(np.uint8)*255).save(p/'mask.png')
        rle = validate_object({'class_name':'cow','bbox_xyxy':[20,3,24,8],'mask_file':'mask.png'},
                              32,24,True,p)['segmentation']
        data['annotations'].append({'id':2,'image_id':1,'category_id':1,'bbox':[20,3,4,5],
                                     'area':20,'iscrowd':0,'segmentation':rle})
        (p/'_annotations.coco.json').write_text(json.dumps(data))
        dataset = CocoDetection(p, p/'_annotations.coco.json', transforms=None,
                                include_masks=True, remap_category_ids=True)
        _, target = dataset[0]
        if target['labels'].tolist() != [0,0] or tuple(target['masks'].shape) != (2,24,32) or int(target['masks'].sum()) != 140:
            raise ValueError('Pinned COCO category/mask loader contract failed')
    return {'status': 'imports_config_and_synthetic_loader_passed', 'training_started': False,
            'python': platform.python_version(), 'platform': platform.platform(), 'packages': packages,
            'cuda_compiled': torch.version.cuda, 'cuda_available': torch.cuda.is_available(),
            'cuda_training_verified': False, 'gpu_vram_experiment_run': False,
            'model_class': RFDETRSegSmall.__name__, 'resolution': mc.resolution,
            'segmentation_head': mc.segmentation_head, 'train_config': tc.model_dump(mode='json'),
            'training_api_imports': [RFDETRDataModule.__name__, RFDETRModelModule.__name__, build_trainer.__name__],
            'coco_loader_fixture': 'synthetic polygon+exported uncompressed RLE; cow=1 -> contiguous=0; masks=(2,24,32), area=140; no training',
            'licenses': {'opencv_supervision_roboflow': 'excluded', 'cuda': 'NVIDIA runtime terms apply; distribution audit pending'}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check-environment', action='store_true')
    p.add_argument('--report', type=Path)
    p.add_argument('--dataset', type=Path)
    p.add_argument('--weights', type=Path)
    p.add_argument('--output-dir', type=Path)
    p.add_argument('--epochs', type=int, default=20)
    p.add_argument('--batch-size', type=int, default=1)
    p.add_argument('--grad-accum-steps', type=int, default=4)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--resume', type=Path)
    p.add_argument('--allow-training', action='store_true')
    p.add_argument('--evaluate-test', action='store_true')
    args = p.parse_args()
    if args.check_environment:
        result = check_environment()
        if args.report:
            write_json(args.report, result)
        print(json.dumps({key: result[key] for key in ('status', 'training_started', 'cuda_available', 'cuda_compiled')}))
        return 0
    if not args.allow_training:
        raise ValueError('Training disabled. Human labels and an explicit --allow-training are required.')
    if None in (args.dataset, args.weights, args.output_dir):
        p.error('Training needs --dataset, --weights and --output-dir')
    counts = validate_training_dataset(args.dataset, 'segmentation')
    environment = check_environment()
    if not environment['cuda_available']:
        raise ValueError('CUDA GPU unavailable; Mac inference / CPU import checks are not CUDA training acceptance')
    if checksum(args.weights) != WEIGHTS_SHA256:
        raise ValueError('Official Seg Small weight checksum mismatch')
    if not args.resume:
        prepare_output(args.output_dir)
    elif not args.resume.is_file():
        raise ValueError('Resume checkpoint unavailable')
    import torch
    from rfdetr import RFDETRSegSmall
    model = RFDETRSegSmall(pretrain_weights=str(args.weights), device='cuda', cuda_graphs=False)
    torch.cuda.reset_peak_memory_stats()
    kwargs = dict(dataset_dir=str(args.dataset), dataset_file='roboflow', output_dir=str(args.output_dir),
                  epochs=1 if args.smoke else args.epochs, batch_size=args.batch_size,
                  grad_accum_steps=args.grad_accum_steps, resolution=384, device='cuda',
                  augmentation_backend='torchvision', multi_scale=False,
                  num_workers=0 if args.smoke else 2, tensorboard=False, wandb=False,
                  save_dataset_grids=False, checkpoint_interval=1, eval_backend='faster_coco_eval',
                  eval_masks_head_resolution=False, eval_batch_size=1, run_test=False,
                  early_stopping=not args.smoke, early_stopping_patience=5)
    if args.resume:
        kwargs['resume'] = str(args.resume)
    try:
        model.train(**kwargs)
    except torch.OutOfMemoryError:
        raise ValueError('GPU OOM: stop this run; reduce micro-batch, keep accumulation explicit; no VRAM promise') from None
    validation = model.evaluate(split='val', dataset_dir=str(args.dataset), dataset_file='roboflow', device='cuda',
                                output_dir=str(args.output_dir), tensorboard=False, wandb=False,
                                eval_backend='faster_coco_eval', eval_batch_size=1)
    test = None
    if args.evaluate_test:
        test = model.evaluate(split='test', dataset_dir=str(args.dataset), dataset_file='roboflow', device='cuda',
                              output_dir=str(args.output_dir), tensorboard=False, wandb=False,
                              eval_backend='faster_coco_eval', eval_batch_size=1)
    result = {'training_started': True, 'dataset_counts': counts, 'dataset_manifest_sha256': checksum(args.dataset/'dataset_manifest.json'),
              'validation': validation, 'test': test, 'smoke': args.smoke,
              'peak_cuda_memory_bytes': torch.cuda.max_memory_allocated(), 'environment': environment,
              'checkpoint_policy': 'persistent output_dir; checkpoint_{epoch}.ckpt full state for resume; best .pth for inference'}
    destination = args.output_dir / ('run_report_resume.json' if args.resume else 'run_report.json')
    write_json(destination, result)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f'Eğitim kapısı/ortamı: {error}', file=sys.stderr)
        raise SystemExit(2)
