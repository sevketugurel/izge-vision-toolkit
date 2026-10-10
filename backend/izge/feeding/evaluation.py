"""Compare 1 Hz predictions with gold sets (Decision 7). Pure computation and report rendering.

Intervals are (stall, state, start, end, confidence) in absolute epoch seconds, so several gold
sets and recordings combine. Only seconds covered by a gold event are compared. Second s is
[s, s+1); its state is the interval covering s + 0.5. Stall-hours use local clock hours.
"""
import math
from collections import defaultdict
from datetime import datetime, timezone as tz
from typing import Callable
from zoneinfo import ZoneInfo
from .states import STATES, FEEDING, NOT_VISIBLE

NO_PREDICTION = 'tahmin_yok'
NEAR_MID = ('yakin', 'orta')
REQUIRED_STALLS, REQUIRED_SECONDS = 6, 2 * 3600


def second_states(intervals) -> dict[str, dict[int, tuple[str, float | None]]]:
    out: dict[str, dict[int, tuple[str, float | None]]] = defaultdict(dict)
    for stall, state, start, end, confidence in intervals:
        s = math.ceil(start - .5)
        while s + .5 < end:
            out[stall][s] = (state, confidence)
            s += 1
    return out


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def evaluate(gold, predicted, timezone: str, mesafe: dict[str, str],
             is_daytime: Callable[[datetime], bool], threshold_minutes: float = 3.0, synthetic: bool = False) -> dict:
    zone = ZoneInfo(timezone)
    truth = second_states((g[0], g[1], g[2], g[3], None) for g in gold)
    pred = second_states(predicted)
    hours: dict[tuple, dict] = {}
    confusion = {t: {p: 0 for p in (*STATES, NO_PREDICTION)} for t in STATES}
    per_mesafe = defaultdict(lambda: dict(seconds=0, pred_not_visible=0, truth_not_visible=0, predicted=0, conf=[]))
    night_seconds = 0
    labeled = defaultdict(int)
    for stall, seconds in truth.items():
        for s, (t_state, _) in seconds.items():
            p_state, conf = pred.get(stall, {}).get(s, (NO_PREDICTION, None))
            confusion[t_state][p_state] += 1
            local = datetime.fromtimestamp(s, tz.utc).astimezone(zone)
            hour = local.replace(minute=0, second=0, microsecond=0)
            daytime = is_daytime(hour)
            night_seconds += not is_daytime(local)
            labeled[stall] += 1
            h = hours.setdefault((stall, hour), dict(stall=stall, hour=hour.isoformat(), mesafe=mesafe.get(stall),
                daynight='gunduz' if daytime else 'gece', labeled_seconds=0, truth_feeding=0, pred_feeding=0))
            h['labeled_seconds'] += 1
            h['truth_feeding'] += t_state == FEEDING
            h['pred_feeding'] += p_state == FEEDING
            m = per_mesafe[mesafe.get(stall) or '?']
            m['seconds'] += 1
            m['truth_not_visible'] += t_state == NOT_VISIBLE
            m['pred_not_visible'] += p_state == NOT_VISIBLE
            m['predicted'] += p_state != NO_PREDICTION
            if conf is not None:
                m['conf'].append(conf)
    rows = []
    for h in sorted(hours.values(), key=lambda r: (r['stall'], r['hour'])):
        h['truth_minutes'] = h.pop('truth_feeding') / 60
        h['pred_minutes'] = h.pop('pred_feeding') / 60
        h['error_minutes'] = h['pred_minutes'] - h['truth_minutes']
        rows.append(h)

    def mae(selected):
        return _mean(abs(r['error_minutes']) for r in selected)
    mae_near_mid = mae(r for r in rows if r['mesafe'] in NEAR_MID)
    requirements = dict(
        stalls=len([s for s, n in labeled.items() if n >= REQUIRED_SECONDS]),
        stalls_required=REQUIRED_STALLS, seconds_per_stall_required=REQUIRED_SECONDS,
        night_window=night_seconds > 0)
    requirements['met'] = requirements['stalls'] >= REQUIRED_STALLS and requirements['night_window']
    accepted = bool(requirements['met'] and mae_near_mid is not None and mae_near_mid <= threshold_minutes
                    and not synthetic)
    return dict(
        compared_seconds=sum(labeled.values()),
        stall_hours=rows,
        mae_minutes=dict(total=mae(rows), near_mid=mae_near_mid,
                         by_mesafe={k: mae(r for r in rows if r['mesafe'] == k) for k in ('yakin', 'orta', 'uzak')},
                         by_daynight={k: mae(r for r in rows if r['daynight'] == k) for k in ('gunduz', 'gece')}),
        confusion=confusion,
        frame_accuracy=sum(confusion[s][s] for s in STATES) / max(1, sum(labeled.values())),
        by_mesafe={k: dict(seconds=v['seconds'], pred_not_visible_rate=v['pred_not_visible'] / v['seconds'],
                           truth_not_visible_rate=v['truth_not_visible'] / v['seconds'],
                           prediction_coverage=v['predicted'] / v['seconds'], mean_confidence=_mean(v['conf']))
                   for k, v in sorted(per_mesafe.items())},
        requirements=requirements,
        acceptance=dict(threshold_minutes=threshold_minutes, mae_near_mid=mae_near_mid, accepted=accepted,
                        synthetic=synthetic,
                        reason=('sentetik altın set: kabul kanıtı değildir' if synthetic else
                                'altın set gereksinimleri karşılanmadı (6 bölme × 2 sa, en az bir gece penceresi)'
                                if not requirements['met'] else
                                'yakın/orta bölme-saat MAE eşiği aşıldı' if not accepted else 'kabul ölçütü sağlandı')),
    )


