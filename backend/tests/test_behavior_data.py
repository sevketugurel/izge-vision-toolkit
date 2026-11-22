import ast
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

import test_behavior_preflight as fixtures
from izge.domain import Frame, SourceGap
from izge.video import FileVideoSource
from izge.vision.annotation_data import (export_coco, leakage_components, safe_file,
                                        validate_object, validate_splits, validate_training_dataset)
from izge.vision.data_inventory import inventory_sources, source_family
from izge.vision.data_preparation import context_clip, frame_at, pilot_targets
from izge.vision.config import load
from izge.vision.detector import Detection, DetectorUnavailable
from izge.vision.preflight import checksum
from izge.vision.roi_review import render_draft
from izge.vision.video_experiment import run_video_experiment

video, config = fixtures.video, fixtures.config
ROOT = Path(__file__).resolve().parents[2]


def sources():
    return [{'id': str(i), 'sha256': str(i), 'family_id': f'f{i}', 'session_id': f's{i}',
             'recording_day': f'2026-10-0{i}', 'recording_day_verified': True,
             'recording_day_verification': {'verified_by':'unit-fixture','evidence':'synthetic software fixture',
                                            'verified_at':'2026-10-12T10:00:00+03:00'},
             'reserved_test_candidate': i == 3} for i in (1,2,3)]


def test_derivatives_and_unknown_recording_days_are_conservatively_grouped():
    records = sources()
    for row in records:
        row['recording_day_verified'] = False
    assert len(set(leakage_components(records).values())) == 1
    synthetic_stamp = '20990102030405'
    encoded = Path('h264') / f'{synthetic_stamp}.mp4'
    original = Path(f'{synthetic_stamp}.dav')
    assert source_family(encoded, 'different') == source_family(original, 'original')


@pytest.mark.parametrize('key', ['family_id', 'session_id', 'sha256', 'recording_day', 'overlap_groups'])
def test_source_day_and_transitive_overlap_leakage_is_rejected(key):
    records = sources()
    if key == 'overlap_groups':
        records[0]['overlap_groups'] = ['a']
        records[1]['overlap_groups'] = ['a','b']
        records[2]['overlap_groups'] = ['b']
    else:
        records[1][key] = records[0][key]
    with pytest.raises(ValueError, match='leaks'):
        validate_splits(records, {'1':'train','2':'valid','3':'test'})


def test_day_verification_and_camera_holdout_are_not_bypassed():
    records = sources()
    records[0]['recording_day_verified'] = False
    with pytest.raises(ValueError, match='human verification'):
        validate_splits(records, {'1':'train','2':'valid','3':'test'})
    with pytest.raises(ValueError, match='Locked'):
        validate_splits(sources(), {'1':'test','2':'valid','3':'train'})


def test_midnight_recording_day_overlap_stays_in_one_split():
    records=sources()
    records[0]['recording_days']=['2026-10-01','2026-10-02']
    with pytest.raises(ValueError,match='leaks'):
        validate_splits(records,{'1':'train','2':'valid','3':'test'})


def test_scaled_draft_pixels_are_rejected(config):
    with pytest.raises(ValueError,match='referans piksel'):
        render_draft(Image.new('RGB',(320,180)),load(config),0,0.)


