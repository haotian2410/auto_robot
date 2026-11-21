#!/usr/bin/env python3
"""A stand-in for ``demo.run_demo`` that records how it was called.

It writes the same artifacts the control side does -- a ``report.json`` that
grows one command at a time, a trajectory, and a ``logs/`` directory the native
library creates relative to the current working directory -- so the runner's own
promises (paths, isolation, read-back, kill on timeout) are testable without a
simulator.  Behaviour comes from the ``FAKE_DEMO`` JSON environment variable.
"""
import argparse
import json
import os
import pathlib
import time

BEHAVIOUR = {"rows": 1, "delay": 0.0, "sleep": 0.0, "exit": 0, "success": True}


def main():
    behaviour = {**BEHAVIOUR, **json.loads(os.environ.get("FAKE_DEMO", "{}"))}
    parser = argparse.ArgumentParser()
    for name in ("commands", "catalog", "output", "backend", "library", "object-library-url"):
        parser.add_argument("--" + name)
    parser.add_argument('--collision-model', choices=('proxy', 'precise'))
    args, extra = parser.parse_known_args()
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    skills = ["approach", "pick", "lift_up", "retreat", "move_to", "approach", "put", "retreat"]
    rows = [{"command_id": str(index), "skill_name": skill, "success": True, "status": "success"}
            for index, skill in enumerate(skills[: behaviour["rows"]], 1)]
    (out / "invocation.json").write_text(json.dumps({
        "argv": list(extra),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "pythonpath": os.environ.get("PYTHONPATH"),
        "library": os.environ.get("SIM_PLATFORM_LIBRARY"),
        "tmpdir": os.environ.get("TMPDIR"),
        "vlm_base_url": os.environ.get("ROBOT_LINK_VLM_BASE_URL"),
        "commands_readable": pathlib.Path(args.commands).is_file(),
        "catalog_readable": pathlib.Path(args.catalog).is_file(),
        "backend": args.backend,
        "collision_model": args.collision_model,
        "object_library_url": getattr(args, "object_library_url"),
        "paths_absolute": all(pathlib.Path(value).is_absolute()
                             for value in (args.commands, args.catalog, args.output, args.library))}),
        encoding="utf-8")
    # The native collision library opens this before Python gets a say.
    (pathlib.Path(os.getcwd()) / "logs").mkdir(exist_ok=True)
    (pathlib.Path(os.getcwd()) / "logs" / "sim_core_20261111_000000.logcompr").write_bytes(b"BQLOG")
    for index in range(1, len(rows) + 1):
        (out / "report.json").write_text(json.dumps({
            "request_id": json.loads(pathlib.Path(args.commands).read_text())["request_id"],
            "success": behaviour["success"] and index == len(rows),
            "steps": rows[:index]}), encoding="utf-8")
        time.sleep(behaviour["delay"])
    if behaviour["sleep"]:
        time.sleep(behaviour["sleep"])
    if behaviour["rows"]:
        (out / "trajectory.traj").write_bytes(b"TRAJ" * behaviour["rows"])
    return behaviour["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
