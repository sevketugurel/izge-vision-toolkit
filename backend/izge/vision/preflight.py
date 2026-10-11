"""Local, models-free video inspection with bounded image memory and safe outputs."""
import csv
import hashlib
import json
import os
import platform
import subprocess
from dataclasses import asdict, fields
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path

import av
import yaml
from PIL import ImageDraw, ImageFont

from ..domain import Frame
from ..video import FileVideoSource
from .config import load
from .timing import TimingDiagnostics, TimingRow


class PreflightError(ValueError):
    """A user-safe preflight message; never a raw codec/config exception."""


def checksum(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _font():
    for name in ('DejaVuSans.ttf', '/System/Library/Fonts/Supplemental/Arial.ttf'):
        try:
            return ImageFont.truetype(name, 24)
        except OSError:
            pass
    return ImageFont.load_default(size=24)


def _label(image, text):
    image = image.copy()
    draw = ImageDraw.Draw(image)
    font = _font()
    box = draw.textbbox((12, 10), text, font=font)
    draw.rectangle((0, 0, min(image.width, box[2] + 12), box[3] + 10), fill=(20, 20, 20))
    draw.text((12, 10), text, fill=(255, 230, 100), font=font)
    return image


def _code_revision():
    try:
        root = Path(__file__).resolve().parents[3]
        revision = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        dirty = bool(subprocess.run(['git', '-C', str(root), 'status', '--porcelain'],
                                    capture_output=True, text=True, check=True, timeout=5).stdout)
        names = ['backend/izge/domain.py', 'backend/izge/video.py', 'scripts/inspect_behavior_video.py',
                 *[f'backend/izge/vision/{name}.py' for name in ('config', 'timing', 'geometry', 'preflight')]]
        return {'commit': revision, 'working_tree_dirty': dirty,
                'files_sha256': {name: checksum(root / name) for name in names}}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'working_tree_dirty': None}


