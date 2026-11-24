"""Read a control report back into Brain-shaped facts.

The child process reports two things the Brain cares about: whether each
command it exported succeeded, and where every body ended up.  The report keys
poses by ``object_name_in_scene`` while the Brain addresses objects by
``object_id_in_scene``, so the scene document that was handed to the runner is
used as the translation table here, at the boundary, rather than downstream.
"""
from __future__ import annotations

from pathlib import Path
from .joint_feedback import measured_joints, complete_state
from .final_scene import validated_snapshot

SUCCESS = "success"
# A MuJoCo settle is not a placement: reporting a millimetre of drift as a move
# would make every task look like it rearranged the whole table.
MOVE_TOLERANCE_M = 1e-3
MOVE_TOLERANCE_QUAT = 1e-3
DETAIL_LIMIT = 800


def object_names(scene_document) -> dict[str, int]:
    objects = scene_document.get("objects") or []
    return {item["object_name_in_scene"]: item["object_id_in_scene"]
            for item in objects
            if isinstance(item, dict) and isinstance(item.get("object_name_in_scene"), str)
            and isinstance(item.get("object_id_in_scene"), int)}


def restore_runtime_state_names(report: dict, name_mapping: list[dict]) -> None:
    """Restore control transport names in the measured final state.

    ``SubprocessExecutionAdapter`` calls this at the bridge boundary before
    ``summarize``.  Keeping the helper here also makes the name contract
    explicit for other bridge callers and keeps feedback validation independent
    of the control repository's internal naming scheme.
    """
    if not isinstance(report, dict):
        return
    mapping = {
        row.get("transport_name"): row.get("original_name")
        for row in name_mapping
        if isinstance(row, dict)
        and isinstance(row.get("transport_name"), str)
        and isinstance(row.get("original_name"), str)
    }
    final = report.get("final_state")
    if not isinstance(final, dict):
        return
    gripper = final.get("gripper")
    if isinstance(gripper, dict) and isinstance(gripper.get("object_id"), str):
        gripper["object_id"] = mapping.get(gripper["object_id"], gripper["object_id"])
    objects = final.get("objects")
    if isinstance(objects, dict):
        final["objects"] = {mapping.get(key, key): value for key, value in objects.items()}


def _transform(scene_document, object_id) -> dict:
    for item in scene_document.get("objects") or []:
        if isinstance(item, dict) and item.get("object_id_in_scene") == object_id:
            for component in item.get("components") or []:
                if isinstance(component, dict) and component.get("component_type") == "Transform":
                    return component.get("properties") or {}
    return {}


def _moved(before, after) -> bool:
    if not before or not isinstance(after, dict):
        return False
    position, quaternion = before.get("position"), before.get("quaternion_xyzw")
    if not isinstance(position, list) or not isinstance(after.get("position"), list):
        return False
    if len(position) != len(after["position"]) or any(
            abs(float(a) - float(b)) > MOVE_TOLERANCE_M for a, b in zip(position, after["position"])):
        return True
    start = before.get("quaternion_xyzw")
    end = after.get("quaternion_xyzw")
    return isinstance(start, list) and isinstance(end, list) and len(start) == len(end) and any(
        abs(float(a) - float(b)) > MOVE_TOLERANCE_QUAT for a, b in zip(start, end))


def _short(text) -> str:
    text = str(text)
    return text[:DETAIL_LIMIT] + ("…" if len(text) > DETAIL_LIMIT else "")


# Failures that never reach the control's report writer, so error_messages.py has
# nothing to describe and the sentence is ours to write.
RUNNER_DETAILS = {"runner_report_missing": "控制端子进程没有写出最终结果摘要，可能在启动或执行过程中退出",
                  "control_output_invalid": "控制端输出不完整或格式无效，实际状态需核验"}


def steps(report, describe=None) -> list[dict]:
    steps = []
    for row in report.get("steps") or []:
        error = row.get("error") if isinstance(row.get("error"), dict) else {}
        status = SUCCESS if (row.get("status") == SUCCESS or row.get("success") is True) else "failed"
        message = error.get("error_message")
        if status != SUCCESS and describe is not None:
            message = describe(error)
        steps.append({"command_id": str(row.get("command_id")),
                      "skill_name": row.get("skill_name"),
                      "status": status,
                      "error_code": error.get("error_code"),
                      "detail": None if message is None else _short(message)})
    return steps


