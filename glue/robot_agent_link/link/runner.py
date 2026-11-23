"""Run the control side as a child process and read its structured report back.

A subprocess rather than an in-process call for two reasons this project cannot
solve another way: the native library writes its log files relative to the
current directory, and a segfault inside FCL or a MuJoCo step must not take the
Brain's web service down with it.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPORT_NAME = "report.json"
TRAJ_NAME = "trajectory.traj"
FRAMES_NAME = "frames.jsonl"
FINAL_STATE_NAME = "final_state.npz"
BLACKBOARD_NAME = "blackboard.json"
NEW_ARTIFACTS = ('summary.json', 'commands/results.jsonl',
                 'trajectories/execution.traj', 'trajectories/frames.jsonl',
                 'snapshots/final_scene.json', 'snapshots/state.json',
                 'snapshots/final_state.npz', 'snapshots/blackboard.json')
LINK_ROOT = Path(__file__).resolve().parents[1]


def read_report(out, *, partial=False):
    """Adapt upstream persisted views; missing final summary is never success."""
    out = Path(out)
    summary = out / 'summary.json'
    rows_path = out / 'commands/results.jsonl'
    if summary.exists() or rows_path.exists():
        report = json.loads(summary.read_text(encoding='utf-8')) if summary.exists() else {
            'success': False, 'error': 'runner_report_missing'}
        if not isinstance(report, dict):
            raise ValueError('control_summary_invalid')
        rows = []
        lines = rows_path.read_text(encoding='utf-8').splitlines() if rows_path.exists() else []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                if partial and index == len(lines) - 1:
                    break
                raise
            if not isinstance(row, dict):
                raise ValueError('control_command_result_invalid')
            rows.append(row)
        report['steps'] = rows
        if report.get('failure'):
            report['error'] = report['failure']
        if not partial and summary.exists():
            if report.get('command_count') != len(rows):
                raise ValueError('control_command_result_count_mismatch')
            # Only a final summary certifies the state file belongs to this run.
            if report.get('final_state_file'):
                path = (out / report['final_state_file']).resolve()
                if path != (out / 'snapshots/state.json').resolve():
                    raise ValueError('control_state_path_invalid')
                report['final_state'] = json.loads(path.read_text(encoding='utf-8'))
        return report
    legacy = out / REPORT_NAME
    if legacy.exists():
        return json.loads(legacy.read_text(encoding='utf-8'))
    return {'success': False, 'steps': [], 'error': 'runner_report_missing'}


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
        out = Path(self.workspace.out)
        return out / ('summary.json' if (out / 'summary.json').exists() else REPORT_NAME)

    @property
    def traj_path(self):
        out = Path(self.workspace.out)
        return out / ('trajectories/execution.traj' if (out / 'summary.json').exists()
                      or (out / 'trajectories').exists() else TRAJ_NAME)

    @property
    def final_scene_path(self):
        return Path(self.workspace.out) / 'snapshots/final_scene.json'

    @property
    def artifacts(self):
        out = Path(self.workspace.out)
        return {str(path.relative_to(out)): path for path in
                [*(out / name for name in NEW_ARTIFACTS), out / REPORT_NAME, out / TRAJ_NAME,
                 out / FRAMES_NAME, out / FINAL_STATE_NAME, out / BLACKBOARD_NAME]
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
    if os.name == 'nt':
        # Windows has no POSIX process groups or SIGKILL; Popen.kill uses
        # TerminateProcess for this isolated control child.
        if process.poll() is None:
            process.kill()
        return process.wait()
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
        progress_interval=1.0, child_env=None, collision_model=None):
    if collision_model not in (None, 'proxy', 'precise'):
        raise ValueError('collision_model_invalid')
    argv = [str(python), "-m", "link.control_child",
            "--commands", str(commands_path),
            "--catalog", str(catalog_path),
            "--output", str(workspace.out),
            "--backend", backend,
            "--library", str(library),
            "--object-library-url", str(object_library_url)]
    if collision_model is not None:
        argv += ['--collision-model', collision_model]
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
                        partial = read_report(workspace.out, partial=True)
                    except (OSError, ValueError):
                        continue
                    if isinstance(partial, dict) and partial != seen:
                        seen = partial
                        progress(partial)
    # Keep request inputs, native logs and output together for replay/audit.
    raw = log_path.read_bytes()
    try:
        report = read_report(workspace.out)
    except (OSError, ValueError, TypeError) as exc:
        report = {'success': False, 'steps': [], 'error': 'control_output_invalid',
                  'output_error': str(exc)}
    return ExecutionResult(returncode=returncode, timed_out=timed_out, report=report,
                           workspace=workspace, log_path=log_path,
                           stdout_tail=raw.decode("utf-8", "replace")[-4000:])