def fmt(v, digits=2, suffix=''):
    return '—' if v is None else f'{v:.{digits}f}{suffix}'


def markdown(report: dict) -> str:
    a, m = report['acceptance'], report['mae_minutes']
    lines = [f"# Değerlendirme raporu — {report['created_at'][:10]}", '']
    if a['synthetic']:
        lines += ['> **SENTETİK ALTIN SET.** Bu rapor yalnız hattın çalıştığını gösterir; doğruluk veya v1 kabul kanıtı değildir.', '']
    lines += [f"**Kabul bayrağı (yakın/orta bölme-saat MAE ≤ {a['threshold_minutes']:g} dk):** "
              f"{'✅ GEÇTİ' if a['accepted'] else '❌ GEÇMEDİ'} — {a['reason']}", '',
              '## Kaynak', '', '| Alan | Değer |', '|---|---|']
    for gs in report['gold_sets']:
        lines.append(f"| Altın set | {gs['source_name']} ({gs['id']}), medya {gs['media_filename']}, "
                     f"{'sentetik' if gs['synthetic'] else 'gerçek'} |")
        lines.append(f"| Tahmin | run {gs['run_id']}, köken {gs['origin']}, model {gs['model_version'] or '—'}, "
                     f"backbone {gs['backbone'] or '—'} |")
    r = report['requirements']
    lines += [f"| Karşılaştırılan saniye | {report['compared_seconds']} |",
              f"| Gereksinim | ≥ 2 sa etiketli bölme: {r['stalls']}/{r['stalls_required']}; gece penceresi: "
              f"{'var' if r['night_window'] else 'yok'} |", '',
              '## Bölme-saat yemde süre hatası (MAE, dk)', '', '| Kapsam | MAE |', '|---|---:|',
              f"| Toplam | {fmt(m['total'])} |", f"| Yakın + orta | {fmt(m['near_mid'])} |"]
    lines += [f"| {k} | {fmt(v)} |" for k, v in m['by_mesafe'].items()]
    lines += [f"| {'Gündüz' if k == 'gunduz' else 'Gece'} | {fmt(v)} |" for k, v in m['by_daynight'].items()]
    lines += ['', '## Kare bazlı karışıklık matrisi (1 Hz; satır = altın set, sütun = tahmin)', '',
              '| | ' + ' | '.join((*STATES, NO_PREDICTION)) + ' |', '|---|' + '---:|' * (len(STATES) + 1)]
    for t, row in report['confusion'].items():
        lines.append(f'| {t} | ' + ' | '.join(str(v) for v in row.values()) + ' |')
    lines += ['', f"Kare doğruluğu: {fmt(report['frame_accuracy'], 3)}", '',
              '## Mesafe sınıfına göre görünmüyor oranı ve güven', '',
              '| Mesafe | Saniye | Tahmin görünmüyor | Altın set görünmüyor | Tahmin kapsamı | Ort. güven |',
              '|---|---:|---:|---:|---:|---:|']
    for k, v in report['by_mesafe'].items():
        lines.append(f"| {k} | {v['seconds']} | {fmt(100 * v['pred_not_visible_rate'], 1, '%')} | "
                     f"{fmt(100 * v['truth_not_visible_rate'], 1, '%')} | {fmt(100 * v['prediction_coverage'], 1, '%')} | "
                     f"{fmt(v['mean_confidence'], 3)} |")
    lines += ['', 'Uzak bölmelerde eşik aranmaz; güven ve görünmüyor oranı bilgi amaçlıdır.', '',
              '## Bölme-saat ayrıntısı', '',
              '| Bölme | Saat | Mesafe | Gündüz/gece | Etiketli dk | Altın set yemde dk | Tahmin yemde dk | Hata dk |',
              '|---|---|---|---|---:|---:|---:|---:|']
    for row in report['stall_hours']:
        lines.append(f"| {row['stall']} | {row['hour'][:16]} | {row['mesafe']} | {row['daynight']} | "
                     f"{row['labeled_seconds'] / 60:.1f} | {row['truth_minutes']:.2f} | {row['pred_minutes']:.2f} | "
                     f"{row['error_minutes']:+.2f} |")
    return '\n'.join(lines) + '\n'
