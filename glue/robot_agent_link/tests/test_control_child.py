"""The bootstrap that starts the child: it must neither fail the run nor hide itself.

``runner.run`` points the child at ``link.control_child`` instead of
``demo.run_demo`` so that the perception redirect can happen inside the child.
That buys one risk worth a test of its own: a control checkout whose module
layout moved would raise while being patched, and the run would then stop
before the simulator ever started. Redirection is a fix, not a prerequisite.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from link.runner import LINK_ROOT
from link.qwen_vlm import BASE_URL_ENV

FAKE_CONTROL = Path(__file__).with_name("fake_control")


def launch(tmp_path, behaviour, base_url=None):
    """Run the bootstrap the way the runner does, against the stand-in control."""
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(FAKE_CONTROL), str(LINK_ROOT)]), FAKE_DEMO=json.dumps(behaviour))
    environment.pop(BASE_URL_ENV, None)
    if base_url:
        environment[BASE_URL_ENV] = base_url
    commands = tmp_path / "commands.json"
    commands.write_text(json.dumps({"request_id": "r-1", "commands": []}), encoding="utf-8")
    process = subprocess.run([sys.executable, "-m", "link.control_child",
                              "--commands", str(commands), "--catalog", str(tmp_path / "catalog.json"),
                              "--output", str(tmp_path / "out"), "--backend", "mujoco",
                              "--library", "lib.so", "--object-library-url", "http://127.0.0.1:8000"],
                             cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60)
    return process


def test_a_control_the_patch_cannot_find_is_reported_but_still_runs(tmp_path):
    # The fake control has no ``atomic_action`` at all: naming an endpoint here
    # raises while patching, exactly as a renamed upstream module would.
    process = launch(tmp_path, {"rows": 2}, base_url="http://127.0.0.1:8080/v1")
    assert process.returncode == 0
    assert (tmp_path / "out" / "report.json").is_file()
    assert "perception VLM -> http://127.0.0.1:8080/v1 FAILED" in process.stderr
    assert "running the control with its own endpoint" in process.stderr


def test_the_endpoint_in_use_is_named_in_the_child_log(tmp_path):
    process = launch(tmp_path, {"rows": 1})
    assert f"perception VLM -> unset ({BASE_URL_ENV})" in process.stderr
    assert json.loads((tmp_path / "out" / "report.json").read_text())["success"] is True


def test_the_childs_own_exit_status_survives_the_extra_frame(tmp_path):
    # The runner reads a nonzero return code as a crash, so an extra module in
    # front of the demo must not convert the control's failure into success.
    assert launch(tmp_path, {"rows": 1, "exit": 2}).returncode == 2
