"""Gold sets: `python -m izge.gold import|evaluate|list` (BORIS aggregated events, Decision 7)."""
import argparse
import json
import uuid
from pathlib import Path
from sqlalchemy import select
from . import schema as s
from .db import Session
from .feeding import boris
from .feeding.gold import import_gold, evaluation_report, write_report, gold_exclusions

REPORTS = Path(__file__).resolve().parents[2] / 'docs' / 'reports'


def main():
    parser = argparse.ArgumentParser(description='BORIS altın set içe aktarma ve değerlendirme')
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='BORIS aggregated events CSV/TSV içe aktar')
    imp.add_argument('--farm', required=True, type=uuid.UUID)
    imp.add_argument('--media', required=True, type=uuid.UUID, help='Etiketlenen kaydın medya kimliği')
    imp.add_argument('--file', required=True, type=Path)
    imp.add_argument('--mapping', type=Path, help='Varsayılan: config/boris.yaml')
    imp.add_argument('--synthetic', action='store_true', help='Sentetik/test altın seti (raporda kabul sayılmaz)')
    imp.add_argument('--note', default='')
    ev = sub.add_parser('evaluate', help='Tahminleri altın setlerle karşılaştır, rapor yaz')
    ev.add_argument('--farm', required=True, type=uuid.UUID)
    ev.add_argument('--gold-set', type=uuid.UUID, action='append')
    ev.add_argument('--out', type=Path, default=REPORTS)
    ev.add_argument('--threshold', type=float, default=3.0, help='Yakın/orta bölme-saat MAE eşiği (dk)')
    ls = sub.add_parser('list', help='Altın setleri ve eğitimden dışlanan günleri listele')
    ls.add_argument('--farm', required=True, type=uuid.UUID)
    args = parser.parse_args()
    if args.command == 'import':
        with Session.begin() as db:
            result = import_gold(db, args.farm, args.media, args.file.read_text(encoding='utf-8-sig'), args.file.name,
                                 boris.load_mapping(args.mapping), args.synthetic, args.note)
        print(json.dumps(result, default=str, ensure_ascii=False))
    elif args.command == 'evaluate':
        with Session() as db:
            report = evaluation_report(db, args.farm, args.gold_set, args.threshold)
        md, js = write_report(report, args.out)
        a = report['acceptance']
        print(f"Rapor: {md}\nJSON: {js}\nYakın/orta MAE: {a['mae_near_mid']} dk · kabul: {a['accepted']} ({a['reason']})")
    else:
        with Session() as db:
            rows = db.execute(select(s.gold_sets).where(s.gold_sets.c.farm_id == args.farm)).mappings().all()
            days, media = gold_exclusions(db, args.farm)
        for r in rows:
            print(f"{r['id']}  {r['source_name']}  medya={r['media_id']}  {'SENTETİK' if r['synthetic'] else ''}")
        print('Eğitimden dışlanan günler:', ', '.join(sorted(days)) or '—')


if __name__ == '__main__':
    main()