def _error_code(report, steps) -> str | None:
    error = report.get("error") if isinstance(report.get("error"), dict) else {}
    if error.get("error_code"):
        return str(error["error_code"])
    for step in steps:
        if step["status"] != SUCCESS:
            return step["error_code"] or "execution_step_failed"
    return str(error.get("stage") or error.get("failed_stage") or "control_reported_failure") if error else None


def _failed_error(report) -> dict | str | None:
    """The payload that explains this run, from the report or from its first failed step.

    A run can end with no top-level error and one rejected command; reading only
    ``report["error"]`` there would show the user a tail of raw log instead of the
    reason the arm stopped.
    """
    error = report.get("error")
    if error is not None:
        return error
    for row in report.get("steps") or []:
        if isinstance(row.get("error"), dict) and row.get("error"):
            return row["error"]
    return None


def _detail(result, describe=None) -> str:
    """The one message a person will read, kept to a length a chat reply can carry.

    A control failure message can enumerate every contact pair it found, and this
    string travels to the browser as the task's error message, so the control's own
    evidence sentence is used whenever its module can be read.
    """
    report = result.report or {}
    error = _failed_error(report)
    if isinstance(error, dict):
        if describe is not None:
            return _short(describe(error))
        message = error.get("message") or error.get("error_message") or result.stdout_tail
    elif isinstance(error, str):
        message = RUNNER_DETAILS.get(error) or error
    elif result.timed_out:
        message = "执行超时：控制端子进程未在规定时间内结束，已被终止"
    else:
        message = result.stdout_tail
    return _short(message)


def summarize(result, scene_document, describe=None) -> dict:
    """Reduce an ``ExecutionResult`` to status, moved poses and the held object."""
    report = result.report or {}
    completed = steps(report, describe)
    final = report.get("final_state") or {}
    names = object_names(scene_document)
    moved = {}
    for name, pose in (final.get("objects") or {}).items():
        identifier = names.get(name)
        if identifier is None or not _moved(_transform(scene_document, identifier), pose):
            continue
        moved[identifier] = {"position": [float(v) for v in pose["position"]],
                             "quaternion_xyzw": [float(v) for v in pose["quaternion_xyzw"]]}
    # Control reports articulated mechanisms by joint name.  Translate those
    # names to Scene object ids at the bridge boundary so Brain can persist
    # drawer/door state without treating it as a Transform edit.
    articulated_joints, active_joint_ids = measured_joints(final, scene_document)
    held = (final.get("gripper") or {}).get("object_id")
    traj = Path(result.traj_path)
    verified = complete_state(final, scene_document, articulated_joints, active_joint_ids)
    snapshot = None
    snapshot_error = None
    no_effect = (report.get("execution_started") is False
                 and report.get("success") is False
                 and report.get("command_count", 0) == 0
                 and (report.get("steps") == [] or
                      (report.get("commands_file") and report.get("command_count") == 0
                       and not report.get("steps")))
                 and not completed and not final and not result.timed_out
                 and report.get("successful_command_count", 0) == 0
                 and report.get("failed_command_count", 0) == 0
                 and not (traj.is_file() and traj.stat().st_size))
    # New upstream runs must provide their authored final Scene. Legacy reports
    # retain the measured-state adapter for old fixtures and saved evidence.
    if report.get('commands_file') and not no_effect:
        try:
            snapshot = validated_snapshot(result.final_scene_path, scene_document, articulated_joints)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            snapshot_error = str(exc)
            verified = False
    return {"ok": result.succeeded and snapshot_error is None,
            "steps": completed,
            "moved": moved,
            "articulated_joints": articulated_joints,
            "active_joint_ids": sorted(active_joint_ids),
            "state_verified": verified,
            "final_scene": snapshot if verified else None,
            "final_scene_path": str(result.final_scene_path) if snapshot is not None else None,
            "no_effect_verified": bool(no_effect),
            "holding_object": names.get(held) if isinstance(held, str) else None,
            "execution_checkpoint": final.get("execution_checkpoint") if verified else None,
            "traj_path": str(traj) if traj.is_file() and traj.stat().st_size else None,
            "snapshot_error": snapshot_error,
            "error_code": 'control_final_scene_invalid' if snapshot_error and result.succeeded else None if result.succeeded else (_error_code(report, completed)
                                                          or ("execution_timeout" if result.timed_out
                                                              else "execution_runner_failed")),
            "detail": snapshot_error if result.succeeded else _detail(result, describe)}
