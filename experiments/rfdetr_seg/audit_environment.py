"""Read actual wheel notices and linked FFmpeg license/build metadata; no network access."""
import argparse
import ctypes
import hashlib
import json
import platform
from importlib.metadata import distributions
from pathlib import Path


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit():
    import av
    import torch
    try:
        import cv2
    except ImportError:
        cv2 = None
    packages = []
    for dist in sorted(distributions(), key=lambda d: d.metadata['Name'].lower()):
        notices = []
        for file in dist.files or []:
            if any(token in str(file).lower() for token in ('license', 'copying', 'notice')):
                path = Path(dist.locate_file(file))
                if path.is_file():
                    notices.append({'file': str(file), 'sha256': sha(path)})
        m = dist.metadata
        packages.append({'name': m['Name'], 'version': dist.version,
                         'license_expression': m.get('License-Expression'),
                         'license_field': m.get('License'),
                         'license_classifiers': [c for c in m.get_all('Classifier', []) if c.startswith('License')],
                         'project_urls': m.get_all('Project-URL', []), 'notices': notices})
    codecs = []
    for name, module in [('av', av), ('cv2', cv2)]:
        if module is None:
            continue
        for path in sorted((Path(module.__file__).parent / '.dylibs').glob('libavcodec*.dylib')):
            library = ctypes.CDLL(str(path))
            fields = {}
            for key in ('configuration', 'license'):
                function = getattr(library, f'avcodec_{key}')
                function.restype = ctypes.c_char_p
                fields[key] = function().decode()
            codecs.append({'package': name, 'library': path.name, 'sha256': sha(path), **fields})
    return {'python': platform.python_version(), 'platform': platform.platform(),
            'packages': packages, 'ffmpeg': codecs, 'opencv_build': cv2.getBuildInformation() if cv2 else None,
            'devices': {'cuda_available': torch.cuda.is_available(), 'mps_built': torch.backends.mps.is_built(),
                        'mps_available_in_this_process': torch.backends.mps.is_available()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit()
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(f'{len(result["packages"])} kurulu dağıtım; {len(result["ffmpeg"])} codec ikilisi incelendi')


if __name__ == '__main__':
    main()
