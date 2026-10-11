from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from izge.domain import Frame, SourceGap
from izge.video import FileVideoSource
from izge.vision.timing import PTSSampler, TimingDiagnostics


def frame(pts, base=Fraction(1, 1000), index=None):
    return Frame(pts, base, float(pts * base), 320, 180, None, decoded_index=index)


def test_vfr_and_planned_sampling_are_not_source_loss():
    diag = TimingDiagnostics(source_gap_threshold_s=2)
    rows = [diag.consume(frame(p, index=i)) for i, p in enumerate((0, 40, 160, 230, 1100))]
    assert [r.pts for r in rows if r.selected_detection] == [0, 230, 1100]
    assert rows[1].sampling_reason == 'planned_sampling_skip'
    summary = diag.summary(1.2)
    assert summary['gap_reasons'] == {}
    assert summary['supported_timestamp_span_s'] == 1.1
    assert summary['empty_detection_sampling_windows'] == 3


def test_rational_sampler_does_not_miss_exact_bucket_edges():
    sampler = PTSSampler(3)
    assert all(sampler.select(Fraction(i, 3)) for i in range(4))
    assert not sampler.select(Fraction(1))


def test_nonzero_origin_and_mixed_frame_time_bases():
    diag = TimingDiagnostics(origin=Fraction(2))
    assert diag.consume(frame(2000)).offset_s == 0
    assert diag.consume(frame(9, Fraction(1, 4))).offset_s == .25
    assert diag.consume(frame(2300)).offset_s == .3
    assert diag.summary(.4)['supported_timestamp_span_s'] == .3


def test_gap_brackets_use_last_valid_time_base_when_raw_source_basis_changed():
    diag = TimingDiagnostics()
    diag.consume(frame(1000))
    diag.consume(SourceGap('missing_pts', before_pts=99, time_base=Fraction(1, 100),
                           pts_time_base=Fraction(1, 100)))
    diag.consume(frame(1200))
    gap = diag.summary(1.3)['source_gaps'][0]
    assert gap['before_pts'] == 1000 and gap['after_pts'] == 1200
    assert gap['time_base'] == {'numerator': 1, 'denominator': 1000}


def test_leading_gap_and_unknown_declared_end_are_explicit():
    diag = TimingDiagnostics()
    diag.consume(frame(200))
    diag.consume(frame(400))
    summary = diag.summary(.5)
    assert summary['leading_unobserved_video_s'] == .2
    assert summary['unsupported_video_timeline_s'] == .3
    assert diag.summary(None)['status'] == 'warning'
    assert diag.summary(.3)['declared_video_end_consistent'] is False
    assert diag.summary(.3)['unsupported_video_timeline_s'] is None


def test_missing_pts_is_bracketed_by_valid_pts_not_fps():
    diag = TimingDiagnostics()
    diag.consume(frame(0))
    row = diag.consume(SourceGap('missing_pts', time_base=Fraction(1, 1000), decoded_index=1))
    assert row.pts is None and row.offset_s is None and not row.selected_detection
    diag.consume(frame(240))
    gap = diag.summary(.3)['source_gaps'][0]
    assert (gap['before_pts'], gap['after_pts']) == (0, 240)
    assert (gap['start_offset'], gap['end_offset']) == (0, .24)
    assert gap['bounds_uncertain']
    assert diag.summary(.3)['supported_timestamp_span_s'] == 0


def test_nonmonotonic_raw_pts_is_rejected_without_repair_or_sorting():
    diag = TimingDiagnostics()
    rows = [diag.consume(frame(p)) for p in (0, 400, 200, 600)]
    assert rows[2].pts == 200 and rows[2].offset_s == .2
    assert rows[2].source_status == 'non_monotonic_pts' and not rows[2].selected_detection
    summary = diag.summary(.7)
    assert summary['status'] == 'partial'
    assert summary['supported_timestamp_span_s'] == .4
    assert summary['source_gaps'][0]['start_offset'] == .4
    assert summary['source_gaps'][0]['end_offset'] == .6