def inspect_video(source: Path, config_path: Path, output_dir: Path) -> dict:
    source, config_path, output_dir = source.resolve(), config_path.resolve(), output_dir.resolve()
    if not source.is_file():
        raise PreflightError('Kaynak yerel ve okunabilir bir video dosyası olmalı')
    # Refuse reuse: stale previews/CSV and user data cannot masquerade as a new run.
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise PreflightError('Çıktı klasörü yeni veya boş olmalı')
    try:
        config_bytes_hash = checksum(config_path)
        config = load(config_path)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        raise PreflightError('Behavior yapılandırması okunamadı veya geçersiz') from None
    before_hash = checksum(source)
    reader = FileVideoSource(source)
    try:
        metadata = reader.metadata()
        config.validate_resolution(metadata['width'], metadata['height'])
    except (OSError, ValueError, TypeError, IndexError, av.error.FFmpegError):
        raise PreflightError('Video açılamadı veya çözünürlük ROI referansıyla uyumsuz') from None
    warnings = []
    roi_verified = config.roi_verified_for(metadata['width'], metadata['height'])
    if not roi_verified:
        warnings.append('ROI doğrulanmadı; ROI tabanlı analiz engellendi.')
    if (metadata['width'], metadata['height']) != (config.reference_width, config.reference_height):
        warnings.append('Ölçeklenen ROI yalnız önizlemedir; bu çözünürlükte tekrar doğrulanmalıdır.')
    if metadata['stream_start_pts'] is None:
        warnings.append('Akış başlangıç PTS bilinmiyor; açık politika ile sıfır köken kullanıldı.')
    warnings.append('Kayıt saati doğrulanmadı; dosya adından mutlak zaman veya hayvan kimliği çıkarılmadı.')
    output_dir.mkdir(parents=True, exist_ok=True)
    base = metadata['time_base']
    origin = metadata['normalization_origin_pts'] * Fraction(base['numerator'], base['denominator'])
    diag = TimingDiagnostics(config.sampling.detection_hz, config.sampling.posture_hz,
                             config.sampling.source_gap_threshold_s, origin)
    resolution_changed = False
    with (output_dir / 'frames.csv').open('x', encoding='utf-8', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=[field.name for field in fields(TimingRow)])
        writer.writeheader()
        for event in reader.read_events():
            if isinstance(event, Frame) and (event.width, event.height) != (metadata['width'], metadata['height']):
                resolution_changed = True
            writer.writerow(asdict(diag.consume(event)))
    timing = diag.summary(metadata['video_stream_end_offset_s'])

    samples = []
    if diag.first_time is not None:
        # A second sequential pass avoids retaining every frame or using frame count/FPS for targets.
        targets = [diag.first_time + (diag.last_time - diag.first_time) * Fraction(i, 3) for i in range(4)]
        nearest = [None] * 4
        distances = [None] * 4
        sample_diag = TimingDiagnostics(config.sampling.detection_hz, config.sampling.posture_hz,
                                       config.sampling.source_gap_threshold_s, origin)
        for event in reader.read_events():
            row = sample_diag.consume(event)
            if not isinstance(event, Frame) or row.source_status not in ('valid', 'timestamp_discontinuity'):
                continue
            at = event.pts * event.time_base - origin
            for i, target_time in enumerate(targets):
                distance = abs(at - target_time)
                if distances[i] is None or distance < distances[i]:
                    nearest[i], distances[i] = event, distance
        if sample_diag.summary(metadata['video_stream_end_offset_s']) != timing:
            timing['status'] = 'partial'
            warnings.append('Tekrar çözme zaman sonuçları farklı; kaynak güvenilirliği incelenmeli.')
        for i, event in enumerate(nearest):
            if event is None:
                continue
            image = event.image.to_image()
            if resolution_changed:
                roi_verified = False
            label = f'{os.getenv("IZGE_APP_NAME", "İzge")} | PTS {event.pts} | {event.offset:.3f} sn'
            if not roi_verified:
                label += ' | ROI doğrulanmadı'
            image = _label(image, label)
            name = f'sample_{i + 1:02d}.png'
            with (output_dir / name).open('xb') as target:
                image.save(target, format='PNG')
            samples.append({'file': name, 'decoded_index': event.decoded_index, 'pts': event.pts,
                            'time_base': str(event.time_base), 'offset_s': event.offset,
                            'target_offset_s': float(targets[i])})
        if len({s['decoded_index'] for s in samples}) < 4:
            warnings.append('Dört farklı geçerli kare yok; örnek kareler tekrar edebilir.')
        if samples and roi_verified:
            preview = nearest[0].image.to_image()
            draw = ImageDraw.Draw(preview)
            for regions, color in ((config.animal_rois, 'lime'), (config.feeder_rois, 'cyan'),
                                   (config.stalls, 'orange')):
                for region in regions:
                    draw.polygon(region.points(), outline=color, width=4)
                    draw.text(region.points()[0], region.id, fill=color, font=_font())
            preview = _label(preview, 'ROI doğrulama kaydı mevcut; modelsiz geometri önizlemesi')
            with (output_dir / 'roi_preview.png').open('xb') as target:
                preview.save(target, format='PNG')
    after_hash = checksum(source)
    unchanged = before_hash == after_hash
    if not unchanged:
        timing['status'] = 'failed'
        warnings.append('Kaynak checksum değişti; bu çıktı kabul edilemez.')
    if resolution_changed:
        timing['status'] = 'partial'
        roi_verified = False
        warnings.append('Akış içinde çözünürlük değişti; ROI doğrulaması geçersiz.')
    if timing['status'] in ('failed', 'partial'):
        warnings.append('Kaynak zaman incelemesi kısmi/başarısız; tam çözme kabulü verilmedi.')
    manifest = {
        'schema_version': 'behavior-preflight-1', 'mode': 'models_free_preflight',
        'camera_id': config.camera_id, 'source': {
            'sha256_before': before_hash, 'sha256_after': after_hash, 'unchanged': unchanged,
            'bytes': source.stat().st_size},
        'metadata': metadata, 'timing': timing,
        'clock': {'verified': False, 'recorded_at': None},
        'roi': {'placeholder': config.placeholder, 'verified_for_video': roi_verified,
                'analysis_allowed': roi_verified and timing['status'] == 'complete',
                'preview_file': 'roi_preview.png' if (output_dir / 'roi_preview.png').exists() else None},
        'config_sha256': config.fingerprint(), 'config_file_sha256': config_bytes_hash,
        'config': config.model_dump(mode='json'), 'code': _code_revision(),
        'environment': {'python': platform.python_version(), 'platform': platform.system(),
                        'machine': platform.machine(), 'av': version('av'), 'pillow': version('pillow'),
                        'pydantic': version('pydantic'), 'pyyaml': version('pyyaml'),
                        'uv_lock_sha256': checksum(Path(__file__).resolve().parents[3] / 'uv.lock')},
        'samples': samples, 'warnings': warnings,
        'model_loaded': False, 'behavior_results_produced': False,
    }
    with (output_dir / 'manifest.json').open('x', encoding='utf-8') as target:
        json.dump(manifest, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write('\n')
    return manifest
