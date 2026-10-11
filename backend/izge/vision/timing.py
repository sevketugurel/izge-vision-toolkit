"""PTS diagnostics and explicit sampling decisions; no nominal-FPS timing repair."""
from collections import Counter
from dataclasses import asdict, dataclass, replace
from fractions import Fraction

from ..domain import Frame, SourceGap


@dataclass(frozen=True)
class TimingRow:
    record_type: str
    decoded_index: int | None
    pts: int | None
    time_base_numerator: int | None
    time_base_denominator: int | None
    offset_s: float | None
    source_status: str
    selected_detection: bool = False
    selected_posture: bool = False
    sampling_reason: str = 'source_rejected'
    gap_before_s: float | None = None


class PTSSampler:
    """First source frame per absolute PTS bucket. Empty buckets are not invented frames."""
    def __init__(self, hz: float):
        self.hz = Fraction(str(hz))
        if self.hz <= 0:
            raise ValueError('Örnekleme sıklığı pozitif olmalı')
        self.last_bucket = None
        self.empty_windows = 0

    def select(self, timestamp: Fraction) -> bool:
        bucket = timestamp // (1 / self.hz)
        if self.last_bucket is not None and bucket <= self.last_bucket:
            return False
        if self.last_bucket is not None:
            self.empty_windows += max(0, bucket - self.last_bucket - 1)
        self.last_bucket = bucket
        return True


