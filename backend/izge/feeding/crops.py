"""Stall crop geometry. The same function prepares crops for labeling, training and inference."""
from dataclasses import dataclass
import numpy as np
from PIL import Image, ImageDraw
from .camera_config import CameraConfig

# Pixels outside the stall polygon are replaced by the ImageNet mean so neighbours do not leak in.
FILL = (124, 116, 104)


@dataclass(frozen=True)
class StallGeometry:
    stall_id: str
    points: tuple[tuple[float, float], ...]   # pixels in the video resolution
    bbox: tuple[int, int, int, int]           # x0, y0, x1, y1 (exclusive), clipped to the frame


def scale_factor(config: CameraConfig, width: int, height: int) -> float:
    ref = config.reference_resolution
    if (width, height) == (ref.width, ref.height):
        return 1.
    if abs(width * ref.height - height * ref.width) > max(width, ref.width) // 100:
        raise ValueError(f'Video çözünürlüğü {width}×{height}, kamera config referansı {ref.width}×{ref.height} '
                         'ile aynı en-boy oranında değil; kesitler uygulanamaz')
    return width / ref.width


def geometries(config: CameraConfig, width: int, height: int) -> list[StallGeometry]:
    f = scale_factor(config, width, height)
    result = []
    for stall in config.stalls:
        points = tuple((x * f, y * f) for x, y in stall.points())
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        x0, y0 = max(0, int(np.floor(min(xs)))), max(0, int(np.floor(min(ys))))
        x1, y1 = min(width, int(np.ceil(max(xs)))), min(height, int(np.ceil(max(ys))))
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise ValueError(f'{stall.id}: kesit bu çözünürlükte çok küçük')
        result.append(StallGeometry(stall.id, points, (x0, y0, x1, y1)))
    return result


def crop(frame: np.ndarray, geometry: StallGeometry) -> np.ndarray:
    """RGB uint8 crop of the polygon's bounding box, with outside pixels filled."""
    x0, y0, x1, y1 = geometry.bbox
    region = frame[y0:y1, x0:x1].copy()
    mask = Image.new('L', (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in geometry.points], fill=255)
    outside = np.asarray(mask) == 0
    region[outside] = FILL
    return region


def letterbox(image: np.ndarray, size: int) -> np.ndarray:
    """Pad to a square with FILL and resize to size×size (aspect ratio preserved)."""
    h, w = image.shape[:2]
    side = max(h, w)
    canvas = np.empty((side, side, 3), dtype=np.uint8)
    canvas[:] = FILL
    top, left = (side - h) // 2, (side - w) // 2
    canvas[top:top + h, left:left + w] = image
    return np.asarray(Image.fromarray(canvas).resize((size, size), Image.Resampling.BICUBIC))


def context_image(frame: np.ndarray, geometry: StallGeometry, width: int = 480) -> Image.Image:
    """Small full-frame view with the stall crop outlined, for the labeling screen."""
    image = Image.fromarray(frame)
    f = width / image.width
    image = image.resize((width, max(1, round(image.height * f))), Image.Resampling.BILINEAR)
    draw = ImageDraw.Draw(image)
    draw.polygon([(x * f, y * f) for x, y in geometry.points], outline=(255, 214, 0), width=3)
    return image
