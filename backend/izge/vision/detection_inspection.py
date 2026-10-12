"""Local four-frame real-model experiment; never a behaviour/identity analysis."""
import os
import platform
from importlib.metadata import distributions
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..video import FileVideoSource
from .config import load
from .detector import DetectorUnavailable, RFDETRSegDetector, validate_mode
from .inspection import code_provenance, prepare_output, reference_frames, write_json
from .preflight import _font, _label, checksum


def environment_provenance() -> dict:
    root = Path(__file__).resolve().parents[3]
    lock = root / 'experiments/rfdetr_seg/uv.lock'
    return {'python': platform.python_version(), 'platform': platform.system(),
            'machine': platform.machine(),
            'packages': {d.metadata['Name']: d.version for d in distributions()},
            'experiment_uv_lock_sha256': checksum(lock) if lock.exists() else None,
            'main_uv_lock_sha256': checksum(root / 'uv.lock')}


def render_detections(image, detections, label: str):
    image = image.convert('RGB').copy()
    overlay = np.asarray(image).copy()
    for i, detection in enumerate(detections):
        color = np.array([(i*71+80) % 200+55, (i*43+140) % 200+55, (i*89+20) % 200+55])
        if detection.mask is not None:
            overlay[detection.mask] = (.6*overlay[detection.mask] + .4*color).astype(np.uint8)
    image = Image.fromarray(overlay)
    draw = ImageDraw.Draw(image)
    for i, detection in enumerate(detections, 1):
        draw.rectangle(detection.box, outline='lime', width=4)
        draw.text((detection.box[0]+4, max(60, detection.box[1])),
                  f'{i}: {detection.class_name} id={detection.class_id} {detection.score:.3f}',
                  fill='lime', font=_font(), stroke_width=2, stroke_fill='black')
    return _label(image, label + f' | {len(detections)} model tespiti')


