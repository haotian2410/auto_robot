"""The subprocess transport: what the child is given, and what comes back.

Run against a stand-in for ``demo.run_demo`` (``tests/fake_control``), so this
covers the parts the bridge owns -- the command line, the working directory the
native library buries its logs in, the read-back of ``report.json``, progress
while a minutes-long run is still going, and the kill when it outlives its
budget -- without needing a simulator or the collision library.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest
from link.runner import LINK_ROOT, run
from link.workspace import build

FAKE_CONTROL = Path(__file__).with_name("fake_control")
LIBRARY = "/home/cscvlab/lht/_thirdparty/SimPlatform-main/build/libsim_platform_c.so"


@pytest.fixture
def stage(tmp_path, fixtures, monkeypatch):
    """One scratch workspace per call, whose child behaves as the test asks."""
    def prepare(behaviour):
        monkeypatch.setenv("FAKE_DEMO", json.dumps(behaviour))
        task = fixtures.json("commands_link_0002.json")
        return build(tmp_path, scene_document=fixtures.bytes("scene3.json"), task=task,
                     asset_root=str(fixtures.directory), request_id=task["request_id"])

    return prepare


def launch(ws, commands_path, **kwargs):
    kwargs.setdefault("timeout_seconds", 30)
    return run(ws, commands_path=commands_path, catalog_path=ws.catalog_path, python=sys.executable,
               control_root=FAKE_CONTROL, library=LIBRARY,
               object_library_url="http://127.0.0.1:8000", **kwargs)


def invocation(result):
    return json.loads((Path(result.workspace.out) / "invocation.json").read_text(encoding="utf-8"))


def test_the_child_is_handed_the_workspace_and_nothing_it_has_to_guess(stage):
    ws, commands_path, document = stage({"rows": 1})
    result = launch(ws, commands_path)
    seen = invocation(result)
    assert seen["cwd"] == str(ws.root)
    assert seen["paths_absolute"] is True
    assert seen["commands_readable"] and seen["catalog_readable"]
    # The control side is found through PYTHONPATH alone, and the native library
    # through its own variable: no install, no copy, no upstream file touched.
    # The bridge comes second because the child boots through it, and the
    # control comes first so any future name clash cannot shadow upstream code.
    assert seen["pythonpath"] == os.pathsep.join(
        [str(FAKE_CONTROL.resolve()), str(LINK_ROOT)])
    # The build publishes its library through a symlink; the child gets the file.
    assert seen["library"] == str(Path(LIBRARY).resolve())
    assert seen["tmpdir"] == str(ws.root)
    assert seen["backend"] == "mujoco" and seen["object_library_url"] == "http://127.0.0.1:8000"
    assert result.returncode == 0 and result.report["request_id"] == document["request_id"]


def test_the_perception_endpoint_the_child_is_told_is_the_one_it_uses(stage):
    # The control hard-codes a cloud endpoint behind a dead key, so the only
    # way to move perception onto the local model is to name it for the child.
    ws, commands_path, _ = stage({"rows": 1})
    result = launch(ws, commands_path,
                    child_env={"ROBOT_LINK_VLM_BASE_URL": "http://127.0.0.1:8080/v1"})
    assert invocation(result)["vlm_base_url"] == "http://127.0.0.1:8080/v1"


def test_explicit_collision_representation_is_forwarded(stage):
    ws, commands_path, _ = stage({"rows": 1})
    before = {str(p): p.read_bytes() for p in ws.root.rglob('scene.json')}
    result = launch(ws, commands_path, collision_model='precise')
    assert invocation(result)['collision_model'] == 'precise'
    assert before
    assert all(Path(p).read_bytes() == content for p, content in before.items())


def test_an_unnamed_child_environment_leaves_the_child_alone(stage):
    ws, commands_path, _ = stage({"rows": 1})
    result = launch(ws, commands_path, child_env={"ROBOT_LINK_VLM_BASE_URL": ""})
    assert invocation(result)["vlm_base_url"] is None


def test_the_logs_the_native_library_writes_stay_inside_the_scratch_directory(stage):
    ws, commands_path, _ = stage({"rows": 2})
    result = launch(ws, commands_path)
    assert (ws.root / "logs").exists()
    assert result.log_path == ws.root / "link_run.log" and result.log_path.is_file()
    # ``artifacts`` names the five files the control side promises; the fake's own
    # evidence file is not one of them.
    assert set(result.artifacts) == {"report.json", "trajectory.traj"}
    assert result.succeeded is True


def test_a_report_that_denies_success_is_not_a_success(stage):
    ws, commands_path, _ = stage({"rows": 3, "success": False})
    result = launch(ws, commands_path)
    assert result.returncode == 0 and result.succeeded is False
    assert len(result.step_summaries()) == 3


def test_a_child_that_died_on_the_spot_still_leaves_a_readable_failure(stage):
    ws, commands_path, _ = stage({"rows": 0, "exit": 2})
    result = launch(ws, commands_path)
    assert (result.succeeded, result.returncode) == (False, 2)
    assert result.report["error"] == "runner_report_missing" and result.report["steps"] == []


def test_progress_watches_the_report_grow_while_the_child_is_still_running(stage):
    ws, commands_path, _ = stage({"rows": 4, "delay": 0.2})
    seen = []
    result = launch(ws, commands_path, progress=seen.append, progress_interval=0.05)
    counts = [len(value["steps"]) for value in seen]
    assert len(set(counts)) > 1 and max(counts) <= 4 and counts == sorted(counts)
    # Every partial document is a prefix of the finished one, which is all a
    # progress line can be built from.
    final = result.report["steps"]
    assert all(value["steps"] == final[:len(value["steps"])] for value in seen)


@pytest.mark.parametrize("poll", [False, True])
def test_an_expired_budget_kills_the_child_it_started(stage, poll):
    ws, commands_path, _ = stage({"rows": 0, "sleep": 60})
    started = time.monotonic()
    result = launch(ws, commands_path, timeout_seconds=1.5, progress_interval=0.1,
                    progress=(lambda report: None) if poll else None)
    assert time.monotonic() - started < 10
    assert result.timed_out is True and result.succeeded is False
    assert result.report["error"] == "runner_report_missing"
    with pytest.raises(ProcessLookupError):
        os.kill(invocation(result)["pid"], 0)
