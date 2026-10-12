#!/usr/bin/env python3
"""Bounded decoded-pixel evidence, never full conversion-equivalence certification."""
import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from izge.video import FileVideoSource  # noqa: E402
from izge.vision.data_preparation import frame_at  # noqa: E402
from izge.vision.inspection import write_json  # noqa: E402
from izge.vision.preflight import checksum  # noqa: E402


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    results=[]
    for target in (0.,60.,300.):
        original=None
        for source in args.source:
            row={'source':str(source),'target_offset_s':target,'status':'unavailable'}
            try:
                frame=frame_at(FileVideoSource(source),target)
                pixels=np.asarray(frame.image.to_image().convert('RGB'))
                row.update(status='decoded',pts=frame.pts,time_base=str(frame.time_base),offset_s=frame.offset,
                           raster_sha256=hashlib.sha256(pixels.tobytes()).hexdigest())
                if original is None:
                    original=pixels
                elif original.shape==pixels.shape:
                    row['identical_pixels_at_sample']=bool(np.array_equal(original,pixels))
                    row['mean_absolute_rgb_difference_at_sample']=float(np.abs(original.astype(np.int16)-pixels).mean())
            except Exception:
                row['error_code']='seek_or_decode_unavailable'
            results.append(row)
    write_json(args.output,{'schema_version':'behavior-derivative-samples-1',
                            'full_equivalence_verified':False,'sample_results':results,
                            'files_sha256':{str(p):checksum(p) for p in args.source},
                            'notes':['Only sampled PTS evidence; matched pixels do not prove all-frame equivalence.',
                                     'Keep DAV/HEVC/H264 in one source family and split group.']})
    print('Üç PTS noktasında türev kanıtı kaydedildi; tam eşdeğerlik onayı verilmedi.')


if __name__=='__main__':
    main()
