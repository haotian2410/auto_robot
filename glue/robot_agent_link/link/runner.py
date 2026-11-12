"""Run the control side as a child process and read its structured report back.

A subprocess rather than an in-process call for two reasons this project cannot
solve another way: the native library writes its log files relative to the
current directory, and a segfault inside FCL or a MuJoCo step must not take the
Brain's web service down with it.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPORT_NAME = "report.json"
TRAJ_NAME = "trajectory.traj"
FRAMES_NAME = "frames.jsonl"
FINAL_STATE_NAME = "final_state.npz"
BLACKBOARD_NAME = "blackboard.json"
LINK_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ExecutionResult:
    returncode: int
    timed_out: bool
    report: dict
    workspace: object = None
    log_path: Path = None
    stdout_tail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.report.get("success") is True

    @property
    def report_path(self):
        return Path(self.workspace.out) / REPORT_NAME

    @property
    def traj_path(self):
        return Path(self.workspace.out) / TRAJ_NAME

    @property
    def artifacts(self):
        out = Path(self.workspace.out)
        return {path.name: path for path in (out / REPORT_NAME, out / TRAJ_NAME, out / FRAMES_NAME,
                                             out / FINAL_STATE_NAME, out / BLACKBOARD_NAME)
                if path.is_file()}

    def step_summaries(self):
        return [{"command_id": step.get("command_id"), "skill_name": step.get("skill_name"),
                 "status": step.get("status"), "error": step.get("error")}
                for step in self.report.get("steps", [])]


def _child_env(control_root, library, workspace_root, extra=None, base=None):
    env = dict(os.environ if base is None else base)
    # The control is found by path, never by install, so upstream stays a clean
    # checkout. This repository comes second: the child starts in
    # ``link.control_child`` and has to import both, and ordering the control
    # first means a future name clash resolves in favour of the code we do not
    # own rather than silently shadowing it.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(control_root).resolve()), str(LINK_ROOT)])
    env["SIM_PLATFORM_LIBRARY"] = str(Path(library).resolve())
    env["TMPDIR"] = str(Path(workspace_root).resolve())
    env.update({key: str(value) for key, value in (extra or {}).items() if value})
    return env


def _terminate(process):
    """The child owns native threads and a process group of its own."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            continue
    return process.returncode if process.returncode is not None else -signal.SIGKILL


def run(workspace, *, commands_path, catalog_path, python, control_root, library,
        object_library_url, timeout_seconds=180.0, backend="mujoco", progress=None,
        progress_interval=1.0, child_env=None):
    argv = [str(python), "-m", "link.control_child",
            "--commands", str(commands_path),
            "--catalog", str(catalog_path),
            "--output", str(workspace.out),
            "--backend", backend,
            "--library", str(library),
            "--object-library-url", str(object_library_url)]
    log_path = workspace.root / "link_run.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(argv, cwd=workspace.root, env=_child_env(
            control_root, library, workspace.root, child_env), stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        if progress is None:
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode = _terminate(process)
        else:
            # ``report.json`` is rewritten after every command, so a reader
            # willing to see a half-written file can tell how far a
            # minutes-long run has got.
            seen = {}
            while True:
                try:
                    returncode = process.wait(timeout=progress_interval)
                    break
                except subprocess.TimeoutExpired:
                    timeout_seconds -= progress_interval
                    if timeout_seconds <= 0:
                        timed_out = True
                        returncode = _terminate(process)
                        break
                    try:
                        partial = json.loads((Path(workspace.out) / REPORT_NAME).read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if isinstance(partial, dict) and partial != seen:
                        seen = partial
                        progress(partial)
    # The scene registrar rewrites catalog.json in place; keep the log noise
    # inside the scratch directory we own instead of letting it accumulate.
    logs = workspace.root / "logs"
    if logs.is_dir():
        shutil.rmtree(logs, ignore_errors=True)
    raw = log_path.read_bytes()
    report_path = Path(workspace.out) / REPORT_NAME
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {
        "success": False, "steps": [],
        "error": "runner_report_missing", "stderr": raw.decode("utf-8", "replace")[-4000:]}
    return ExecutionResult(returncode=returncode, timed_out=timed_out, report=report,
                           workspace=workspace, log_path=log_path,
                           stdout_tail=raw.decode("utf-8", "replace")[-4000:])
