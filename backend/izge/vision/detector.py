"""Inference-only animal detector, with explicit coordinate and availability contracts."""
import json
from dataclasses import dataclass, replace
from importlib.metadata import version
from math import ceil, floor, isfinite
from pathlib import Path
from time import perf_counter
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw

from ..domain import Frame
from .config import BehaviorConfig
from .geometry import CoordinateTransform
from .preflight import checksum

MODES = ('verified-roi', 'diagnostic-full-frame')
MODEL_LOCK = Path(__file__).resolve().parents[3] / 'experiments/rfdetr_seg/model.lock.json'


class DetectorUnavailable(RuntimeError):
    """Safe error code, deliberately excluding backend exception text/credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    class_id: int
    class_name: str
    score: float
    mask: np.ndarray | None = None
    roi_ids: tuple[str, ...] = ()
    boundary_clipped: bool = False


@dataclass(frozen=True)
class BackendPredictions:
    xyxy: np.ndarray
    class_id: np.ndarray
    confidence: np.ndarray
    mask: np.ndarray | None
    data: dict


class Detector(Protocol):
    def detect(self, frame: Frame) -> list[Detection]: ...


def class_map_from_model(model, coco: dict[int, str]) -> dict[int, str]:
    names = list(model.class_names)
    slots = model.model_config.num_classes
    # COCO-pretrained RF-DETR uses sparse category IDs (cow=21), not index 19 of 80 names.
    if names == list(coco.values()) and slots > len(names):
        return dict(coco)
    return dict(enumerate(names))


def cow_class_id(class_map: dict[int, str]) -> int:
    ids = [key for key, name in class_map.items() if name == 'cow']
    if len(ids) != 1:
        raise DetectorUnavailable('cow_class_unavailable')
    return ids[0]


def select_device(requested: str, capabilities: dict[str, bool]) -> tuple[str, str | None]:
    if requested == 'auto':
        if capabilities['mps']:
            return 'mps', None
        return 'cpu', 'mps_unavailable'  # This experiment targets local CPU/MPS, not implicit CUDA.
    if requested not in ('cpu', 'mps', 'cuda'):
        raise DetectorUnavailable('device_invalid')
    if not capabilities.get(requested, False):
        raise DetectorUnavailable(f'{requested}_unavailable')
    return requested, None


def validate_mode(config: BehaviorConfig, size: tuple[int, int], mode: str) -> dict:
    if mode not in MODES:
        raise ValueError('Bilinmeyen tespit modu')
    config.validate_resolution(*size)
    if mode == 'verified-roi':
        config.require_analysis_ready(*size)
    # Diagnostic mode makes no ROI acceptance claim, even if given a verified config.
    return {'roi_verified': mode == 'verified-roi', 'roi_analysis_allowed': mode == 'verified-roi',
            'experiment_only': True, 'config_geometry_verified': config.roi_verified_for(*size)}


def restore_mask(mask: np.ndarray, transform: CoordinateTransform, coordinate_space: str) -> np.ndarray:
    """Reverse external letterbox only when the backend explicitly returns model-space pixels."""
    x0, y0, x1, y1 = transform.crop_box
    expected = transform.output_size if coordinate_space == 'model' else (x1-x0, y1-y0)
    if mask.dtype != np.bool_ or mask.shape != (expected[1], expected[0]):
        raise ValueError('Maske boolean ve backend koordinat alanıyla aynı boyutta olmalı')
    if coordinate_space == 'model':
        px, py = transform.padding
        rw, rh = transform.resized_size
        image = Image.fromarray(mask[py:py+rh, px:px+rw]).resize((x1-x0, y1-y0), Image.Resampling.NEAREST)
        mask = np.asarray(image, dtype=bool)
    result = np.zeros((transform.source_size[1], transform.source_size[0]), dtype=bool)
    result[y0:y1, x0:x1] = mask
    return result


def convert_predictions(predictions, class_map: dict[int, str], transform: CoordinateTransform,
                        coordinate_space: str = 'input', roi_id: str | None = None) -> list[Detection]:
    """Validate every result before cow filtering; do not hide malformed results as an empty success."""
    if coordinate_space not in ('input', 'model'):
        raise ValueError('Backend koordinat alanı açık olmalı')
    boxes = np.asarray(predictions.xyxy)
    ids, scores = np.asarray(predictions.class_id), np.asarray(predictions.confidence)
    n = len(boxes)
    if boxes.shape != (n, 4) or ids.shape != (n,) or scores.shape != (n,):
        raise ValueError('Tespit dizilerinin boyutları tutarsız')
    masks = predictions.mask
    if masks is not None and len(masks) != n:
        raise ValueError('Maske sayısı tutarsız')
    names = predictions.data.get('class_name')
    if names is not None and len(names) != n:
        raise ValueError('Sınıf adı sayısı tutarsız')
    cow_id = cow_class_id(class_map)
    width, height = transform.source_size
    results = []
    for i in range(n):
        if not np.issubdtype(ids.dtype, np.integer) or int(ids[i]) not in class_map:
            raise ValueError('Tespit sınıfı model haritasında bulunamadı')
        class_id = int(ids[i])
        name = class_map[class_id]
        if names is not None and str(names[i]) != name:
            raise ValueError('Backend sınıf adı/ID eşleşmesi tutarsız')
        score = float(scores[i])
        box = tuple(float(x) for x in boxes[i])
        if not isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Model skoru [0,1] aralığında sonlu olmalı')
        if not all(isfinite(x) for x in box) or not (box[0] < box[2] and box[1] < box[3]):
            raise ValueError('Model kutusu sonlu ve pozitif olmalı')
        if coordinate_space == 'model':
            original = transform.transform_box(box, inverse=True)
        else:
            # predict() already undoes its own resize. Only translate a crop once.
            x0, y0, _, _ = transform.crop_box
            original = (box[0]+x0, box[1]+y0, box[2]+x0, box[3]+y0)
        clipped = (max(0., min(width, original[0])), max(0., min(height, original[1])),
                   max(0., min(width, original[2])), max(0., min(height, original[3])))
        mask = restore_mask(np.asarray(masks[i]), transform, coordinate_space) if masks is not None else None
        if clipped[0] >= clipped[2] or clipped[1] >= clipped[3] or class_id != cow_id:
            continue
        results.append(Detection(clipped, class_id, name, score, mask,
                                 (roi_id,) if roi_id else (), clipped != original))
    return results


def box_iou(a, b) -> float:
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union else 0.


def deduplicate_rois(detections: list[Detection]) -> list[Detection]:
    """Only cross-ROI, same-class duplicates with mask IoU >= .85 AND box IoU >= .8.

    Keep the highest score and all ROI origins; no mask => retain ambiguity, never box-only NMS.
    Neighbouring animals with overlapping boxes but distinct visible masks are retained.
    """
    kept = []
    for candidate in sorted(detections, key=lambda d: d.score, reverse=True):
        duplicate = None
        for i, previous in enumerate(kept):
            if (candidate.class_id != previous.class_id or not candidate.roi_ids or not previous.roi_ids or
                    set(candidate.roi_ids) & set(previous.roi_ids) or
                    candidate.mask is None or previous.mask is None or
                    box_iou(candidate.box, previous.box) < .8):
                continue
            union = np.count_nonzero(candidate.mask | previous.mask)
            iou = np.count_nonzero(candidate.mask & previous.mask) / union if union else 0.
            if iou >= .85:
                duplicate = i
                break
        if duplicate is None:
            kept.append(candidate)
        else:
            kept[duplicate] = replace(kept[duplicate],
                                      roi_ids=tuple(sorted(set(kept[duplicate].roi_ids + candidate.roi_ids))))
    return kept


class RFDETRSegDetector:
    """Lazy real backend; normally constructed only by the isolated experiment interpreter."""

    def __init__(self, weights: Path, device: str, config: BehaviorConfig, mode: str,
                 threshold: float = .5, model_lock: Path = MODEL_LOCK):
        if not isfinite(threshold) or not 0 < threshold < 1:
            raise ValueError('Skor eşiği (0,1) aralığında olmalı')
        self.config, self.mode, self.threshold = config, mode, threshold
        self.provenance = json.loads(model_lock.read_text(encoding='utf-8'))
        if not weights.is_file() or checksum(weights) != self.provenance['weights_sha256']:
            raise DetectorUnavailable('weights_missing_or_checksum_mismatch')
        self.weights = weights
        self.provenance.update({'weights_file': weights.name, 'weights_bytes': weights.stat().st_size,
                                'weights_actual_sha256': checksum(weights),
                                'model_lock_sha256': checksum(model_lock)})
        start = perf_counter()
        try:
            if version('rfdetr') != self.provenance['package_version']:
                raise DetectorUnavailable('package_version_mismatch')
            import torch
            from torchvision.transforms import functional as F
            from rfdetr import RFDETRSegSmall
            from rfdetr.assets.coco_classes import COCO_CLASSES
            self.torch = torch
            self.functional = F
            self.capabilities = {'cpu': True, 'mps': torch.backends.mps.is_available(),
                                 'mps_built': torch.backends.mps.is_built(), 'cuda': torch.cuda.is_available()}
            self.device, self.fallback_reason = select_device(device, self.capabilities)
            self.model = RFDETRSegSmall(pretrain_weights=str(weights.resolve()), device=self.device)
            self.class_map = class_map_from_model(self.model, COCO_CLASSES)
            self.cow_id = cow_class_id(self.class_map)
            # Single-threaded experiment: transfer/eval during load, not the first prediction.
            # Real LWDETR and PostProcess; exclude the Supervision/GUI wrapper.
            self.model.model.model.to(self.device).eval()
            if self.device == 'mps':
                torch.mps.synchronize()
            if checksum(weights) != self.provenance['weights_sha256']:
                raise DetectorUnavailable('weights_changed_during_load')
        except DetectorUnavailable:
            raise
        except Exception:
            raise DetectorUnavailable('model_load_unavailable') from None
        self.load_seconds = perf_counter() - start
        self.inference_seconds: list[float] = []

    def _predict(self, image: Image.Image) -> BackendPredictions:
        F = self.functional
        torch = self.torch
        context = self.model.model
        # Match pinned detr.py preprocessing; PostProcess receives original input H,W.
        with torch.inference_mode():
            tensor = F.to_tensor(image).to(self.device)
            tensor = F.resize(tensor, [384, 384], antialias=False)
            tensor = F.normalize(tensor, self.model.means, self.model.stds)
            raw = context.model(tensor.unsqueeze(0))
            result = context.postprocess(raw, target_sizes=torch.tensor([[image.height, image.width]],
                                                                       device=self.device),
                                         score_threshold=self.threshold)[0]
            selected = result['scores'] > self.threshold
            ids = result['labels'][selected].cpu().numpy()
            mask = result['masks'][selected].squeeze(1).cpu().numpy() if 'masks' in result else None
            return BackendPredictions(result['boxes'][selected].float().cpu().numpy(), ids,
                                      result['scores'][selected].float().cpu().numpy(), mask,
                                      {'class_name': [self.class_map.get(int(cid), '') for cid in ids]})

    def detect(self, frame: Frame) -> list[Detection]:
        validate_mode(self.config, (frame.width, frame.height), self.mode)
        image = frame.image.to_image().convert('RGB')
        crops = [(None, (0, 0, frame.width, frame.height))]
        if self.mode == 'verified-roi':
            crops = [(r, (floor(min(x for x, _ in r.points())), floor(min(y for _, y in r.points())),
                          ceil(max(x for x, _ in r.points())), ceil(max(y for _, y in r.points()))))
                     for r in self.config.animal_rois]
        results = []
        start = perf_counter()
        try:
            for region, crop in crops:
                transform = CoordinateTransform.build(image.size, crop, (384, 384), mode='resize')
                raw_crop = image.crop(crop)  # Not transform.apply(): library owns its internal resize.
                prediction = self._predict(raw_crop)
                converted = convert_predictions(prediction, self.class_map, transform, 'input',
                                                region.id if region else None)
                if region:
                    support = Image.new('1', image.size)
                    ImageDraw.Draw(support).polygon(region.points(), fill=1)
                    support = np.asarray(support, dtype=bool)
                    converted = [d for d in converted if self._in_roi(d, support)]
                results.extend(converted)
            if self.device == 'mps':
                self.torch.mps.synchronize()
            elif self.device == 'cuda':
                self.torch.cuda.synchronize()
        except Exception:
            raise DetectorUnavailable('inference_or_output_validation_failed') from None
        self.inference_seconds.append(perf_counter() - start)
        return deduplicate_rois(results)

    @staticmethod
    def _in_roi(detection: Detection, support: np.ndarray) -> bool:
        if detection.mask is not None and np.count_nonzero(detection.mask):
            return np.count_nonzero(detection.mask & support) / np.count_nonzero(detection.mask) >= .5
        x = min(support.shape[1]-1, int((detection.box[0]+detection.box[2])/2))
        y = min(support.shape[0]-1, int((detection.box[1]+detection.box[3])/2))
        return bool(support[y, x])
