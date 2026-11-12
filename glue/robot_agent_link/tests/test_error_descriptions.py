"""The bridge borrows the control's failure sentences instead of writing its own.

``demo/error_messages.py`` is the authority on what a control failure means -- it
keeps the measurement that killed the step -- so these tests pin two things: that
the sentence the Brain shows is the one the control would have printed, and that a
checkout this machine cannot read degrades to the raw report rather than hiding the
failure behind an import error.
"""
import json
from types import SimpleNamespace

import pytest
from link.error_descriptions import describe_for
from link.feedback import steps, summarize
from link.runner import ExecutionResult

CONTACTS = {"error_code": "PATH_CONSTRAINT_INFEASIBLE", "failed_stage": "planning",
            "details": {"contacts": [{"geom1": "Apple", "geom2": "WorkTable", "distance": -0.003142}]}}


@pytest.fixture(scope="session")
def describe(control_root):
    return describe_for(str(control_root))


def outcome(tmp_path, report, *, timed_out=False, tail=""):
    directory = tmp_path / "out"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return ExecutionResult(returncode=1, timed_out=timed_out, report=report,
                           workspace=SimpleNamespace(out=directory), stdout_tail=tail)


def failed_run(*, top_level, step):
    return {"success": False, "error": top_level,
            "steps": [{"command_id": "1", "skill_name": "approach", "success": False,
                       "status": "failed", **({"error": step} if step is not None else {})}],
            "final_state": {"objects": {}, "gripper": {}}}


def test_the_control_keeps_its_own_error_vocabulary(describe):
    assert describe(CONTACTS) == \
        "Apple 与 WorkTable 穿透 3.142 mm，无法按指定路径运动（PATH_CONSTRAINT_INFEASIBLE）"
    assert describe({"error_code": "PATH_NOT_FOUND",
                     "details": {"planner": "rrt_connect", "planning_time": 5}}) == \
        "RRT-Connect 在5 s 规划预算内未找到无碰撞路径（PATH_NOT_FOUND）"
    # A code that carries no evidence still gets the control's own gloss for it.
    assert describe({"error_code": "DRAWER_TARGET_NOT_REACHED"}) == \
        "抽屉未达到目标开度；上游未提供具体失败原因（DRAWER_TARGET_NOT_REACHED）"
    assert describe({}) == "步骤执行失败；上游未提供具体失败原因"


def test_the_failure_sentence_replaces_the_english_report_message(describe, tmp_path):
    report = failed_run(top_level={"stage": "execution", "message": "Start state is in collision."},
                        step=CONTACTS)
    summary = summarize(outcome(tmp_path, report), {"objects": []}, describe)
    assert summary["detail"] == "运动起点存在碰撞或安全间距不足"
    assert summary["steps"][0]["detail"] == \
        "Apple 与 WorkTable 穿透 3.142 mm，无法按指定路径运动（PATH_CONSTRAINT_INFEASIBLE）"


def test_a_step_failure_without_a_report_error_is_not_left_as_a_log_dump(describe, tmp_path):
    """run_demo can end with only the step carrying a reason; that reason is the answer."""
    report = failed_run(top_level=None, step=CONTACTS)
    assert summarize(outcome(tmp_path, report, tail="Traceback ..."), {"objects": []},
                     describe)["detail"] == \
        "Apple 与 WorkTable 穿透 3.142 mm，无法按指定路径运动（PATH_CONSTRAINT_INFEASIBLE）"


def test_without_the_control_its_own_words_are_kept(tmp_path):
    report = failed_run(top_level={"stage": "execution", "message": "boom"}, step=None)
    summary = summarize(outcome(tmp_path, report), {"objects": []}, None)
    assert summary["detail"] == "boom"
    assert steps(report)[0]["detail"] is None


def test_a_missing_checkout_degrades_instead_of_failing(tmp_path):
    assert describe_for(str(tmp_path / "no-control")) is None
    assert describe_for(None) is None