class TimingDiagnostics:
    GAP_LIMIT = 1000

    def __init__(self, detection_hz=5, posture_hz=1, source_gap_threshold_s=1,
                 origin: Fraction = Fraction(0)):
        self.origin = origin
        self.threshold = Fraction(str(source_gap_threshold_s))
        if self.threshold <= 0:
            raise ValueError('Kaynak zaman boşluğu eşiği pozitif olmalı')
        self.detection = PTSSampler(detection_hz)
        self.posture = PTSSampler(posture_hz)
        self.counts = Counter()
        self.gap_reasons = Counter()
        self.gaps: list[SourceGap] = []
        self.pending_indices: list[int] = []
        self.pending_failure = False
        self.last: Frame | None = None
        self.last_time: Fraction | None = None
        self.first_time: Fraction | None = None
        self.first_pts: int | None = None
        self.supported_span = Fraction(0)

    def _gap(self, gap: SourceGap) -> None:
        self.gap_reasons[gap.reason] += 1
        self.pending_failure = True
        if len(self.gaps) < self.GAP_LIMIT:
            self.gaps.append(gap)
            if gap.end_offset is None:
                self.pending_indices.append(len(self.gaps) - 1)

    def consume(self, event: Frame | SourceGap) -> TimingRow:
        self.counts['records'] += 1
        if isinstance(event, SourceGap):
            self.counts['rejected_source_records'] += 1
            self._gap(replace(event, before_pts=self.last.pts if self.last else None,
                              time_base=self.last.time_base if self.last else None,
                              start_offset=float(self.last_time) if self.last_time is not None else None))
            base = event.pts_time_base
            return TimingRow('source_gap', event.decoded_index, event.pts,
                             base.numerator if base else None, base.denominator if base else None,
                             None, event.reason)

        timestamp = event.pts * event.time_base - self.origin
        base = event.time_base
        if base <= 0 or timestamp < 0 or (self.last_time is not None and timestamp <= self.last_time):
            reason = ('invalid_time_base' if base <= 0 else
                      'before_stream_start' if timestamp < 0 else 'non_monotonic_pts')
            self.counts['rejected_source_records'] += 1
            self._gap(SourceGap(reason, before_pts=self.last.pts if self.last else None,
                                time_base=self.last.time_base if self.last else None,
                                start_offset=float(self.last_time) if self.last_time is not None
                                else None, decoded_index=event.decoded_index, pts=event.pts,
                                pts_time_base=base))
            return TimingRow('frame', event.decoded_index, event.pts, base.numerator, base.denominator,
                             float(timestamp), reason)

        delta = timestamp - self.last_time if self.last_time is not None else None
        discontinuity = delta is not None and delta > self.threshold
        if discontinuity:
            # VFR may legitimately be sparse: this is an uncertain candidate, not a dropped-frame count.
            self._gap(SourceGap('timestamp_discontinuity', before_pts=self.last.pts,
                                after_pts=event.pts, time_base=base if base == self.last.time_base else None,
                                start_offset=float(self.last_time), end_offset=float(timestamp)))
        if delta is not None and not self.pending_failure:
            self.supported_span += delta
        for index in self.pending_indices:
            gap = self.gaps[index]
            compatible = gap.time_base == base
            self.gaps[index] = replace(gap, after_pts=event.pts if compatible else None,
                                      end_offset=float(timestamp), time_base=base if compatible else None,
                                      before_pts=gap.before_pts if compatible else None)
        self.pending_indices.clear()
        self.pending_failure = False
        if self.first_time is None:
            self.first_time, self.first_pts = timestamp, event.pts
        self.last, self.last_time = event, timestamp
        selected_detection = self.detection.select(timestamp)
        selected_posture = self.posture.select(timestamp)
        self.counts['valid_timestamp_frames'] += 1
        self.counts['selected_detection'] += selected_detection
        self.counts['selected_posture'] += selected_posture
        self.counts['planned_sampling_skips'] += not selected_detection
        reason = ('selected_detection_and_posture' if selected_detection and selected_posture else
                  'selected_detection' if selected_detection else
                  'selected_posture' if selected_posture else 'planned_sampling_skip')
        return TimingRow('frame', event.decoded_index, event.pts, base.numerator, base.denominator,
                         float(timestamp), 'timestamp_discontinuity' if discontinuity else 'valid',
                         selected_detection, selected_posture, reason,
                         float(delta) if delta is not None else None)

    def summary(self, video_end_s: float | None) -> dict:
        first = float(self.first_time) if self.first_time is not None else None
        last = float(self.last_time) if self.last_time is not None else None
        status = ('failed' if last is None else
                  'partial' if self.counts['rejected_source_records'] else
                  'warning' if self.gap_reasons else 'complete')
        duration_consistent = (self.last_time <= Fraction(str(video_end_s))
                               if video_end_s is not None and self.last_time is not None else None)
        if status == 'complete' and duration_consistent is not True:
            status = 'warning'
        gaps = []
        for gap in self.gaps:
            record = asdict(gap)
            for key in ('time_base', 'pts_time_base'):
                base = getattr(gap, key)
                record[key] = {'numerator': base.numerator, 'denominator': base.denominator} if base else None
            gaps.append(record)
        return {
            'status': status, 'counts': dict(self.counts),
            'first_frame_pts': self.first_pts, 'last_frame_pts': self.last.pts if self.last else None,
            'first_frame_offset_s': first, 'last_frame_offset_s': last,
            'supported_timestamp_span_s': float(self.supported_span),
            'unsupported_between_frames_s': float(self.last_time - self.first_time - self.supported_span)
            if self.last_time is not None else None,
            'leading_unobserved_video_s': first,
            'declared_video_end_consistent': duration_consistent,
            'unsupported_video_timeline_s': float(Fraction(str(video_end_s)) - self.supported_span)
            if duration_consistent is True else None,
            'unobserved_video_tail_s': float(max(Fraction(0), Fraction(str(video_end_s)) - self.last_time))
            if video_end_s is not None and last is not None else None,
            'gap_reasons': dict(self.gap_reasons), 'source_gaps': gaps,
            'source_gap_records_omitted': sum(self.gap_reasons.values()) - len(gaps),
            'empty_detection_sampling_windows': self.detection.empty_windows,
            'empty_posture_sampling_windows': self.posture.empty_windows,
            'timing_policy': 'pts_only_no_eof_extension',
            'discontinuity_policy': 'candidate_only_vfr_is_not_proof_of_dropped_frames',
            'sampling_policy': 'first_valid_frame_per_pts_bucket',
            'behavior_duration_produced': False,
        }
