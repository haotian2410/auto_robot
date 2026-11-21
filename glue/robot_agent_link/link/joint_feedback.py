"""Object-scoped measured joint feedback (never global suffix matching)."""
import math


def measured_joints(final, document):
    result, active = {}, set()
    for obj in document.get('objects', []):
        components = {c['component_type']: c.get('properties', {}) for c in obj.get('components', [])}
        driver = components.get('RobotDriver', {})
        sequence = driver.get('joint_sequence') or []
        if not sequence:
            continue
        identifier, name = obj['object_id_in_scene'], obj['object_name_in_scene']
        values = {}
        explicit = (final.get('scene_driver_joints') or {}).get(str(identifier))
        if isinstance(explicit, dict):
            values = explicit
            if final.get('robot_object_id') == identifier:
                active.add(identifier)
        elif final.get('robot_object_id') == identifier:
            names, positions = final.get('joint_names', []), final.get('joints', [])
            if len(names) != len(positions) or len(set(names)) != len(names) or set(names) != set(sequence):
                continue
            values = dict(zip(names, positions))
            active.add(identifier)
        else:
            mechanisms = final.get('mechanisms') or {}
            for joint in sequence:
                matches = [entry for key, entry in mechanisms.items()
                           if isinstance(entry, dict) and key.startswith(name + '__')
                           and entry.get('joint_name', key) in (joint, name + '__' + joint)]
                if len(matches) == 1:
                    values[joint] = matches[0].get('joint_position')
        if (set(values) == set(sequence) and all(isinstance(v, (int, float))
                and not isinstance(v, bool) and math.isfinite(v) for v in values.values())):
            result[identifier] = {key: float(value) for key, value in values.items()}
    return result, active & result.keys()


def complete_state(final, document, joints, active):
    """Fail closed for partial execution: complete arm, passive and rigid state.

    A held payload cannot currently be reconstructed by the fresh subprocess.
    Such a run remains unknown even if its arm positions were measured.
    """
    if not active:
        return False
    gripper = final.get('gripper') or {}
    if gripper.get('known') is not True or gripper.get('holding') is not False or gripper.get('object_id'):
        return False
    for obj in document.get('objects', []):
        components = {c['component_type']: c.get('properties', {}) for c in obj.get('components', [])}
        if components.get('RobotDriver', {}).get('joint_sequence'):
            if obj['object_id_in_scene'] not in joints:
                return False
        if ('MetadataRef' in components and not any(k in components for k in ('Camera', 'RobotAttachment'))
                and obj['object_id_in_scene'] not in active):
            pose = (final.get('objects') or {}).get(obj['object_name_in_scene'], {})
            for key, size in (('position', 3), ('quaternion_xyzw', 4)):
                values = pose.get(key, [])
                if len(values) != size or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                    return False
    return True
