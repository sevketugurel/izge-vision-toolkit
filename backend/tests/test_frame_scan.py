import pytest

from izge.vision import tracking_demo
from izge.vision.frame_scan import scan_video


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    return tracking_demo.write_video(tmp_path_factory.mktemp('scan') / 'v.mkv', tracking_demo.timestamps_ms())


def test_scan_samples_by_pts_and_selects_within_pool(video):
    result = scan_video(video, hz=1, select=4, min_gap_s=1)
    offsets = [r['offset_s'] for r in result['samples']]
    assert offsets == sorted(offsets) and len(offsets) == len({ms // 1000 for ms in tracking_demo.timestamps_ms()})
    assert result['source_problems'] == []
    assert result['appearance_counts'] == {'monochrome_night_appearance': len(offsets)}  # flat grey scene
    assert len(result['selected_indices']) <= 4 and set(result['selected_indices']) <= set(range(len(offsets)))
    assert result['source'] == 'v.mkv'


def test_scan_respects_sample_cap(video):
    result = scan_video(video, hz=10, max_samples=5)
    assert len(result['samples']) == 5 and 'max_samples_reached' in result['source_problems']
