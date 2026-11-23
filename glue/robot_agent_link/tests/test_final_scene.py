from copy import deepcopy
import json

import pytest
from link.final_scene import validated_snapshot


def scene():
    return {'scene_id': 3, 'scene_version': 2, 'objects': [{
        'object_id_in_scene': 2, 'object_name_in_scene': 'arm', 'components': [
            {'component_type': 'Transform', 'properties': {'parent': None,
                'position': [0, 0, 0], 'quaternion_xyzw': [0, 0, 0, 1]}},
            {'component_type': 'RobotDriver', 'properties': {'joint_sequence': ['j'],
                'initial_joint_positions_rad': {'j': 0}}}]}]}


def test_snapshot_keeps_initial_and_accepts_measured_current(tmp_path):
    before = scene()
    after = deepcopy(before)
    after['objects'][0]['components'][1]['properties']['now_joint_positions_rad'] = {'j': .5}
    path = tmp_path / 'final_scene.json'
    path.write_text(json.dumps(after))
    assert validated_snapshot(path, before, {2: {'j': .5}}) == after
    after['objects'][0]['components'][1]['properties']['initial_joint_positions_rad'] = {'j': .5}
    path.write_text(json.dumps(after))
    with pytest.raises(ValueError, match='immutable_fields'):
        validated_snapshot(path, before, {2: {'j': .5}})


@pytest.mark.parametrize('height', [.5, -105.236])
def test_bridge_does_not_judge_simulated_position(tmp_path, height):
    before = scene()
    after = deepcopy(before)
    after['objects'][0]['components'][0]['properties']['position'][2] = height
    after['objects'][0]['components'][1]['properties']['now_joint_positions_rad'] = {'j': 0}
    path = tmp_path / 'final_scene.json'
    path.write_text(json.dumps(after))
    assert validated_snapshot(path, before, {2: {'j': 0}}) == after


def test_unchanged_attachment_transform_can_omit_pose(tmp_path):
    before = scene()
    before['objects'][0]['components'][0]['properties'] = {'scale': [1, 1, 1], 'parent': 1}
    before['objects'][0]['components'].append({
        'component_type': 'RobotAttachment', 'properties': {'parent_object_id': 1}})
    after = deepcopy(before)
    after['objects'][0]['components'][1]['properties']['now_joint_positions_rad'] = {'j': .5}
    path = tmp_path / 'final_scene.json'
    path.write_text(json.dumps(after))
    assert validated_snapshot(path, before, {2: {'j': .5}}) == after


@pytest.mark.parametrize('field', ['position', 'quaternion_xyzw'])
def test_present_pose_cannot_disappear_in_final_snapshot(tmp_path, field):
    before = scene()
    after = deepcopy(before)
    del after['objects'][0]['components'][0]['properties'][field]
    path = tmp_path / 'final_scene.json'
    path.write_text(json.dumps(after))
    with pytest.raises(ValueError, match='control_final_scene_pose_invalid'):
        validated_snapshot(path, before, {})


def test_rounded_unit_quaternion_is_normalized(tmp_path):
    before = scene()
    after = deepcopy(before)
    after['objects'][0]['components'][0]['properties']['quaternion_xyzw'] = [0, 0, .7071, .7071]
    path = tmp_path / 'final_scene.json'
    path.write_text(json.dumps(after))
    after['objects'][0]['components'][1]['properties']['now_joint_positions_rad'] = {'j': 0}
    path.write_text(json.dumps(after))
    result = validated_snapshot(path, before, {2: {'j': 0}})
    quat = result['objects'][0]['components'][0]['properties']['quaternion_xyzw']
    assert sum(v * v for v in quat) == pytest.approx(1.0)


def test_materially_non_unit_quaternion_is_rejected(tmp_path):
    before = scene()
    after = deepcopy(before)
    after['objects'][0]['components'][0]['properties']['quaternion_xyzw'] = [0, 0, .5, .5]
    path = tmp_path / 'final_scene.json'
    path.write_text(json.dumps(after))
    with pytest.raises(ValueError, match='control_final_scene_quaternion_invalid'):
        validated_snapshot(path, before, {})
