"""Reading a control report back: one row per command, only real moves, one message.

``report_planning_initialization_collision.json`` is the byte-for-byte output of
the first real Linux run, which died before its first command.  The success shape
below is written out rather than recorded, because nothing has succeeded yet; its
fields follow ``demo/run_demo.py`` (steps, error, final_state) and
``execution/mujoco.py`` (objects keyed by name, quaternions already xyzw).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from link.feedback import (DETAIL_LIMIT, MOVE_TOLERANCE_M, object_names, steps, summarize,
                           restore_runtime_state_names)
from link.runner import ExecutionResult

APPLE, TRAY = 7, 13
APPLE_AT = [0.18000000000000008, -0.18499999999999997, 0.637373]
QUATERNION = [0.0, 0.0, 0.7071067811865476, 0.7071067811865476]
STANDING = [0.0, 0.0, 0.0, 1.0]
SKILLS = ["approach", "pick", "lift_up", "retreat", "move_to", "approach", "put", "retreat"]


def outcome(tmp_path, report, *, returncode=0, timed_out=False, tail=""):
    """The dataclass the runner hands back, with a real ``out`` directory behind it."""
    directory = tmp_path / "out"
    directory.mkdir(parents=True, exist_ok=True)
    if report is not None:
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return ExecutionResult(returncode=returncode, timed_out=timed_out, report=report or {},
                           workspace=SimpleNamespace(out=directory), stdout_tail=tail)


def run_report(*, success=True, objects=None, holding=None, error=None):
    return {"request_id": "link-1", "success": success,
            "steps": [{"command_id": str(index), "skill_name": skill,
                       "success": True, "status": "success"}
                      for index, skill in enumerate(SKILLS, 1)],
            "error": error,
            "final_state": {"objects": objects if objects is not None else {
                                "Apple": {"position": [0.28, 0.165, 0.66], "quaternion_xyzw": QUATERNION},
                                "WorkTable": {"position": [0.0, 0.0, 0.0], "quaternion_xyzw": STANDING}},
                            "gripper": {"object_id": holding}}}


def aborted_at(index, code, message):
    """The command at ``index`` failed and ended the run; the rest never reported."""
    return {**run_report(success=False), "steps": [
        {"command_id": str(number), "skill_name": skill, "success": number < index,
         "status": "success" if number < index else "failed",
         **({"error": {"error_code": code, "error_message": message}} if number == index else {})}
        for number, skill in enumerate(SKILLS, 1)], "error": {"stage": "execution", "message": message}}


@pytest.fixture(scope="session")
def scene(fixtures):
    return fixtures.json("scene3.json")


def test_names_are_translated_once_at_the_boundary(scene):
    names = object_names(scene)
    assert (names["Apple"], names["Tray"]) == (APPLE, TRAY)
    assert all(isinstance(identifier, int) for identifier in names.values())


@pytest.mark.parametrize('started, timed_out, final, verified', [
    (False, False, None, True), (None, False, None, False),
    (True, False, None, False), (False, True, None, False),
    (False, False, {'objects': {}}, False),
])
def test_preflight_evidence_is_explicit(tmp_path, scene, started, timed_out, final, verified):
    report = {'success': False, 'steps': [], 'execution_started': started, 'final_state': final}
    result = outcome(tmp_path, report, returncode=1, timed_out=timed_out)
    assert summarize(result, scene)['no_effect_verified'] is verified


def test_new_preflight_report_needs_no_final_snapshot(tmp_path, scene):
    report = dict(success=False, execution_started=False, command_count=0,
                  commands_file='commands/results.jsonl',
                  error=dict(stage='validation', message='invalid object name'))
    summary = summarize(outcome(tmp_path, report, returncode=1), scene)
    assert summary['no_effect_verified'] is True
    assert summary['snapshot_error'] is None
    assert summary['error_code'] != 'control_final_scene_invalid'
    assert summary['detail'] == 'invalid object name'


def test_failure_without_legacy_stage_does_not_hide_original_message(tmp_path, scene):
    report = dict(success=False, execution_started=False, steps=[],
                  error=dict(failed_stage='validation', error_message='joint1 velocity is zero'))
    summary = summarize(outcome(tmp_path, report, returncode=1), scene)
    assert summary['error_code'] == 'validation'
    assert summary['detail'] == 'joint1 velocity is zero'


def test_contradictory_command_count_cannot_certify_no_effect(tmp_path, scene):
    report = dict(success=False, execution_started=False, command_count=1, steps=[])
    assert summarize(outcome(tmp_path, report, returncode=1), scene)['no_effect_verified'] is False


def test_a_clean_run_reports_every_command_and_one_moved_object(tmp_path, scene):
    summary = summarize(outcome(tmp_path, run_report(holding="Apple")), scene)
    assert summary["ok"] is True and summary["error_code"] is None and summary["detail"] is None
    assert [step["status"] for step in summary["steps"]] == ["success"] * 8
    assert [step["command_id"] for step in summary["steps"]] == [str(number) for number in range(1, 9)]
    assert summary["moved"] == {APPLE: {"position": [0.28, 0.165, 0.66], "quaternion_xyzw": QUATERNION}}
    assert summary["holding_object"] == APPLE


def test_verified_grasp_state_is_recoverable_for_next_task(tmp_path, scene):
    # A successful pick intentionally ends with holding=true.  The measured
    # object identity is valid state, not an incomplete final snapshot.
    report = run_report(holding="Apple")
    report["final_state"].update({
        "robot_object_id": 2,
        "joint_names": ["j1"],
        "joints": [0.1],
        "gripper": {"known": True, "holding": True, "object_id": "Apple"},
        "scene_driver_joints": {"2": {"j1": 0.1}},
    })
    from link.joint_feedback import complete_state
    document = {"objects": [
        {"object_id_in_scene": 2, "object_name_in_scene": "Arm",
         "components": [{"component_type": "RobotDriver", "properties":
                         {"joint_sequence": ["j1"]}}]},
        {"object_id_in_scene": APPLE, "object_name_in_scene": "Apple",
         "components": [{"component_type": "MetadataRef", "properties": {}}]},
    ]}
    joints, active = {2: {"j1": 0.1}}, {2}
    assert complete_state(report["final_state"], document, joints, active) is True


def test_transport_names_are_restored_in_measured_grasp_state():
    """A non-ASCII/duplicate Scene name must not invalidate a successful pick."""
    report = {"final_state": {
        "objects": {"object_7": {"position": [0.1, 0.2, 0.3],
                                   "quaternion_xyzw": STANDING}},
        "gripper": {"known": True, "holding": True, "object_id": "object_7"},
    }}
    restore_runtime_state_names(report, [{"object_id": 7,
                                          "original_name": "苹果",
                                          "transport_name": "object_7"}])
    assert report["final_state"]["gripper"]["object_id"] == "苹果"
    assert "苹果" in report["final_state"]["objects"]


def test_articulated_mechanism_state_is_mapped_to_scene_object_id(tmp_path, scene):
    document = json.loads(json.dumps(scene))
    document["objects"].append({
        "object_id_in_scene": 19,
        "object_name_in_scene": "Drawer",
        "components": [{"component_type": "RobotDriver", "properties": {
            "role": "passive", "joint_sequence": ["drawer01", "drawer02"]
        }}],
    })
    report = run_report()
    report["final_state"]["mechanisms"] = {
        "Drawer__link": {"joint_name": "drawer01", "joint_position": 0.12},
        "Drawer__link2": {"joint_name": "drawer02", "joint_position": 0.08},
    }
    summary = summarize(outcome(tmp_path, report), document)
    assert summary["articulated_joints"] == {19: {"drawer01": 0.12, "drawer02": 0.08}}


def test_the_clip_is_offered_only_when_the_runner_left_a_non_empty_one(tmp_path, scene):
    assert summarize(outcome(tmp_path, run_report()), scene)["traj_path"] is None
    empty = outcome(tmp_path, run_report())
    (Path(empty.workspace.out) / "trajectory.traj").write_bytes(b"")
    assert summarize(empty, scene)["traj_path"] is None
    recorded = outcome(tmp_path, run_report())
    clip = Path(recorded.workspace.out) / "trajectory.traj"
    clip.write_bytes(b"TRAJ")
    assert Path(summarize(recorded, scene)["traj_path"]) == clip


def test_settling_is_not_a_move_and_an_unknown_name_is_not_an_object(tmp_path, scene):
    drifted = [value + MOVE_TOLERANCE_M / 10 for value in APPLE_AT]
    summary = summarize(outcome(tmp_path, run_report(objects={
        "Apple": {"position": drifted, "quaternion_xyzw": QUATERNION},
        "WorkTable": {"position": [0.0, 0.0, 0.0], "quaternion_xyzw": STANDING},
        "Ghost": {"position": [1.0, 1.0, 1.0], "quaternion_xyzw": STANDING}})), scene)
    assert summary["ok"] is True and summary["moved"] == {}
    assert summary["holding_object"] is None


def test_a_suspend_row_counts_as_a_finished_command(tmp_path, scene):
    report = run_report()
    report["steps"][2].update(status="suspend", success=True, completion="partial")
    summary = summarize(outcome(tmp_path, report), scene)
    assert summary["steps"][2] == {"command_id": "3", "skill_name": "lift_up", "status": "success",
                                   "error_code": None, "detail": None}


def test_the_failing_command_names_the_failure(tmp_path, scene):
    summary = summarize(outcome(tmp_path, aborted_at(2, "ik_failed", "no reachable pose"),
                               returncode=1), scene)
    assert summary["ok"] is False
    assert [step["status"] for step in summary["steps"]] == ["success"] + ["failed"] * 7
    assert summary["steps"][1] == {"command_id": "2", "skill_name": "pick", "status": "failed",
                                   "error_code": "ik_failed", "detail": "no reachable pose"}
    assert summary["steps"][0]["error_code"] is None
    # A row-level code outranks the report's stage, which is the vaguer source.
    assert summary["error_code"] == "ik_failed" and summary["detail"] == "no reachable pose"
    # What the simulator ended up with is reported even though the plan failed:
    # whether a failed run may rewrite the world is the caller's decision, not
    # something this reader gets to hide.
    assert summary["moved"] == {APPLE: {"position": [0.28, 0.165, 0.66],
                                        "quaternion_xyzw": QUATERNION}}


def test_a_report_level_code_outranks_every_other_source(tmp_path, scene):
    report = aborted_at(2, "ik_failed", "no reachable pose")
    report["error"]["error_code"] = "collision_at_grasp"
    summary = summarize(outcome(tmp_path, report, returncode=1), scene)
    assert summary["error_code"] == "collision_at_grasp"
    assert summary["detail"] == "no reachable pose"


def test_a_control_message_that_enumerates_every_contact_is_cut_short(tmp_path, scene):
    message = "Initial state collides: " + "('geom1': 'x',) " * 600
    summary = summarize(outcome(tmp_path, aborted_at(1, "collision", message), returncode=1), scene)
    assert summary["detail"] == message[:DETAIL_LIMIT] + "…"
    assert summary["steps"][0]["detail"] == message[:DETAIL_LIMIT] + "…"


def test_the_recorded_first_run_reduces_to_a_refusal_without_touching_the_scene(
        tmp_path, fixtures, scene):
    report = fixtures.json("report_planning_initialization_collision.json")
    summary = summarize(outcome(tmp_path, report, returncode=1), scene)
    assert report["success"] is False and report["steps"] == []
    assert summary["ok"] is False and summary["steps"] == [] and summary["moved"] == {}
    assert summary["traj_path"] is None and summary["holding_object"] is None
    assert summary["error_code"] == "planning_initialization"
    assert summary["detail"].startswith("Initial state collides")
    assert len(summary["detail"]) == DETAIL_LIMIT + 1


def test_a_run_that_outlived_its_budget_is_a_timeout(tmp_path, scene):
    summary = summarize(outcome(tmp_path, {"success": False, "steps": []}, returncode=-9,
                               timed_out=True), scene)
    assert summary["ok"] is False and summary["traj_path"] is None
    assert summary["error_code"] == "execution_timeout"
    # The kill leaves no report error behind, so the timeout itself is the sentence.
    assert summary["detail"] == "执行超时：控制端子进程未在规定时间内结束，已被终止"


def test_a_report_the_runner_had_to_invent_is_not_a_crash_in_the_reader(tmp_path, scene):
    missing = {"success": False, "steps": [], "error": "runner_report_missing", "stderr": "boom"}
    summary = summarize(outcome(tmp_path, missing, returncode=1, tail="boom"), scene)
    assert summary["error_code"] == "execution_runner_failed"
    # A runner code is not a sentence a browser user can act on, so the bridge
    # names what it means instead of forwarding the key.
    assert summary["detail"] == "控制端子进程没有写出最终结果摘要，可能在启动或执行过程中退出"
    assert summary["steps"] == []


def test_step_rows_keep_their_identifiers_as_text(scene):
    assert steps({"steps": [{"command_id": 4, "skill_name": "put", "status": "failed"}]}) == \
        [{"command_id": "4", "skill_name": "put", "status": "failed",
          "error_code": None, "detail": None}]
    assert steps({}) == []


def test_execution_checkpoint_is_forwarded_by_stable_object_id(tmp_path, scene):
    report = run_report(holding='Apple')
    report['final_state'].update(robot_object_id=2, joint_names=['j1'], joints=[.1],
        scene_driver_joints={'2': {'j1': .1}})
    report['final_state']['gripper'].update(known=True, holding=True)
    checkpoint = {'schema_version': 1, 'held_object_id': APPLE, 'source_request_id': 'link-1'}
    report['final_state']['execution_checkpoint'] = checkpoint
    document = {'objects': [
        {'object_id_in_scene': 2, 'object_name_in_scene': 'Arm', 'components': [
            {'component_type': 'RobotDriver', 'properties': {'joint_sequence': ['j1']}}]},
        {'object_id_in_scene': APPLE, 'object_name_in_scene': 'Apple', 'components': [
            {'component_type': 'MetadataRef', 'properties': {}}]}]}
    summary = summarize(outcome(tmp_path, report), document)
    assert summary['state_verified']
    assert summary['execution_checkpoint'] == checkpoint
    report['final_state']['gripper']['known'] = False
    assert summarize(outcome(tmp_path, report), document)['execution_checkpoint'] is None
