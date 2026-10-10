"""Small classifier on frozen embeddings. Weights are stored as .npz (no pickle)."""
import io
import json
from dataclasses import dataclass
import numpy as np
from .states import STATES


@dataclass(frozen=True)
class LinearClassifier:
    backbone: str
    mean: np.ndarray      # (D,)
    scale: np.ndarray     # (D,)
    weights: np.ndarray   # (D, 3)
    bias: np.ndarray      # (3,)

    def predict_proba(self, embeddings: np.ndarray) -> np.ndarray:
        z = ((embeddings - self.mean) / self.scale) @ self.weights + self.bias
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def to_bytes(self) -> bytes:
        buffer = io.BytesIO()
        np.savez(buffer, mean=self.mean, scale=self.scale, weights=self.weights, bias=self.bias,
                 meta=np.frombuffer(json.dumps({'backbone': self.backbone, 'classes': list(STATES)}).encode(), dtype=np.uint8))
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> 'LinearClassifier':
        with np.load(io.BytesIO(data), allow_pickle=False) as f:
            meta = json.loads(f['meta'].tobytes())
            if meta['classes'] != list(STATES):
                raise ValueError('Sınıflandırıcı sınıf sırası uyumsuz')
            return cls(meta['backbone'], f['mean'], f['scale'], f['weights'], f['bias'])


def fit(embeddings: np.ndarray, labels: list[str], backbone: str, c: float = 1.0) -> LinearClassifier:
    """Multinomial logistic regression (L2) on standardised embeddings."""
    from sklearn.linear_model import LogisticRegression
    missing = set(STATES) - set(labels)
    if missing:
        raise ValueError(f'Eğitim için her sınıftan etiket gerekli; eksik: {", ".join(sorted(missing))}')
    y = np.array([STATES.index(label) for label in labels])
    mean = embeddings.mean(axis=0)
    scale = embeddings.std(axis=0)
    scale[scale < 1e-8] = 1
    model = LogisticRegression(C=c, max_iter=2000, class_weight='balanced')
    model.fit((embeddings - mean) / scale, y)
    weights = np.zeros((embeddings.shape[1], 3))
    bias = np.full(3, -1e9)
    for column, cls in enumerate(model.classes_):
        weights[:, cls] = model.coef_[column]
        bias[cls] = model.intercept_[column]
    return LinearClassifier(backbone, mean, scale, weights, bias)
