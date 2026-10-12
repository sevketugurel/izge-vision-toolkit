"""Standard-library artifact IO, usable in image-only training without video/ML imports."""
import hashlib
import json
from pathlib import Path


def checksum(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def prepare_output(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Çıktı klasörü yeni veya boş olmalı')
    path.mkdir(parents=True,exist_ok=True)


def write_json(path: Path, value: dict) -> None:
    with path.open('x',encoding='utf-8') as target:
        json.dump(value,target,ensure_ascii=False,indent=2,allow_nan=False)
        target.write('\n')
