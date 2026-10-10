"""BORIS import parsing and evaluation metrics (pure, no database)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from izge.feeding import boris
from izge.feeding.evaluation import evaluate, markdown
from synthetic import boris_tsv, BORIS_HEADER

MAPPING = boris.load_mapping()
STALLS = {'A', 'B'}
IST = ZoneInfo('Europe/Istanbul')


def test_parse_tsv_and_csv_with_default_mapping():
    for delimiter in ('\t', ','):
        events = boris.parse(boris_tsv(delimiter=delimiter), MAPPING, STALLS, 90)
        assert len(events) == 8
        assert {(e.stall, e.state) for e in events if e.start == 0} == {('A', 'yemde'), ('B', 'yemde_degil')}


def test_subject_mapping_and_case_insensitive_behaviors():
    mapping = boris.BorisMapping(behaviors={'EATING': 'yemde'}, subjects={'Cow 1': 'A'})
    text = '\t'.join(BORIS_HEADER) + '\n' + '\t'.join(['o', '', '', '', '', '', 'Cow 1', ' eating ', '', '', 'STATE',
                                                         '1', '5', '4', '', '']) + '\n'
    assert boris.parse(text, mapping, STALLS) == [boris.GoldEvent('A', 'yemde', 1., 5.)]


def row(**v):
    base = dict(zip(BORIS_HEADER, ['o', '', '', '', '', '', 'A', 'Yemde', '', '', 'STATE', '0', '5', '5', '', '']))
    base.update(v)
    return '\t'.join(base[h] for h in BORIS_HEADER)


@pytest.mark.parametrize('rows, message', [
    ([row(Behavior='Koşuyor')], 'bilinmeyen davranış'),
    ([row(Subject='Z')], 'bilinmeyen bölme'),
    ([row(**{'Behavior type': 'POINT'})], 'POINT'),
    ([row(**{'Start (s)': '5', 'Stop (s)': '5'})], 'geçersiz aralık'),
    ([row(**{'Stop (s)': 'x'})], 'sayı değil'),
    ([row(**{'Stop (s)': '500'})], 'video süresini'),
    ([row(), row(Behavior='Görünmüyor', **{'Start (s)': '3', 'Stop (s)': '8'})], 'çakışan'),
    ([], 'olay yok'),
])
def test_parse_rejects(rows, message):
    text = '\t'.join(BORIS_HEADER) + '\n' + '\n'.join(rows)
    with pytest.raises(ValueError, match=message):
        boris.parse(text, MAPPING, STALLS, 90)


def test_missing_column_is_named():
    with pytest.raises(ValueError, match='Start'):
        boris.parse('Subject\tBehavior\tStop (s)\nA\tYemde\t3\n', MAPPING, STALLS)


def day_time(hour):
    return datetime(2026, 10, 10, hour, tzinfo=IST).timestamp()


def is_day(local):
    return 6 <= local.hour < 19


def test_stall_hour_mae_confusion_and_rates():
    t0 = day_time(14)
    gold = [('A', 'yemde', t0, t0 + 600), ('A', 'yemde_degil', t0 + 600, t0 + 3600),
            ('B', 'gorunmuyor', t0, t0 + 1800)]
    pred = [('A', 'yemde', t0, t0 + 780, .9), ('A', 'yemde_degil', t0 + 780, t0 + 3600, .8),
            ('B', 'gorunmuyor', t0, t0 + 900, .4)]
    r = evaluate(gold, pred, 'Europe/Istanbul', {'A': 'yakin', 'B': 'uzak'}, is_day)
    a_hour = next(x for x in r['stall_hours'] if x['stall'] == 'A')
    assert a_hour['truth_minutes'] == 10 and a_hour['pred_minutes'] == 13 and a_hour['error_minutes'] == 3
    assert r['mae_minutes']['near_mid'] == 3 and r['mae_minutes']['by_mesafe']['uzak'] == 0
    assert r['confusion']['yemde_degil']['yemde'] == 180 and r['confusion']['gorunmuyor']['tahmin_yok'] == 900
    assert r['by_mesafe']['uzak']['pred_not_visible_rate'] == .5 and r['by_mesafe']['uzak']['mean_confidence'] == .4
    assert r['compared_seconds'] == 3600 + 1800
    assert r['requirements']['met'] is False and r['acceptance']['accepted'] is False
    assert 'gereksinim' in r['acceptance']['reason']


def full_gold(error_seconds=0, night=True, synthetic=False):
    gold, pred, mesafe = [], [], {}
    for i in range(6):
        stall = f'S{i}'
        mesafe[stall] = ('yakin', 'orta', 'uzak')[i % 3]
        start = day_time(22 if night and i == 0 else 10)
        gold += [(stall, 'yemde', start, start + 3600), (stall, 'yemde_degil', start + 3600, start + 7200)]
        pred += [(stall, 'yemde', start, start + 3600 + error_seconds, .9),
                 (stall, 'yemde_degil', start + 3600 + error_seconds, start + 7200, .9)]
    return evaluate(gold, pred, 'Europe/Istanbul', mesafe, is_day, synthetic=synthetic)


def test_acceptance_flag_thresholds_and_requirements():
    ok = full_gold(error_seconds=170)
    assert ok['requirements'] == dict(stalls=6, stalls_required=6, seconds_per_stall_required=7200,
                                      night_window=True, met=True)
    assert ok['mae_minutes']['by_daynight']['gece'] is not None
    assert ok['acceptance']['accepted'] is True
    # The overshoot lands in the second hour only: per stall-hour errors are 0 and e/60 min.
    assert ok['mae_minutes']['near_mid'] == pytest.approx(170 / 60 / 2)
    failing = full_gold(error_seconds=400)
    assert failing['mae_minutes']['near_mid'] == pytest.approx(400 / 60 / 2)
    assert failing['acceptance']['accepted'] is False and 'eşiği' in failing['acceptance']['reason']
    assert full_gold(night=False)['acceptance']['accepted'] is False
    synthetic = full_gold(synthetic=True)
    assert synthetic['acceptance']['accepted'] is False and 'sentetik' in synthetic['acceptance']['reason']


def test_markdown_report_marks_synthetic():
    report = full_gold(synthetic=True) | dict(created_at=datetime(2026, 10, 10, tzinfo=timezone.utc).isoformat(),
        gold_sets=[dict(id='g', source_name='x.tsv', media_filename='m.mp4', synthetic=True, run_id='r',
                        origin='model', model_version='v', backbone='b')])
    text = markdown(report)
    assert 'SENTETİK ALTIN SET' in text and '❌ GEÇMEDİ' in text and 'Kare bazlı karışıklık matrisi' in text
