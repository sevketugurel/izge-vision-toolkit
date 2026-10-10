from datetime import datetime, timedelta, timezone
import pytest
from izge.gpu_controller import decide, Controller

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
IDLE = timedelta(minutes=15)


@pytest.mark.parametrize('status, pending, idle_for, expected', [
    ('TERMINATED', 1, 0, 'start'),        # queued work, VM off (or preempted)
    ('STOPPED', 3, 0, 'start'),
    ('RUNNING', 2, 60, None),             # busy: never stop
    ('STAGING', 1, 0, None),              # transitional: wait
    ('STOPPING', 1, 0, None),
    ('RUNNING', 0, 14, None),             # idle but below the threshold
    ('RUNNING', 0, 15, 'stop'),
    ('TERMINATED', 0, 99, None),
])
def test_decide(status, pending, idle_for, expected):
    assert decide(status, pending, NOW - timedelta(minutes=idle_for), NOW, IDLE) == expected


class FakeInstance:
    def __init__(self, status):
        self.state, self.calls = status, []
    def status(self):
        return self.state
    def start(self):
        self.calls.append('start')
        self.state = 'RUNNING'
    def stop(self):
        self.calls.append('stop')
        self.state = 'TERMINATED'


def test_controller_tracks_last_work_and_stops_after_idle():
    vm = FakeInstance('TERMINATED')
    c = Controller(vm, IDLE, NOW)
    assert c.step(1, NOW) == 'start'
    assert c.step(0, NOW + timedelta(minutes=10)) is None
    assert c.step(0, NOW + timedelta(minutes=16)) == 'stop'
    assert vm.calls == ['start', 'stop']
