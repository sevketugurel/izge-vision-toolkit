"""Models-free local behavior preflight. Uses existing dependencies only."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from izge.vision.preflight import PreflightError, inspect_video  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Modelsiz video/PTS/ROI ön kontrolü')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = inspect_video(args.source, args.config, args.output_dir)
    except PreflightError as exc:
        print(f'Ön kontrol başarısız: {exc}', file=sys.stderr)
        return 2
    except OSError:
        print('Ön kontrol başarısız: kaynak/config/çıktı erişimini ve geçerli yapılandırmayı kontrol edin.',
              file=sys.stderr)
        return 2
    metadata, timing = manifest['metadata'], manifest['timing']
    for warning in manifest['warnings']:
        print(f'Uyarı: {warning}', file=sys.stderr)
    print(f"Codec: {metadata['codec']}; çözünürlük: {metadata['width']}×{metadata['height']}")
    print(f"Ham ilk/son PTS: {timing['first_frame_pts']} / {timing['last_frame_pts']}; "
          f"time-base: {metadata['time_base']['numerator']}/{metadata['time_base']['denominator']}")
    print(f"Son kare: {timing['last_frame_offset_s']} sn; video akışı: "
          f"{metadata['video_stream_duration_s']} sn; kapsayıcı: {metadata['container_duration_s']} sn")
    print(f"Geçerli kare: {timing['counts'].get('valid_timestamp_frames', 0)}; "
          f"örnek PNG: {len(manifest['samples'])}; kaynak değişmedi: {manifest['source']['unchanged']}")
    print(f"Durum: {timing['status']}; manifest.json ve frames.csv üretildi.")
    return 0 if timing['status'] in ('complete', 'warning') else 2


if __name__ == '__main__':
    raise SystemExit(main())
