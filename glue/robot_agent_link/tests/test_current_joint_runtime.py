"""Opt-in MuJoCo load test: does not run a motion or change source assets."""
import json
import os
from pathlib import Path
import pytest


def test_current_joint_state_is_simulator_initial_state(tmp_path):
    source = os.environ.get('CONTROL_STATE_TEST_SCENE')
    root = os.environ.get('CONTROL_STATE_TEST_ASSETS')
    if not source or not root:
        pytest.skip('requires installed Control and explicit local scene/assets')
    from execution.resources.resolver import SceneResourceCoordinator, component
    from execution.backends.mujoco.backend import MuJoCoExecutionBackend
    scene = json.loads(Path(source).read_text())
    arm = next(obj for obj in scene['objects'] if len(component(obj, 'RobotDriver').get('joint_sequence', [])) == 6
               and not component(obj, 'RobotAttachment') and component(obj, 'RobotDriver').get('role') != 'passive')
    driver = component(arm, 'RobotDriver')
    initial = dict(driver['initial_joint_positions_rad'])
    expected = {name: initial[name] + .015 for name in driver['joint_sequence']}
    driver['now_joint_positions_rad'] = expected
    passive_expected = {}
    for item in scene['objects']:
        passive = component(item, 'RobotDriver')
        if passive.get('role') == 'passive' and passive.get('joint_sequence'):
            values = {name: .02 for name in passive['joint_sequence']}
            passive['now_joint_positions_rad'] = values
            passive_expected[str(item['object_id_in_scene'])] = values
    path = tmp_path / 'scene.json'
    path.write_text(json.dumps(scene))
    task = {'schema_version': '2.0', 'request_id': 'restore-state',
            'scene_id': scene['scene_id'], 'scene_version': scene['scene_version'], 'commands': []}
    resources = SceneResourceCoordinator(scene_file=path, asset_root=root).resolve(task)
    assert list(resources.initial_joints) == pytest.approx(list(expected.values()))
    backend = MuJoCoExecutionBackend()
    backend.load(resources, tmp_path / 'mujoco')
    state = backend.get_state()
    assert state['joints'] == pytest.approx(list(expected.values()))
    assert state['scene_driver_joints'][str(arm['object_id_in_scene'])] == pytest.approx(expected)
    assert driver['initial_joint_positions_rad'] == initial
    for identifier, values in passive_expected.items():
        assert state['scene_driver_joints'][identifier] == pytest.approx(values)
    # Validate actual compiled primitive sizes, not just acceptance of scale.
    import xml.etree.ElementTree as ET
    compiled = list((tmp_path / 'mujoco').rglob('*.xml'))
    for obj in resources.scene['objects']:
        collider = component(obj, 'Collider')
        scale = component(obj, 'Transform').get('scale', [1, 1, 1])
        if collider.get('shape') not in {'box', 'cylinder'} or scale == [1, 1, 1]:
            continue
        name = obj['object_name_in_scene'] + '_collision'
        geoms = [g for file in compiled for g in ET.parse(file).iter('geom') if g.get('name') == name]
        assert geoms, f'missing scaled collision geometry: {name}'
        size = collider['size']
        expected_size = ([size[i] * scale[i] / 2 for i in range(3)]
                         if collider['shape'] == 'box' else [size[0] * scale[0], size[2] * scale[2] / 2])
        for geom in geoms:
            assert list(map(float, geom.get('size').split())) == pytest.approx(expected_size)
