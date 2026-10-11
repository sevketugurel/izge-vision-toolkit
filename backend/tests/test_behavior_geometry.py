import pytest
from PIL import Image

from izge.vision.geometry import CoordinateTransform


def test_known_letterbox_coordinates_and_box():
    transform = CoordinateTransform.build((1000, 600), (100, 50, 900, 450), (640, 640))
    assert transform.resized_size == (640, 320)
    assert transform.padding == (0, 160)
    assert transform.to_model((100, 50)) == (0, 160)
    assert transform.to_model((500, 250)) == (320, 320)
    assert transform.to_original((640, 480)) == (900, 450)
    assert transform.transform_box((100, 50, 900, 450)) == (0, 160, 640, 480)
    assert transform.transform_box((0, 160, 640, 480), inverse=True) == (100, 50, 900, 450)


def test_nonuniform_resize_and_polygon_roundtrip():
    transform = CoordinateTransform.build((1000, 600), (100, 50, 900, 450), (400, 400), 'resize')
    assert transform.to_model((900, 450)) == (400, 400)
    for point in ((100, 50), (320, 135), (810, 402), (900, 450)):
        assert transform.to_original(transform.to_model(point)) == pytest.approx(point)


def test_rounded_letterbox_uses_actual_axis_scales():
    transform = CoordinateTransform.build((40, 40), (3, 7, 16, 18), (17, 17))
    assert transform.resized_size == (17, 14)
    assert transform.padding == (0, 1)
    assert transform.to_model((16, 18)) == (17, 15)
    for point in ((3, 7), (9.5, 12.5), (16, 18)):
        assert transform.to_original(transform.to_model(point)) == pytest.approx(point, abs=1e-10)


def test_image_crop_letterbox_matches_coordinates():
    image = Image.new('RGB', (20, 20), 'blue')
    for y in range(4, 8):
        for x in range(2, 10):
            image.putpixel((x, y), (255, 0, 0))
    transform = CoordinateTransform.build((20, 20), (2, 4, 10, 8), (16, 16))
    output = transform.apply(image)
    assert output.size == (16, 16)
    assert output.getpixel((8, 0)) == (114, 114, 114)
    assert output.getpixel((8, 8)) == (255, 0, 0)
    assert output.getpixel((8, 15)) == (114, 114, 114)
    with pytest.raises(ValueError, match='boyutu'):
        transform.apply(Image.new('RGB', (10, 10)))


@pytest.mark.parametrize('crop,size,mode', [
    ((-1, 0, 5, 5), (10, 10), 'resize'), ((0, 0, 21, 5), (10, 10), 'resize'),
    ((0, 0, 0, 5), (10, 10), 'resize'), ((0, 0, 5, 5), (0, 10), 'resize'),
    ((0, 0, 5.5, 5), (10, 10), 'resize'), ((0, 0, 5, 5), (10, 10), 'other'),
])
def test_invalid_transform(crop, size, mode):
    with pytest.raises(ValueError):
        CoordinateTransform.build((20, 20), crop, size, mode)


def test_nonfinite_points_are_rejected():
    transform = CoordinateTransform.build((20, 20), (0, 0, 20, 20), (10, 10))
    with pytest.raises(ValueError):
        transform.to_original((float('nan'), 3))
    with pytest.raises(ValueError):
        transform.to_model((3, float('inf')))
