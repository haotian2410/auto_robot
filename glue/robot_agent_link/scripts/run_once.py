#!/usr/bin/env python3
"""Execute one atomic-skill task on the control side without touching the Brain.

Run from anywhere; every path has an environment default so the same command
works in a shell and later inside the Brain's subprocess adapter.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

LINK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LINK_ROOT))

from link import error_descriptions, feedback  # noqa: E402
from link.qwen_vlm import BASE_URL_ENV  # noqa: E402
from link.runner import run  # noqa: E402
from link.workspace import build, load_task  # noqa: E402

ENV = {
    "python": ("LINK_PYTHON", "/home/cscvlab/miniconda3/envs/robot_exec/bin/python"),
    "control_root": ("LINK_CONTROL_ROOT", "/home/cscvlab/lht/robot_agent_control"),
    "library": ("LINK_LIBRARY", "/home/cscvlab/lht/_thirdparty/SimPlatform-main/build/libsim_platform_c.so"),
    "asset_root": ("LINK_ASSET_ROOT",
                   "/home/cscvlab/lht/webeditor_runtime/ObjectLibraryServer-1.0.8-rel/data"),
    "ols_url": ("LINK_OLS_URL", "http://127.0.0.1:8000"),
    "vlm_base_url": ("LINK_VLM_BASE_URL", "http://127.0.0.1:8080/v1"),
    "workspace_root": ("LINK_WORKSPACE_ROOT", "/home/cscvlab/lht/robot_agent_workspace"),
}


def default(key):
    return os.environ.get(ENV[key][0], ENV[key][1])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, default=LINK_ROOT / "data" / "apple_to_tray.json")
    parser.add_argument("--scene-file", type=Path,
                        default=default("asset_root") + "/scenes/3/ur5_table_sorting.json")
    parser.add_argument("--request-id", default=None)
    for key, help_text in (
            ("python", "interpreter with mujoco/scipy/trimesh/coacd"),
            ("control_root", "checkout of the control implementation"),
            ("library", "SimPlatform native library"),
            ("asset_root", "OLS data directory holding models/ and robots/"),
            ("ols_url", "running ObjectLibraryServer base URL"),
            ("vlm_base_url", "OpenAI-compatible endpoint for the control's grasp/place perception"),
            ("workspace_root", "parent of the per-request scratch directories")):
        parser.add_argument("--" + key.replace("_", "-"), dest=key, default=default(key), help=help_text)
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("LINK_TIMEOUT", 900)))
    parser.add_argument("--backend", default="mujoco")
    return parser.parse_args(argv)


def report_verdict(result, task):
    steps = result.report.get("steps", [])
    wanted = [command["command_id"] for command in task["commands"]]
    got = [str(step.get("command_id")) for step in steps]
    lines = [f"returncode={result.returncode} timed_out={result.timed_out} "
             f"success={result.report.get('success')}"]
    for step in steps:
        lines.append(f"  {step.get('command_id')} {step.get('skill_name')}: {step.get('status')} "
                     f"{step.get('error') or step.get('error_code') or ''}".rstrip())
    missing = [item for item in wanted if item not in got]
    if missing:
        lines.append(f"  missing command_ids: {missing}")
    if not result.report.get("success"):
        # The error message can run to tens of kilobytes of contact pairs; the
        # truncated form is printed below as part of the feedback line.
        error = result.report.get("error")
        lines.append("  report error: " + (str((error or {}).get("stage")) if isinstance(error, dict)
                                           else str(error)))
    final = (result.report.get("final_state") or {}).get("objects") or {}
    for name, pose in sorted(final.items()):
        if name in {"Apple", "Tray", "Basket"}:
            lines.append(f"  {name} -> {pose.get('position')}")
    traj = result.traj_path
    lines.append(f"  trajectory: {traj} bytes={traj.stat().st_size if traj.is_file() else 'MISSING'}")
    lines.append(f"  log: {result.log_path}")
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)
    task = load_task(args.task)
    scene_document = Path(args.scene_file).read_bytes()
    ws, commands_path, task = build(args.workspace_root, scene_document=scene_document,
                                    task=task, asset_root=args.asset_root,
                                    request_id=args.request_id or task["request_id"])
    result = run(ws, commands_path=commands_path, catalog_path=ws.catalog_path, python=args.python,
                 control_root=args.control_root, library=args.library,
                 object_library_url=args.ols_url, timeout_seconds=args.timeout,
                 backend=args.backend,
                 child_env={BASE_URL_ENV: args.vlm_base_url})
    print(f"workspace: {ws.root}")
    print(report_verdict(result, task))
    # The same reduction the Brain reads, so a failure here is debugged once.
    summary = feedback.summarize(result, json.loads(scene_document),
                                 error_descriptions.describe_for(args.control_root))
    print("feedback: " + json.dumps({key: value for key, value in summary.items()
                                     if key != "steps"}, ensure_ascii=False))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
