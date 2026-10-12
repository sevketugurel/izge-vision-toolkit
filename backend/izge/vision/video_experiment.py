"""Bounded sparse/streaming real inference. No tracking, labels or durations."""
import json
import math
import os
import platform
import resource
import subprocess
from fractions import Fraction
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from ..domain import SourceGap
from ..video import FileVideoSource
from .config import load
from .data_preparation import frame_at
from .detection_inspection import environment_provenance, render_detections
from .detector import DetectorUnavailable, RFDETRSegDetector, validate_mode
from .inspection import code_provenance, prepare_output, write_json
from .preflight import checksum
from .timing import TimingDiagnostics


def memory_snapshot() -> dict:
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    maximum *= 1 if platform.system() == 'Darwin' else 1024
    try:
        current = int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(os.getpid())], text=True).strip())*1024
    except (OSError, ValueError, subprocess.SubprocessError):
        current = None
    return {'rss_bytes': current, 'process_peak_rss_bytes': maximum}


def run_video_experiment(source: Path, config_path: Path, weights: Path, output: Path,
                         device: str = 'mps', targets: list[float] | None = None,
                         start: float = 60., duration: float = 180., period: float = 20.,
                         maximum: int = 9, detector_factory=RFDETRSegDetector) -> dict:
    if not 1 <= maximum <= 32 or not math.isfinite(period) or period <= 0:
        raise ValueError('Explicit inference budget 1..32 and positive sampling period required')
    sparse = targets is not None
    if sparse:
        if (not targets or any(not math.isfinite(x) for x in targets) or len(targets) > min(maximum, 12)
                or targets != sorted(set(targets)) or targets[0] < 0):
            raise ValueError('Sparse targets must be ordered/unique and inside bounded budget')
    elif not math.isfinite(start) or start < 0 or not math.isfinite(duration) or not 120 <= duration <= 300:
        raise ValueError('Streaming smoke window must be 2..5 minutes, never the whole long video')
    prepare_output(output)
    started = perf_counter()
    manifest = {'schema_version': 'behavior-bounded-experiment-1', 'status': 'failed',
                'mode': 'diagnostic-full-frame', 'experiment_only': True,
                'roi_verified': False, 'roi_analysis_allowed': False,
                'human_labels_produced': False, 'behavior_results_produced': False,
                'identity_results_produced': False, 'clock_verified': False,
                'posture_model_run': False,
                'scope': 'sparse_pts_frames' if sparse else 'limited_streaming_window',
                'full_file_processed': False, 'model_loaded': False, 'errors': [], 'frames': [],
                'code': code_provenance(), 'environment': environment_provenance(),
                'budget': {'maximum_inferences': maximum, 'targets_s': targets,
                           'start_s': None if sparse else start, 'duration_s': None if sparse else duration,
                           'period_s': None if sparse else period, 'queue_capacity_frames': 1},
                'decode_seconds': 0., 'decoded_frames_in_window': 0, 'source_gaps': [],
                'last_decoded_pts': None, 'last_inference_pts': None}
    detector, before, events = None, None, None
    save_indices = (set(range(len(targets))) if sparse else {0, maximum//2, maximum-1})
    manifest['budget'].update(maximum_decode_events=20000, maximum_processing_seconds=300,
                              buffer_policy='sequential iterator; no application queue; codec internal buffers excluded')
    manifest['source_gap_counts'] = {}
    try:
        before = checksum(source)
        manifest['source'] = {'name': source.name, 'sha256_before': before}
        reader = FileVideoSource(source)
        metadata = reader.metadata()
        manifest['metadata'] = metadata
        config = load(config_path)
        manifest.update(validate_mode(config, (metadata['width'], metadata['height']), 'diagnostic-full-frame'))
        manifest['config'] = {'path': str(config_path), 'file_sha256': checksum(config_path),
                              'geometry_verification': config.geometry_verification.model_dump(mode='json')
                                                       if config.geometry_verification else None,
                              'placeholder': config.placeholder}
        detector = detector_factory(weights, device, config, 'diagnostic-full-frame', .5)
        manifest.update(model_loaded=True, model=detector.provenance, device=detector.device,
                        capabilities=detector.capabilities, fallback_reason=detector.fallback_reason,
                        model_load_seconds=detector.load_seconds,
                        preprocessing={'input': 'raw PyAV RGB', 'resize': [384, 384], 'letterbox': False,
                                       'output': 'original input pixels; no second inverse'},
                        thresholds={'score_strictly_greater': .5, 'mask_logit': 0.}, memory_after_load=memory_snapshot())
        root_base = metadata['time_base']
        origin = metadata['normalization_origin_pts']*Fraction(root_base['numerator'], root_base['denominator'])
        diag = TimingDiagnostics(1/period, 1/period, config.sampling.source_gap_threshold_s,
                                 origin+Fraction(str(start))) if not sparse else None
        schedule = targets if sparse else [start+i*period for i in range(maximum) if i*period < duration]
        next_index = 0
        events = None if sparse else reader.read_events(start=start)
        last_offset, event_count = None, 0
        with (output / 'detections.jsonl').open('x', encoding='utf-8') as sink:
            while True:
                if event_count >= 20000 or perf_counter()-started >= 300:
                    manifest['stop_reason'] = 'decode_event_or_wall_time_budget'
                    break
                decode_start = perf_counter()
                if sparse:
                    if next_index >= len(schedule):
                        break
                    frame = frame_at(reader, schedule[next_index])
                else:
                    try:
                        frame = next(events)
                    except StopIteration:
                        manifest['source_eof_before_window_end'] = True
                        break
                manifest['decode_seconds'] += perf_counter()-decode_start
                event_count += 1
                if isinstance(frame, SourceGap):
                    counts = manifest['source_gap_counts']
                    counts[frame.reason] = counts.get(frame.reason, 0)+1
                    if len(manifest['source_gaps']) < 100:
                        manifest['source_gaps'].append({'reason': frame.reason, 'before_pts': frame.before_pts,
                                                        'start_offset': frame.start_offset})
                    diag.consume(frame)
                    continue
                if not sparse and frame.offset >= start+duration:
                    manifest['first_after_window_pts'] = frame.pts
                    manifest['window_end_boundary_reached'] = True
                    break
                if last_offset is not None and frame.offset <= last_offset:
                    raise DetectorUnavailable('nonmonotonic_source_pts')
                last_offset = frame.offset
                manifest['decoded_frames_in_window'] += 1
                manifest['last_decoded_pts'] = frame.pts
                manifest['last_decoded_offset_s'] = frame.offset
                if diag is not None:
                    diag.consume(frame)
                if next_index >= len(schedule) or frame.offset < schedule[next_index]:
                    continue
                if (frame.width, frame.height) != (metadata['width'], metadata['height']):
                    raise DetectorUnavailable('resolution_changed')
                if sparse and frame.offset-schedule[next_index] > 1:
                    raise DetectorUnavailable('sparse_target_not_reached')
                results = detector.detect(frame)
                saved = next_index in save_indices
                row = {'status': 'success', 'origin': 'model_proposal', 'human_reviewed': False,
                       'source_sha256': before, 'pts': frame.pts, 'time_base': str(frame.time_base),
                       'offset_s': frame.offset, 'target_offset_s': schedule[next_index],
                       'decoded_index_after_seek': frame.decoded_index, 'image_size': [frame.width, frame.height],
                       'detections': []}
                for j, result in enumerate(results, 1):
                    mask = None
                    if result.mask is not None:
                        name = f'mask_{next_index+1:02d}_{j:02d}.png' if saved else None
                        if name:
                            Image.fromarray(result.mask.astype(np.uint8)*255).save(output / name)
                        mask = {'shape': list(result.mask.shape), 'file': name, 'saved': saved,
                                'encoding': 'png_uint8_binary_0_255' if saved else None,
                                'foreground_pixels': int(np.count_nonzero(result.mask)),
                                'sha256': checksum(output/name) if name else None}
                    row['detections'].append({'class_id': result.class_id, 'class_name': result.class_name,
                                              'score': result.score, 'box_xyxy': list(result.box),
                                              'mask': mask, 'animal_id': None, 'track_id': None, 'stall_id': None})
                sink.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
                sink.flush()
                preview = f'detection_{next_index+1:02d}.png' if saved else None
                if preview:
                    render_detections(frame.image.to_image(), results,
                                      f'{os.getenv("IZGE_APP_NAME", "İzge")} | MODEL ÖNERİSİ — ROI KAPALI | PTS {frame.pts} | {frame.offset:.3f}s').save(output/preview)
                manifest['frames'].append({'pts': frame.pts, 'offset_s': frame.offset, 'count': len(results),
                                           'inference_seconds': detector.inference_seconds[-1], 'preview': preview,
                                           'masks_saved': saved, 'memory': memory_snapshot()})
                manifest['last_inference_pts'] = frame.pts
                next_index += 1
                del results, row
        manifest['decode_events_observed'] = event_count
        manifest['status'] = 'success' if ((sparse and next_index == len(schedule)) or
                                          manifest.get('window_end_boundary_reached')) else 'partial'
        if diag is not None:
            manifest['window_timing'] = diag.summary(duration)
            manifest['window_timing_origin_offset_s'] = start
            if manifest['source_gaps'] or manifest['window_timing']['status'] not in ('complete', 'warning'):
                manifest['status'] = 'partial'
    except DetectorUnavailable as exc:
        manifest['errors'].append({'code': exc.code})
    except Exception:
        manifest['errors'].append({'code': 'source_config_or_output_unavailable'})
    finally:
        if events is not None:
            events.close()
        if before is not None:
            after = checksum(source)
            manifest['source'].update(sha256_after=after, unchanged=before == after)
            if before != after:
                manifest['status'] = 'failed'
                manifest['errors'].append({'code': 'source_checksum_changed'})
        if detector is not None:
            manifest['inference_seconds'] = detector.inference_seconds
        manifest['total_processing_seconds'] = perf_counter()-started
        manifest['memory_final'] = memory_snapshot()
        manifest['disk_bytes_before_manifest'] = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        write_json(output / 'manifest.json', manifest)
    return manifest