def inspect_detection(source: Path, config_path: Path, weights: Path, output_dir: Path,
                      device: str = 'auto', mode: str = 'diagnostic-full-frame', threshold: float = .5,
                      detector_factory=RFDETRSegDetector) -> dict:
    prepare_output(output_dir)
    manifest = {'schema_version': 'behavior-detection-experiment-1', 'mode': mode, 'status': 'failed',
                'experiment_only': True, 'roi_verified': False, 'roi_analysis_allowed': False,
                'clock': {'verified': False, 'recorded_at': None},
                'behavior_results_produced': False, 'identity_results_produced': False,
                'model_loaded': False, 'inference_completed': False, 'errors': [], 'frames': [],
                'requested_device': device, 'code': code_provenance(), 'environment': environment_provenance()}
    before = None
    detector = None
    try:
        if not source.is_file():
            raise DetectorUnavailable('source_unavailable')
        before = checksum(source)
        manifest['source'] = {'name': source.name, 'bytes': source.stat().st_size, 'sha256_before': before}
        config = load(config_path)
        manifest['config'] = {'path': str(config_path), 'sha256': config.fingerprint(),
                              'file_sha256': checksum(config_path), 'data': config.model_dump(mode='json')}
        reader = FileVideoSource(source)
        metadata = reader.metadata()
        manifest['metadata'] = metadata
        manifest.update(validate_mode(config, (metadata['width'], metadata['height']), mode))
        if mode == 'verified-roi' and config.geometry_verification.reference_sha256 != before:
            raise DetectorUnavailable('roi_reference_checksum_mismatch')
        frames, targets, timing = reference_frames(reader, config, metadata)
        manifest['timing'] = timing
        detector = detector_factory(weights, device, config, mode, threshold)
        manifest.update({'model_loaded': True, 'model': detector.provenance,
                         'device': detector.device, 'device_capabilities': detector.capabilities,
                         'fallback_reason': detector.fallback_reason,
                         'class_map': detector.class_map, 'selected_cow_class_id': detector.cow_id,
                         'model_load_seconds': detector.load_seconds,
                         'load_timing_policy': 'imports+constructor+explicit device transfer+eval; synchronized',
                         'preprocessing': {'input': 'raw PyAV frame RGB', 'library_resize': [384, 384],
                                           'letterbox': False, 'backend_output': 'PostProcess original input pixel coordinates',
                                           'adapter_inverse_resize': False,
                                           'crop_policy': 'full image' if mode == 'diagnostic-full-frame' else
                                                          'verified animal ROI AABB; >=50% mask support or box centre'},
                         'thresholds': {'model_score': threshold, 'score_comparison': 'strictly_greater',
                                        'mask_logit': 0., 'mask_probability_equivalent': .5, 'cross_roi_box_iou': .8,
                                        'cross_roi_mask_iou': .85},
                         'deduplication': 'cross-ROI same-class box AND mask IoU; retain without masks',
                         'warnings': ['Deney çıktısıdır; ROI kabulü, altın etiket/mAP veya davranış sonucu değildir.',
                                      'Kare içi sıra numarası hayvan kimliği değildir.']})
        import json
        with (output_dir / 'detections.jsonl').open('x', encoding='utf-8') as target:
            for i, frame in enumerate(frames, 1):
                results = detector.detect(frame)
                row = {'status': 'success', 'pts': frame.pts, 'time_base': str(frame.time_base),
                       'offset_s': frame.offset, 'decoded_index': frame.decoded_index,
                       'target_offset_s': float(targets[i-1]), 'origin': 'model',
                       'image_size': [frame.width, frame.height], 'detections': []}
                for j, result in enumerate(results, 1):
                    mask = None
                    if result.mask is not None:
                        name = f'mask_{i:02d}_{j:02d}.png'
                        with (output_dir / name).open('xb') as mask_file:
                            Image.fromarray(result.mask.astype(np.uint8)*255).save(mask_file, format='PNG')
                        mask = {'shape': list(result.mask.shape), 'file': name, 'encoding': 'png_uint8_binary_0_255',
                                'dtype': 'bool', 'sha256': checksum(output_dir / name),
                                'foreground_pixels': int(np.count_nonzero(result.mask))}
                    row['detections'].append({'detection_index': j, 'box_xyxy': list(result.box),
                                              'class_id': result.class_id, 'class_name': result.class_name,
                                              'score': result.score, 'mask': mask, 'roi_ids': list(result.roi_ids),
                                              'boundary_clipped': result.boundary_clipped,
                                              'animal_id': None, 'track_id': None, 'stall_id': None})
                target.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                target.flush()
                label = (f'{os.getenv("IZGE_APP_NAME", "İzge")} | TAM KARE DENEYİ — ROI ANALİZİ KAPALI'
                         if mode == 'diagnostic-full-frame' else 'DOĞRULANMIŞ ROI — TESPİT DENEYİ')
                image = render_detections(frame.image.to_image(), results,
                                          label + f' | PTS {frame.pts} | {frame.offset:.3f} sn')
                name = f'detection_{i:02d}.png'
                with (output_dir / name).open('xb') as preview:
                    image.save(preview, format='PNG')
                manifest['frames'].append({'pts': frame.pts, 'offset_s': frame.offset,
                                           'time_base': str(frame.time_base), 'count': len(results),
                                           'preview': name, 'inference_seconds': detector.inference_seconds[-1]})
        manifest['inference_completed'] = True
        manifest['status'] = 'success'
    except DetectorUnavailable as exc:
        manifest['errors'].append({'code': exc.code})
    except Exception:
        manifest['errors'].append({'code': 'source_config_or_output_invalid'})
    finally:
        if before is not None:
            after = checksum(source)
            manifest['source'].update({'sha256_after': after, 'unchanged': before == after})
            if before != after:
                manifest['status'] = 'failed'
                manifest['errors'].append({'code': 'source_checksum_changed'})
        if detector is not None:
            manifest['inference_seconds'] = detector.inference_seconds
        if manifest['status'] != 'success':
            manifest['roi_analysis_allowed'] = False
        write_json(output_dir / 'manifest.json', manifest)
    report = ['# Tespit deneyi ön incelemesi', '', f'Çalışma durumu: {manifest["status"]}; mod: {mode}.',
              'Kare içi tespit sayısı hayvan envanteri değildir; takip ve davranış üretilmedi.', '',
              '| PTS | Offset sn | Model tespiti | Çıkarım sn |', '| --- | --- | --- | --- |']
    report += [f'| {f["pts"]} | {f["offset_s"]} | {f["count"]} | {f["inference_seconds"]:.6f} |'
               for f in manifest['frames']]
    report += ['', 'Her önizlemede uzak/örtülü/yatan hayvanları, kesik kenar kutularını, birden çok gövdeyi '
               'birleştiren maskeleri ve yanlış sınıfları ham görüntüyle karşılaştırın.',
               'Bu dört kare etiketli altın set değildir; mAP, gerçek zaman kapasitesi veya davranış başarısı ölçülmedi.']
    (output_dir / 'detection_review.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    return manifest
