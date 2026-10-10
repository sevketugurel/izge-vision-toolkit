"""Frozen image backbones that turn stall crops into embeddings.

Only DINOv3 ViT-S/16 is a production backbone (Decision 4). It is gated on Hugging Face and
needs HF_TOKEN; when the weights cannot be loaded the pipeline stops with an explicit error
and is never silently replaced by another model. Tests inject a stub backbone directly.
"""
import os
from typing import Protocol
import numpy as np
from .crops import letterbox

DINOV3_ID = 'facebook/dinov3-vits16-pretrain-lvd1689m'
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class Backbone(Protocol):
    id: str
    dim: int
    def embed(self, crops: list[np.ndarray]) -> np.ndarray: ...   # (N, dim) float32, L2-normalised


class BackboneUnavailable(RuntimeError):
    pass


def pick_device(torch) -> str:
    wanted = os.getenv('IZGE_DEVICE', 'auto')
    if wanted != 'auto':
        return wanted
    if torch.cuda.is_available():
        return 'cuda'
    if torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'


class Dinov3Backbone:
    """CLS token concatenated with the mean patch token (register tokens excluded)."""
    def __init__(self, model_id: str = DINOV3_ID, model=None, device: str | None = None,
                 image_size: int = 224, batch_size: int = 32):
        try:
            import torch
        except ImportError as exc:
            raise BackboneUnavailable('PyTorch kurulu değil; `uv sync --extra ml` ile kurun') from exc
        self.torch = torch
        if model is None:
            from transformers import AutoModel
            token = os.getenv('HF_TOKEN') or None
            try:
                model = AutoModel.from_pretrained(model_id, token=token)
            except Exception as exc:
                raise BackboneUnavailable(
                    f'{model_id} ağırlıkları yüklenemedi. Model Hugging Face üzerinde lisans onayı ister; '
                    'HF_TOKEN tanımlayın ve lisansı kabul edin.') from exc
        self.id, self.device = model_id, device or pick_device(torch)
        self.model = model.eval().to(self.device)
        self.registers = int(getattr(model.config, 'num_register_tokens', 0) or 0)
        self.dim = 2 * int(model.config.hidden_size)
        self.image_size, self.batch_size = image_size, batch_size

    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        torch = self.torch
        out = []
        for i in range(0, len(crops), self.batch_size):
            batch = np.stack([letterbox(c, self.image_size) for c in crops[i:i + self.batch_size]])
            x = (batch.astype(np.float32) / 255 - IMAGENET_MEAN) / IMAGENET_STD
            pixels = torch.from_numpy(x.transpose(0, 3, 1, 2).copy()).to(self.device)
            with torch.inference_mode():
                hidden = self.model(pixel_values=pixels).last_hidden_state
            cls, patches = hidden[:, 0], hidden[:, 1 + self.registers:]
            features = torch.cat([cls, patches.mean(dim=1)], dim=1)
            features = torch.nn.functional.normalize(features.float(), dim=1)
            out.append(features.cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, self.dim), dtype=np.float32)


def load_backbone(backbone_id: str) -> Backbone:
    if backbone_id != DINOV3_ID:
        raise BackboneUnavailable(f'Desteklenmeyen backbone: {backbone_id}')
    return Dinov3Backbone(backbone_id)


def slug(backbone_id: str) -> str:
    return ''.join(ch if ch.isalnum() or ch in '.-' else '-' for ch in backbone_id.lower())
