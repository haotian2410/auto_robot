"""Validate upstream's editable snapshot without granting arbitrary scene writes."""
from copy import deepcopy
import json
import math


def validated_snapshot(path, source, joints):
    candidate = json.loads(path.read_text(encoding='utf-8'))
    before, after = deepcopy(source), deepcopy(candidate)
    if len(after.get('objects', [])) != len(before.get('objects', [])):
        raise ValueError('control_final_scene_object_set_changed')
    for object_index, (left, right) in enumerate(zip(before['objects'], after['objects'])):
        if left['object_id_in_scene'] != right.get('object_id_in_scene'):
            raise ValueError('control_final_scene_identity_changed')
        if len(left['components']) != len(right.get('components', [])):
            raise ValueError('control_final_scene_components_changed')
        for component_index, (old, new) in enumerate(zip(left['components'], right['components'])):
            kind = old['component_type']
            if kind != new.get('component_type'):
                raise ValueError('control_final_scene_components_changed')
            a, b = old.get('properties', {}), new.get('properties', {})
            if kind == 'Transform':
                for key, size in [('position', 3), ('quaternion_xyzw', 4)]:
                    # OLS attachment transforms can omit these fields; retain
                    # an unchanged omission, never invent a measured pose.
                    if key not in a and key not in b:
                        continue
                    value = b.get(key)
                    if not isinstance(value, list) or len(value) != size or not all(
                            isinstance(v, (int, float)) and not isinstance(v, bool)
                            and math.isfinite(v) for v in value):
                        raise ValueError('control_final_scene_pose_invalid')
                    if key == 'quaternion_xyzw':
                        norm_sq = sum(v * v for v in value)
                        # Control snapshots may round unit quaternions (for example
                        # 0.707106... -> 0.7071).  Accept only a small, finite
                        # serialization drift and normalize the value before publish.
                        if norm_sq <= 1e-12 or abs(norm_sq - 1.0) > 5e-4:
                            raise ValueError('control_final_scene_quaternion_invalid')
                        norm = math.sqrt(norm_sq)
                        normalized = [v / norm for v in value]
                        # Compare the normalized representation so the bridge
                        # can publish it without treating serialization drift as
                        # an immutable-scene mutation.
                        a[key] = normalized
                        b[key] = normalized
                        candidate['objects'][object_index]['components'][component_index]['properties'][key] = normalized
                        continue
                    a.pop(key, None)
                    b.pop(key, None)
            elif kind == 'RobotDriver' and a.get('joint_sequence'):
                measured = joints.get(left['object_id_in_scene'])
                if measured is None or b.get('now_joint_positions_rad') != measured:
                    raise ValueError('control_final_scene_joint_evidence_mismatch')
                a.pop('now_joint_positions_rad', None)
                b.pop('now_joint_positions_rad', None)
    if before != after:
        raise ValueError('control_final_scene_immutable_fields_changed')
    return candidate
