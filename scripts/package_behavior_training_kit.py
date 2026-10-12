#!/usr/bin/env python3
"""Package code/locks only; never include raw videos, labels, weights or credentials."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    paths=sorted((root/'backend/izge').rglob('*.py'))
    paths += [r for r in (root/'experiments/rfdetr_training').iterdir() if r.is_file() and r.suffix!='.ipynb']
    paths += [root/'experiments/rfdetr_seg/model.lock.json',root/'scripts/fetch_behavior_model.py',
              root/'scripts/export_behavior_annotations.py']
    paths += [root/'docs'/name for name in ('ANNOTATION_WORKFLOW.md', 'TRAINING_COMPUTE_PLAN.md',
                                          'THIRD_PARTY_LICENSES.md')]
    checksums={str(r.relative_to(root)):hashlib.sha256(r.read_bytes()).hexdigest() for r in paths}
    with args.output.open('xb') as target,zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            z.write(path,str(path.relative_to(root)))
        z.writestr('kit_manifest.json',json.dumps({'schema_version':'izge-training-code-kit-1',
                                                  'private_video_included':False,'weights_included':False,
                                                  'human_dataset_included':False,'files_sha256':checksums},indent=2))
    digest=hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f'{args.output}: SHA256 {digest}; {args.output.stat().st_size} bayt; yalnız kod/kilitler')


if __name__=='__main__':
    main()
