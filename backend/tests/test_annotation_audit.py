from izge.vision.annotation_audit import audit_coco, audit_file


def coco(**over):
    data = {'images': [{'id': 1, 'file_name': 'a.png', 'width': 100, 'height': 100, 'group_id': 'g', 'split': 'train'}],
            'categories': [{'id': 1, 'name': 'cow'}],
            'annotations': [{'id': 1, 'image_id': 1, 'category_id': 1, 'bbox': [10, 10, 30, 30], 'origin': 'manual'}]}
    data.update(over)
    return data


def codes(report):
    return set(report['problem_counts'])


def test_clean_dataset_ok_and_origin_counted():
    report = audit_coco(coco())
    assert report['ok'] and report['origins'] == {'manual': 1} and report['per_class'] == {'cow': 1}
    assert report['box_area_fraction']['median'] == 0.09


def test_defects_are_all_reported_in_one_pass():
    anns = [
        {'id': 1, 'image_id': 1, 'category_id': 1, 'bbox': [10, 10, 30, 30], 'origin': 'model', 'score': 1.5},
        {'id': 2, 'image_id': 1, 'category_id': 1, 'bbox': [10.5, 10.5, 30, 30], 'origin': 'model'},   # duplicate
        {'id': 3, 'image_id': 1, 'category_id': 1, 'bbox': [90, 90, 30, 30]},                      # out + no origin
        {'id': 4, 'image_id': 1, 'category_id': 1, 'bbox': [0, 0, 0, 5], 'origin': 'manual'},      # degenerate
        {'id': 5, 'image_id': 1, 'category_id': 1, 'bbox': [50, 50, 3, 3], 'origin': 'manual'},    # tiny
        {'id': 6, 'image_id': 9, 'category_id': 1, 'bbox': [1, 1, 5, 5], 'origin': 'manual'},
        {'id': 7, 'image_id': 1, 'category_id': 5, 'bbox': [1, 1, 20, 20], 'origin': 'bogus'},
    ]
    report = audit_coco(coco(annotations=anns))
    assert not report['ok']
    assert codes(report) >= {'duplicate_box', 'bbox_out_of_image', 'origin_missing_or_invalid', 'bbox_degenerate',
                             'bbox_tiny', 'annotation_unknown_image', 'annotation_unknown_category',
                             'score_out_of_range'}


def test_group_leakage_and_missing_files(tmp_path):
    images = [{'id': 1, 'file_name': 'a.png', 'width': 10, 'height': 10, 'group_id': 'g', 'split': 'train'},
              {'id': 2, 'file_name': 'b.png', 'width': 10, 'height': 10, 'group_id': 'g', 'split': 'test'}]
    (tmp_path / 'a.png').write_bytes(b'x')
    report = audit_coco(coco(images=images, annotations=[]), image_root=tmp_path)
    assert codes(report) == {'group_leaks_across_splits', 'image_file_missing'}
    assert report['images_without_boxes'] == 2


def test_audit_file_reads_json(tmp_path):
    import json
    path = tmp_path / 'c.json'
    path.write_text(json.dumps(coco()))
    assert audit_file(path)['ok']
