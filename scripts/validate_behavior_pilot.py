#!/usr/bin/env python3
"""Validate generated pilot contents, including PTS-mapped context clips; no model."""
import argparse
import json
import sys
from pathlib import Path

import av
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from izge.vision.preflight import checksum  # noqa: E402


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True)
    p.add_argument('--source-dir',type=Path,required=True)
    args=p.parse_args()
    manifest=json.loads((args.package/'manifest.json').read_text())
    labels=json.loads((args.package/'annotations.template.json').read_text())
    assert 100 <= len(manifest['images']) <= 200 and manifest['human_annotations']==0
    assert not manifest['roi_verified'] and not manifest['roi_analysis_allowed']
    assert all(r['status']=='unlabeled' and r['origin'] is None and r['annotator'] is None
               and r['annotated_at'] is None and r['objects']==[] for r in labels['frames'])
    for source in manifest['sources']:
        assert checksum(args.source_dir/source['path'])==source['sha256']
        assert source['split']=='unassigned' and source['recording_day'] is None
    for row in manifest['images']:
        path=args.package/row['file']
        assert checksum(path)==row['sha256']
        with Image.open(path) as im:
            assert im.size==(row['width'],row['height']) and im.format=='PNG'
            im.verify()
    for row in manifest['roi_reviews']:
        assert row['geometry_verification'] is None and not row['roi_verified']
        with Image.open(args.package/row['roi_preview']) as image:
            image.verify()
    for clip in manifest['clips']:
        folder=args.package/'clips'
        assert checksum(folder/clip['file'])==clip['sha256'] and not clip['behavior_event_verified']
        rows=[json.loads(s) for s in (folder/clip['pts_sidecar']).read_text().splitlines()]
        assert len(rows)==clip['frames']
        with av.open(str(folder/clip['file'])) as container:
            decoded=container.decode(video=0)
            previous=None
            count=0
            for frame,row in zip(decoded,rows,strict=True):
                offset=float(frame.pts*frame.time_base)
                assert abs(offset-row['output_pts']/1000) <= .001
                assert previous is None or offset>previous
                previous,count=offset,count+1
            assert count==clip['frames']
    print(f'Geçti: {len(manifest["images"])} ham PNG, {len(manifest["roi_reviews"])} taslak, '
          f'{len(manifest["clips"])} PTS sidecar/FFV1 klip; insan etiketleri boş ve kaynaklar değişmedi.')


if __name__=='__main__':
    main()
