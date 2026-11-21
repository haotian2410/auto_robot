from link.joint_feedback import measured_joints


def obj(identifier, name, sequence):
    return {'object_id_in_scene': identifier, 'object_name_in_scene': name,
            'components': [{'component_type': 'RobotDriver', 'properties': {'joint_sequence': sequence}}]}


def test_two_drawers_with_identical_joint_names_are_scoped():
    document = {'objects': [obj(1, 'A', ['drawer']), obj(2, 'B', ['drawer'])]}
    final = {'mechanisms': {'A__drawer': {'joint_name': 'A__drawer', 'joint_position': .12},
                            'B__drawer': {'joint_name': 'B__drawer', 'joint_position': .02}}}
    assert measured_joints(final, document)[0] == {1: {'drawer': .12}, 2: {'drawer': .02}}


def test_active_arm_feedback_uses_identity_and_names_not_order():
    document = {'objects': [obj(1, 'Arm', ['a', 'b']), obj(2, 'Other', ['a', 'b'])]}
    final = {'robot_object_id': 1, 'joint_names': ['b', 'a'], 'joints': [.2, .1]}
    joints, active = measured_joints(final, document)
    assert joints == {1: {'a': .1, 'b': .2}} and active == {1}
    final['joints'] = [float('nan'), .1]
    assert measured_joints(final, document) == ({}, set())


def test_partial_current_state_is_not_accepted():
    document = {'objects': [obj(1, 'Arm', ['a', 'b'])]}
    final = {'robot_object_id': 1, 'scene_driver_joints': {'1': {'a': .1}}}
    assert measured_joints(final, document) == ({}, set())
