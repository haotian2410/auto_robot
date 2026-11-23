"""Validate upstream's editable snapshot without granting arbitrary scene writes."""
from copy import deepcopy
import json
import math


# Components authored by the control runtime for planning/collision diagnostics
# are not Scene v1 state.  They may be present in final_scene.json even when
# they were absent from the submitted document.  Keep this list deliberately
# narrow: arbitrary new components must still fail closed.
RUNTIME_ONLY_COMPONENTS = {"RobotModelRef"}


def _component_by_type(components):
    """Return components grouped by type without relying on array order.

    The control runtime is allowed to serialize components in a different
    order.  Scene identity is the object id and the component type, not the
    position in the JSON array.
    """
    result = {}
    for component in components:
        if not isinstance(component, dict):
            continue
        kind = component.get('component_type')
        if kind:
            result.setdefault(kind, []).append(component)
    return result


def _immutable_component_fields(kind, properties):
    """Fields which describe scene identity/assembly, rather than pose.

    Final scenes may legitimately change transforms, material state and
    measured ``now_*`` values.  These fields must remain stable so a runtime
    snapshot cannot silently replace the scene's asset or attachment graph.
    """
    if kind == 'MetadataRef':
        return {k: properties.get(k) for k in ('type', 'path', 'ref', 'metadata_ref')
                if k in properties}
    if kind == 'RobotDriver':
        return {k: properties.get(k) for k in
                ('joint_sequence', 'initial_joint_positions_rad', 'joint_limits')
                if k in properties}
    if kind == 'RobotAttachment':
        return {k: properties.get(k) for k in
                ('parent_object_id', 'child_link', 'parent_link') if k in properties}
    return None


def validated_snapshot(path, source, joints):
    candidate = json.loads(path.read_text(encoding='utf-8'))
    before, after = deepcopy(source), deepcopy(candidate)
    if len(after.get('objects', [])) != len(before.get('objects', [])):
        raise ValueError('control_final_scene_object_set_changed')
    for object_index, (left, right) in enumerate(zip(before['objects'], after['objects'])):
        if left['object_id_in_scene'] != right.get('object_id_in_scene'):
            raise ValueError('control_final_scene_identity_changed')
        old_components = left['components']
        new_components = right.get('components', [])
        # The control side can append a runtime-only model reference used by
        # its collision proxy builder.  It is evidence about this run, not a
        # public Scene component, so remove it from the comparison and from
        # the snapshot that will be published back to Gateway.
        old_runtime_counts = {}
        for component in old_components:
            kind = component.get('component_type') if isinstance(component, dict) else None
            if kind in RUNTIME_ONLY_COMPONENTS:
                old_runtime_counts[kind] = old_runtime_counts.get(kind, 0) + 1
        new_runtime_counts = {}
        for component in new_components:
            kind = component.get('component_type') if isinstance(component, dict) else None
            if kind in RUNTIME_ONLY_COMPONENTS:
                new_runtime_counts[kind] = new_runtime_counts.get(kind, 0) + 1
        # Remove only runtime components newly appended by this control run;
        # components already present in the submitted Scene remain part of the
        # immutable comparison.
        runtime_to_drop = set()
        for kind, count in new_runtime_counts.items():
            if count > old_runtime_counts.get(kind, 0):
                runtime_to_drop.add(kind)
        filtered_new = deepcopy([c for c in new_components
                                 if not (isinstance(c, dict)
                                         and c.get('component_type') in runtime_to_drop)])
        old_by_type = _component_by_type(old_components)
        new_by_type = _component_by_type(filtered_new)
        # Every authored component must still be present.  Extra control-only
        # components are tolerated; component order and harmless runtime
        # additions are not scene identity and must not invalidate a valid
        # execution result.
        for kind, old_items in old_by_type.items():
            if len(new_by_type.get(kind, [])) < len(old_items):
                raise ValueError('control_final_scene_components_changed')
        # Do not publish arbitrary control-private additions.  Keep the first
        # authored occurrence(s) for each type and discard surplus runtime
        # serialization entries; known RobotModelRef entries were already
        # handled above.  This is deliberately not an equality check.
        public_components = []
        seen = {}
        for component in filtered_new:
            kind = component.get('component_type') if isinstance(component, dict) else None
            if kind not in old_by_type:
                continue
            limit = len(old_by_type.get(kind, []))
            if seen.get(kind, 0) >= limit:
                continue
            seen[kind] = seen.get(kind, 0) + 1
            public_components.append(component)
        filtered_new = public_components
        new_by_type = _component_by_type(filtered_new)
        after['objects'][object_index]['components'] = filtered_new
        # ``after`` is the comparison copy; keep the candidate untouched until
        # the end so normalization does not alter the control artifact itself.
        after['objects'][object_index]['components'] = filtered_new
        for kind, old_items in old_by_type.items():
            new_items = new_by_type.get(kind, [])
            for component_index, old in enumerate(old_items):
                new = new_items[component_index]
                a, b = old.get('properties', {}), new.get('properties', {})
                immutable = _immutable_component_fields(kind, a)
                if immutable is not None:
                    if _immutable_component_fields(kind, b) != immutable:
                        raise ValueError('control_final_scene_immutable_fields_changed')
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
                            if norm_sq <= 1e-12 or abs(norm_sq - 1.0) > 5e-4:
                                raise ValueError('control_final_scene_quaternion_invalid')
                            norm = math.sqrt(norm_sq)
                            normalized = [v / norm for v in value]
                            a[key] = normalized
                            b[key] = normalized
                            # Update the matching occurrence, not the first
                            # component of the same type.
                            candidate_components = [c for c in candidate['objects'][object_index]['components']
                                                    if c.get('component_type') == kind]
                            if component_index < len(candidate_components):
                                candidate_components[component_index]['properties'][key] = normalized
                            continue
                        a.pop(key, None)
                        b.pop(key, None)
                elif kind == 'RobotDriver' and a.get('joint_sequence'):
                    measured = joints.get(left['object_id_in_scene'])
                    if measured is None or b.get('now_joint_positions_rad') != measured:
                        raise ValueError('control_final_scene_joint_evidence_mismatch')
        # Drop runtime-only data only after validation; the comparison copy is
        # independent so measured fields in the public snapshot are retained.
        if runtime_to_drop:
            candidate['objects'][object_index]['components'] = [c for c in
                candidate['objects'][object_index]['components']
                if not (isinstance(c, dict) and c.get('component_type') in runtime_to_drop)]
        # Do not compare the complete JSON: control-side names, component
        # ordering and mutable runtime properties are presentation/runtime
        # details.  Identity and assembly invariants were checked above.
    return candidate