def test_discontinuity_is_candidate_not_proven_missing_frame():
    diag = TimingDiagnostics(source_gap_threshold_s=1)
    diag.consume(frame(0))
    row = diag.consume(frame(2000))
    summary = diag.summary(2.1)
    assert row.source_status == 'timestamp_discontinuity'
    assert row.selected_detection
    assert summary['status'] == 'warning' and summary['source_gaps'][0]['bounds_uncertain']
    assert summary['supported_timestamp_span_s'] == 0


def test_decode_failure_keeps_open_boundary_and_partial_status():
    diag = TimingDiagnostics()
    diag.consume(frame(0))
    diag.consume(frame(1000))
    diag.consume(SourceGap('decode_error'))
    summary = diag.summary(2)
    assert summary['status'] == 'partial'
    assert summary['source_gaps'][0]['end_offset'] is None
    assert summary['unobserved_video_tail_s'] == 1


def test_eof_does_not_extend_last_timestamp():
    diag = TimingDiagnostics(source_gap_threshold_s=10)
    diag.consume(frame(0))
    diag.consume(frame(4200))
    summary = diag.summary(4.24)
    assert summary['supported_timestamp_span_s'] == 4.2
    assert summary['unobserved_video_tail_s'] == .04
    assert summary['behavior_duration_produced'] is False


@pytest.mark.parametrize('event,reason', [(frame(-1), 'before_stream_start'),
                                         (frame(1, Fraction(0)), 'invalid_time_base')])
def test_unusable_timestamps(event, reason):
    diag = TimingDiagnostics()
    assert diag.consume(event).source_status == reason
    assert diag.summary(None)['status'] == 'failed'


def test_gap_details_are_bounded_and_counts_are_complete():
    diag = TimingDiagnostics()
    for _ in range(1010):
        diag.consume(SourceGap('missing_pts'))
    summary = diag.summary(None)
    assert summary['gap_reasons']['missing_pts'] == 1010
    assert len(summary['source_gaps']) == 1000
    assert summary['source_gap_records_omitted'] == 10


class Container:
    def __init__(self, frames, fail=False):
        self.items, self.fail = frames, fail
        self.streams = SimpleNamespace(video=[SimpleNamespace(start_time=1000, time_base=Fraction(1, 1000))])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def decode(self, stream):
        yield from self.items
        if self.fail:
            raise ValueError('private decoder text')


def raw_frame(pts, corrupt=False, base=Fraction(1, 1000)):
    return SimpleNamespace(pts=pts, time_base=base, is_corrupt=corrupt, width=320, height=180)


def test_reader_emits_missing_corrupt_and_error_events_and_legacy_raises(monkeypatch):
    import av
    data = [raw_frame(1000), raw_frame(None), raw_frame(1100, corrupt=True), raw_frame(1200)]
    monkeypatch.setattr(av, 'open', lambda *a, **k: Container(data, fail=True))
    reader = FileVideoSource(Path('fixture.mp4'))
    events = list(reader.read_events())
    assert [e.reason for e in events if isinstance(e, SourceGap)] == ['missing_pts', 'corrupt_frame', 'decode_error']
    assert [e.offset for e in events if isinstance(e, Frame)] == [0, .2]
    assert [e.decoded_index for e in events[:-1]] == [0, 1, 2, 3]
    with pytest.raises(ValueError, match='tamamen çözülemedi'):
        list(reader.frames())


def test_reader_keeps_legacy_frame_interface_and_correct_origin(monkeypatch):
    import av
    monkeypatch.setattr(av, 'open', lambda *a, **k: Container([
        raw_frame(1000), raw_frame(None), raw_frame(5, base=Fraction(1, 4))]))
    rows = list(FileVideoSource(Path('fixture.mp4')).frames())
    assert [f.offset for f in rows] == [0, .25]