def test_image_only_dataset_import_does_not_pull_video_or_model_libraries():
    result=subprocess.run([sys.executable,'-c',
        'import sys; from izge.vision import annotation_data; '
        'assert not any(n in sys.modules for n in ("av","torch","rfdetr","izge.video"))'],
        env={'PYTHONPATH':str(ROOT/'backend')},capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_sampler_and_seek_use_vfr_pts_and_keep_budget(video):
    assert len(pilot_targets(1800,36)) == 36 and 60 in pilot_targets(1800,36)
    frame = frame_at(FileVideoSource(video), .1)
    assert frame.offset == .16 and frame.pts*frame.time_base == Fraction(16,100)


def test_inventory_preserves_originals_and_distinguishes_first_frame_readability(video, tmp_path):
    root = tmp_path/'samples'
    root.mkdir()
    (root/'synthetic-video.MP4').write_bytes(video.read_bytes())
    (root/'copy.mp4').write_bytes(video.read_bytes())
    (root/'invalid.dav').write_bytes(b'not video')
    result = inventory_sources(root,tmp_path/'inventory')
    records = result['sources']
    assert len({r['id'] for r in records}) == 3
    assert all(r['unchanged'] and r['recorded_at'] is None for r in records)
    assert sorted(r['readability'] for r in records) == ['metadata_and_first_frame_only'] * 2 + ['unavailable']


def test_context_clip_sidecar_preserves_actual_source_pts_not_constant_fps(video, tmp_path):
    out = tmp_path/'context.mkv'
    info = context_clip(FileVideoSource(video),0.,.35,out)
    rows = [json.loads(s) for s in out.with_suffix('.pts.jsonl').read_text().splitlines()]
    assert [r['source_offset_s'] for r in rows] == [0.,.04,.16,.23,.3]
    with av.open(str(out)) as container:
        frames = list(container.decode(video=0))
    assert [round(float(f.pts*f.time_base),2) for f in frames] == [0.,.04,.16,.23,.3]
    assert info['frames'] == 5 and not info['behavior_event_verified']


@pytest.fixture
def package(tmp_path):
    root = tmp_path/'pilot'
    (root/'images').mkdir(parents=True)
    images, labels = [], []
    for i in (1,2,3):
        path = root/'images'/f'{i}.png'
        Image.new('RGB',(32,24),(i*50,30,80)).save(path)
        images.append({'id':str(i),'source_id':str(i),'file':f'images/{i}.png','sha256':checksum(path),
                       'width':32,'height':24,'pts':i*1000,'time_base':'1/1000','offset_s':float(i)})
        labels.append({'image_id':str(i),'status':'reviewed','origin':'manual','annotator':'unit-fixture',
                       'annotated_at':'2026-10-12T10:00:00+03:00','objects':[{'class_name':'cow',
                       'bbox_xyxy':[4,4,16,14],'segmentation':[[4,4,16,4,16,14,4,14]]}]})
    (root/'manifest.json').write_text(json.dumps({'sources':sources(),'images':images}))
    ann = tmp_path/'annotations.json'
    ann.write_text(json.dumps({'frames':labels}))
    split = tmp_path/'splits.json'
    split.write_text(json.dumps({'sources':sources(),'assignments':{'1':'train','2':'valid','3':'test'}}))
    return root,ann,split


def test_human_coco_export_has_separate_category_map_and_integrity_gate(package, tmp_path):
    root,ann,split = package
    out = tmp_path/'coco'
    proof = export_coco(root,ann,split,out)
    assert proof['class_mapping'] == {'coco_category_id':1,'rfdetr_roboflow_contiguous_label':0,'pretrained_coco_cow_id':21}
    assert validate_training_dataset(out)['train'] == {'images':1,'instances':1}
    (out/'train/1.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):
        validate_training_dataset(out)


@pytest.mark.parametrize('field,value', [('reserved_test_candidate', False), ('camera_id', 'other-camera'),
                                        ('path', 'other-source.mp4'), ('camera_position_revision', 'other-position')])
def test_export_source_review_and_camera_test_lock_are_immutable(package, tmp_path, field, value):
    root, ann, split = package
    data = json.loads(split.read_text())
    data['sources'][2][field] = value
    split.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Source grouping/provenance'):
        export_coco(root, ann, split, tmp_path/'invalid-split')
    assert not (tmp_path/'invalid-split').exists()


@pytest.mark.parametrize('field,value,message', [('group_id', 'unrelated-group', 'source/group'),
                                                ('sha256', 'unrelated-sha', 'checksum differs')])
def test_training_validator_checks_coco_group_against_source_manifest(package, tmp_path, field, value, message):
    root, ann, split = package
    out = tmp_path/'coco'
    export_coco(root, ann, split, out)
    path = out/'train/_annotations.coco.json'
    data = json.loads(path.read_text())
    data['images'][0][field] = value
    path.write_text(json.dumps(data))
    proof = json.loads((out/'dataset_manifest.json').read_text())
    proof['files_sha256']['train/_annotations.coco.json'] = checksum(path)
    (out/'dataset_manifest.json').write_text(json.dumps(proof))
    with pytest.raises(ValueError, match=message):
        validate_training_dataset(out)


@pytest.mark.parametrize('change', ['proposal', 'unlabeled', 'no_annotator', 'naive_time', 'missing_mask'])
def test_export_rejects_nonhuman_and_maskless_segmentation(package, tmp_path, change):
    root,ann,split = package
    labels = json.loads(ann.read_text())
    row = labels['frames'][0]
    if change == 'proposal':
        row['origin'] = 'model_proposal'
    elif change == 'unlabeled':
        row['status'] = 'unlabeled'
    elif change == 'no_annotator':
        row['annotator'] = None
    elif change == 'naive_time':
        row['annotated_at'] = '2026-10-12T10:00:00'
    else:
        row['objects'][0].pop('segmentation')
    ann.write_text(json.dumps(labels))
    with pytest.raises(ValueError):
        export_coco(root,ann,split,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_detection_export_is_not_segmentation_ready_and_manifest_cannot_omit_files(package, tmp_path):
    root,ann,split = package
    out = tmp_path/'boxes'
    export_coco(root,ann,split,out,segmentation=False)
    with pytest.raises(ValueError,match='matching training task'):
        validate_training_dataset(out,'segmentation')
    proof = json.loads((out/'dataset_manifest.json').read_text())
    proof['files_sha256'].pop('train/1.png')
    (out/'dataset_manifest.json').write_text(json.dumps(proof))
    with pytest.raises(ValueError,match='every COCO'):
        validate_training_dataset(out,'detection')


def test_instance_mask_rle_and_path_validation(tmp_path):
    mask = np.zeros((24,32),np.uint8)
    mask[4:14,4:16] = 255
    Image.fromarray(mask).save(tmp_path/'mask.png')
    result = validate_object({'class_name':'cow','bbox_xyxy':[4,4,16,14],'mask_file':'mask.png'},32,24,True,tmp_path)
    assert sum(result['segmentation']['counts']) == 24*32 and result['area'] == 120
    with pytest.raises(ValueError,match='outside package'):
        safe_file(tmp_path,'../outside.png')


class UnitDetector:
    def __init__(self,*args):
        self.provenance,self.device,self.capabilities = {'model_class':'unit_fixture'},'cpu',{'cpu':True}
        self.load_seconds,self.fallback_reason,self.inference_seconds = 0.,None,[]
    def detect(self, frame):
        self.inference_seconds.append(.01)
        mask = np.zeros((frame.height,frame.width),bool)
        mask[2:10,3:15] = True
        return [Detection((3,2,15,10),21,'cow',.8,mask)]


def test_sparse_raw_proposals_are_not_human_labels(video, config, tmp_path):
    result = run_video_experiment(video,config,Path('unused'),tmp_path/'run',targets=[0.,.16,.3,.4],
                                  maximum=4,detector_factory=UnitDetector)
    assert result['status'] == 'success' and not result['full_file_processed'] and not result['roi_analysis_allowed']
    assert [r['offset_s'] for r in result['frames']] == [0.,.16,.3,.4]
    rows = [json.loads(s) for s in (tmp_path/'run/detections.jsonl').read_text().splitlines()]
    assert all(r['origin']=='model_proposal' and not r['human_reviewed'] for r in rows)
    assert len(list((tmp_path/'run').glob('mask*.png'))) == 4


def test_streaming_inference_budget_preserves_window_and_omitted_masks(video, config, tmp_path, monkeypatch):
    class WindowReader:
        def __init__(self,path):
            self.path = path
        def metadata(self):
            return {**FileVideoSource(video).metadata(),'video_stream_duration_s':400.}
        def read_events(self,start=0):
            for second in range(60,241):
                yield Frame(second*1000,Fraction(1,1000),float(second),160,90,
                            av.VideoFrame.from_image(Image.new('RGB',(160,90))))
    monkeypatch.setattr('izge.vision.video_experiment.FileVideoSource',WindowReader)
    result = run_video_experiment(video,config,Path('unused'),tmp_path/'window',period=20,maximum=4,
                                  detector_factory=UnitDetector)
    assert result['status']=='success' and result['decoded_frames_in_window']==180
    assert result['last_decoded_offset_s']==239 and len(result['frames'])==4
    rows = [json.loads(s) for s in (tmp_path/'window/detections.jsonl').read_text().splitlines()]
    assert rows[1]['detections'][0]['mask']['file'] is None
    assert not rows[1]['detections'][0]['mask']['saved']


def test_source_error_and_model_failure_are_not_empty_success(video, config, tmp_path):
    def unavailable(*args):
        raise DetectorUnavailable('cuda_unavailable')
    result = run_video_experiment(video,config,Path('unused'),tmp_path/'fail',targets=[0.],
                                  detector_factory=unavailable)
    assert result['status']=='failed' and result['frames']==[] and not result['model_loaded']
    assert not (tmp_path/'fail/detections.jsonl').exists()


@pytest.mark.parametrize('kwargs',[{'maximum':33},{'duration':1800},{'period':float('nan')},{'targets':[0.,float('inf')]}])
def test_uncontrolled_processing_budget_is_rejected(tmp_path,kwargs):
    with pytest.raises(ValueError):
        run_video_experiment(Path('missing'),Path('missing'),Path('missing'),tmp_path/'out',**kwargs)
    assert not (tmp_path/'out').exists()


def test_training_entrypoint_refuses_without_human_dataset_and_explicit_flag():
    run = subprocess.run([sys.executable,str(ROOT/'experiments/rfdetr_training/train.py')],capture_output=True,text=True)
    assert run.returncode == 2 and 'Training disabled' in run.stderr


def test_colab_notebook_is_unexecuted_and_training_disabled_by_default():
    notebook=json.loads((ROOT/'experiments/rfdetr_training/colab_seg_small.ipynb').read_text())
    assert notebook['nbformat']==4 and notebook['metadata']['izge_validation']['training_executed'] is False
    found={}
    for cell in notebook['cells']:
        if cell['cell_type']!='code':
            continue
        assert cell['execution_count'] is None and cell['outputs']==[]
        tree=ast.parse(''.join(cell['source']))
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant):
                for target in node.targets:
                    if isinstance(target,ast.Name):
                        found[target.id]=node.value.value
    assert found['RUN_SMOKE'] is False and found['RUN_TRAINING'] is False
    assert found['RELEASE_LOCKED_TEST'] is False and found['MOUNT_DRIVE'] is False
    ast.parse(found['gpu_probe'])


def test_source_gap_keeps_partial_window_instead_of_claiming_complete(video, config, tmp_path, monkeypatch):
    class PartialReader:
        def __init__(self,path):
            self.path=path
        def metadata(self):
            return FileVideoSource(video).metadata()
        def read_events(self,start=0):
            yield Frame(60000,Fraction(1,1000),60.,160,90,
                        av.VideoFrame.from_image(Image.new('RGB',(160,90))))
            yield SourceGap('decode_error',before_pts=60000,start_offset=60.)
    monkeypatch.setattr('izge.vision.video_experiment.FileVideoSource',PartialReader)
    result=run_video_experiment(video,config,Path('unused'),tmp_path/'partial',detector_factory=UnitDetector)
    assert result['status']=='partial' and result['last_decoded_pts']==60000
    assert result['source_gap_counts']=={'decode_error':1} and len(result['frames'])==1
    assert not result.get('window_end_boundary_reached',False)
