"""Draft-only review rendering; never creates or changes verification records."""
from pathlib import Path

from PIL import ImageDraw

from ..video import FileVideoSource
from .config import load
from .inspection import code_provenance, prepare_output, reference_frames, write_json
from .preflight import _font, _label, checksum

DRAFT_LABEL = 'TASLAK — İNSAN DOĞRULAMASI YOK'


def review_rois(source: Path, config_path: Path, output_dir: Path, notes: Path | None = None) -> dict:
    config = load(config_path)
    if not config.placeholder or config.geometry_verification is not None:
        raise ValueError('Taslak inceleme yalnız placeholder=true ve geometry_verification=null kabul eder')
    reader = FileVideoSource(source)
    before = checksum(source)
    metadata = reader.metadata()
    config.validate_resolution(metadata['width'], metadata['height'])
    if (metadata['width'], metadata['height']) != (config.reference_width, config.reference_height):
        raise ValueError('Taslak ölçümü referans piksel çözünürlüğünde yapılmalı')
    frames, targets, timing = reference_frames(reader, config, metadata)
    prepare_output(output_dir)
    regions = [*config.animal_rois, *config.feeder_rois, *config.stalls]
    report = [f'# {DRAFT_LABEL}', '', 'Bu paket geometri adayıdır; insan onayı veya davranış sonucu değildir.',
              '', f'Kaynak SHA256: `{before}`', f'Referans: {metadata["width"]} × {metadata["height"]} piksel.',
              'Koordinatlar orijinal görüntünün sol üst kökeninden (x,y); nominal FPS kullanılmaz.', '',
              '| No | ROI ID | Piksel köşeleri, sıralı |', '| --- | --- | --- |']
    for n, region in enumerate(regions, 1):
        report.append(f'| {n} | {region.id} | {region.points()} |')
    samples = []
    for i, frame in enumerate(frames, 1):
        image = frame.image.to_image().convert('RGB')
        draw = ImageDraw.Draw(image)
        for n, region in enumerate(regions, 1):
            color = 'lime' if region in config.animal_rois else 'cyan'
            points = region.points()
            draw.polygon(points, outline=color, width=5)
            for vertex, (x, y) in enumerate(points, 1):
                draw.ellipse((x-6, y-6, x+6, y+6), fill=color)
                # Edge vertices remain visible inside the image.
                at = (min(x+8, image.width-90), min(y+8, image.height-36))
                draw.text(at, f'{n}.{vertex}', fill=color, stroke_width=2,
                          stroke_fill='black', font=_font())
            x = sum(p[0] for p in points) / len(points)
            y = sum(p[1] for p in points) / len(points)
            draw.text((x, y), f'{n}: {region.id}', fill=color, font=_font(),
                      stroke_width=2, stroke_fill='black')
        image = _label(image, f'{DRAFT_LABEL} | PTS {frame.pts} | {frame.offset:.3f} sn')
        name = f'roi_draft_{i:02d}.png'
        with (output_dir / name).open('xb') as target:
            image.save(target, format='PNG')
        samples.append({'file': name, 'pts': frame.pts, 'time_base': str(frame.time_base),
                        'offset_s': frame.offset, 'target_offset_s': float(targets[i-1])})
    after = checksum(source)
    report += ['', 'Dört zaman: ' + ', '.join(f'PTS {s["pts"]} / {s["offset_s"]} sn' for s in samples),
               '', 'Kontrol edilecekler: sol/sağ gövde sınırları ve başların koridora taşması; '
               'yemlik adayının gerçek işlevi ve iki kenarı; sütun/demir örtülmeleri.',
               'Bölme koordinatları kesinleşmediği için stalls boştur. Kayıt saati/kimlik doğrulanmadı.',
               'geometry_verification=null; placeholder=true; ROI tabanlı analiz kapalı.',
               f'Okuma sonrası SHA256: `{after}`; değişmedi: {before == after}.']
    if notes is not None:
        report += ['', notes.read_text(encoding='utf-8')]
    (output_dir / 'roi_review.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    manifest = {'schema_version': 'behavior-roi-review-1', 'status': 'draft' if before == after else 'failed',
                'roi_verified': False, 'roi_analysis_allowed': False, 'experiment_only': True,
                'geometry_verification': None, 'source': {'sha256_before': before, 'sha256_after': after,
                                                        'unchanged': before == after},
                'config': config.model_dump(mode='json'), 'config_sha256': config.fingerprint(),
                'config_file_sha256': checksum(config_path), 'metadata': metadata, 'timing': timing,
                'samples': samples, 'code': code_provenance(), 'model_loaded': False}
    write_json(output_dir / 'roi_manifest.json', manifest)
    return manifest
