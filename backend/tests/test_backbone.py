"""DINOv3 wrapper code path with a tiny randomly initialised DINOv3ViT (no weights, no network).

This checks preprocessing, token pooling and shapes only. It is NOT a DINOv3 embedding: the real
weights are gated (HF_TOKEN) and are never replaced by another model.
"""
import numpy as np
import pytest

torch = pytest.importorskip('torch')
transformers = pytest.importorskip('transformers')
from izge.feeding.backbone import Dinov3Backbone, BackboneUnavailable, load_backbone, DINOV3_ID  # noqa: E402


def tiny_model():
    config = transformers.DINOv3ViTConfig(hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                                          num_attention_heads=2, num_register_tokens=4, patch_size=16, image_size=64)
    torch.manual_seed(0)
    return transformers.DINOv3ViTModel(config)


def test_wrapper_pools_cls_and_patches_and_normalises():
    backbone = Dinov3Backbone('random/tiny-dinov3', model=tiny_model(), device='cpu', image_size=64, batch_size=2)
    crops = [np.random.default_rng(i).integers(0, 255, (40 + i * 7, 30, 3), dtype=np.uint8) for i in range(3)]
    out = backbone.embed(crops)
    assert out.shape == (3, 64) and backbone.dim == 64 and backbone.registers == 4
    assert np.allclose(np.linalg.norm(out, axis=1), 1, atol=1e-5)
    assert np.allclose(out, backbone.embed(crops), atol=1e-5)   # frozen, deterministic


def test_only_dinov3_is_loadable_and_missing_weights_fail_loudly(monkeypatch):
    with pytest.raises(BackboneUnavailable, match='Desteklenmeyen'):
        load_backbone('facebook/dinov2-small')
    def refuse(*args, **kwargs):
        raise OSError('gated repo')
    monkeypatch.setattr(transformers.AutoModel, 'from_pretrained', refuse)
    with pytest.raises(BackboneUnavailable, match='HF_TOKEN'):
        load_backbone(DINOV3_ID)
