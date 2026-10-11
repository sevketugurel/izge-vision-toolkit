"""Pixel-edge crop/resize/letterbox transforms, including rounded resize dimensions."""
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from PIL import Image


@dataclass(frozen=True)
class CoordinateTransform:
    source_size: tuple[int, int]
    crop_box: tuple[int, int, int, int]
    output_size: tuple[int, int]
    resized_size: tuple[int, int]
    padding: tuple[int, int]

    @classmethod
    def build(cls, source_size: tuple[int, int], crop_box: tuple[int, int, int, int],
              output_size: tuple[int, int], mode: Literal['resize', 'letterbox'] = 'letterbox'):
        values = (*source_size, *crop_box, *output_size)
        if any(type(v) is not int for v in values):
            raise ValueError('Görüntü boyutları ve crop sınırları tamsayı olmalı')
        sw, sh = source_size
        x0, y0, x1, y1 = crop_box
        ow, oh = output_size
        if not (sw > 0 and sh > 0 and ow > 0 and oh > 0 and
                0 <= x0 < x1 <= sw and 0 <= y0 < y1 <= sh):
            raise ValueError('Geçersiz crop veya görüntü boyutu')
        cw, ch = x1 - x0, y1 - y0
        if mode == 'resize':
            resized, pad = output_size, (0, 0)
        elif mode == 'letterbox':
            ratio = min(ow / cw, oh / ch)
            rw, rh = max(1, min(ow, round(cw * ratio))), max(1, min(oh, round(ch * ratio)))
            resized, pad = (rw, rh), ((ow - rw) // 2, (oh - rh) // 2)
        else:
            raise ValueError('Bilinmeyen koordinat dönüşümü')
        return cls(source_size, crop_box, output_size, resized, pad)

    def to_model(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        if not isfinite(x) or not isfinite(y):
            raise ValueError('Koordinat sonlu olmalı')
        x0, y0, x1, y1 = self.crop_box
        return ((x - x0) * self.resized_size[0] / (x1 - x0) + self.padding[0],
                (y - y0) * self.resized_size[1] / (y1 - y0) + self.padding[1])

    def to_original(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        if not isfinite(x) or not isfinite(y):
            raise ValueError('Koordinat sonlu olmalı')
        x0, y0, x1, y1 = self.crop_box
        return ((x - self.padding[0]) * (x1 - x0) / self.resized_size[0] + x0,
                (y - self.padding[1]) * (y1 - y0) / self.resized_size[1] + y0)

    def transform_box(self, box: tuple[float, float, float, float], *, inverse=False):
        x0, y0, x1, y1 = box
        if not (x0 < x1 and y0 < y1):
            raise ValueError('Kutu alanı pozitif olmalı')
        transform = self.to_original if inverse else self.to_model
        return (*transform((x0, y0)), *transform((x1, y1)))

    def apply(self, image: Image.Image) -> Image.Image:
        if image.size != self.source_size:
            raise ValueError('Görüntü boyutu koordinat dönüşümüyle uyuşmuyor')
        resized = image.convert('RGB').crop(self.crop_box).resize(self.resized_size, Image.Resampling.BILINEAR)
        canvas = Image.new('RGB', self.output_size, (114, 114, 114))
        canvas.paste(resized, self.padding)
        return canvas
