#!/usr/bin/env python3
"""Measure the collider the native library really built for an .obj asset.

The library may fall back to a box when it cannot load a mesh, and that fallback
is invisible in the render window, so every number here comes out of a distance
query against the collision backend.  The collider component's own ``size`` field
is not evidence: it reads 1 1 1 for meshes with or without a real mesh loaded,
which is what made the original failure so quiet.  Run this with the interpreter
that launches the control subprocess.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

LIBRARY = "/home/cscvlab/lht/_thirdparty/SimPlatform-main/build/libsim_platform_c.so"
CONTROL_ROOT = "/home/cscvlab/lht/robot_agent_control"
ASSET_ROOT = "/home/cscvlab/lht/webeditor_runtime/ObjectLibraryServer-1.0.8-rel/data"
DEFAULT_ASSETS = ["robots/4/robot_pedestal.obj", "models/1/apple.obj",
                  "models/12/basket.obj", "models/17/tray.obj"]
WALL = 0.002
SPAN = 8.0
REACH = 2.0


def file_extents(path: Path) -> list[float]:
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    for line in path.read_text(errors="ignore").splitlines():
        if not line.startswith("v "):
            continue
        for index, value in enumerate(line.split()[1:4]):
            number = float(value)
            low[index] = min(low[index], number)
            high[index] = max(high[index], number)
    if high[0] == float("-inf"):
        raise ValueError(f"{path} declares no vertices")
    return [high[index] - low[index] for index in range(3)]


def mapped_as(path: str) -> str | None:
    wanted = Path(path).name
    for line in Path("/proc/self/maps").read_text().splitlines():
        if wanted in line:
            return line.split()[-1]
    return None


def measure(args) -> int:
    library = str(Path(args.library).resolve())
    os.environ["SIM_PLATFORM_LIBRARY"] = library
    sys.path.insert(0, str(Path(args.control_root).resolve()))
    from collision.simplatform_fcl import _load_sdk
    Simulator, Transform = _load_sdk()
    api_version = sys.modules["sim_platform.sdk"].api_version()

    print("library " + library)
    print(f"        sha256={hashlib.sha256(Path(library).read_bytes()).hexdigest()[:16]} "
          f"bytes={Path(library).stat().st_size} mapped_as={mapped_as(library)} "
          f"api_version={api_version}")

    simulator = Simulator(physics="none", collision="fcl",
                          cache_directory=tempfile.mkdtemp(prefix="geometry-cache-"))
    world = simulator.create_world()

    walls = []
    for axis in range(3):
        size = [SPAN, SPAN, WALL]
        size[axis] = WALL
        walls.append(world.create_primitive(f"wall-{axis}", "box", size))
    # A primitive keeps its dimensions in the transform scale, so moving one has
    # to carry that scale: writing the translation alone silently turns a wall
    # into a unit cube.
    scales = [wall.transform.scale for wall in walls]

    def plane(parts, index, sign):
        """Where the body's surface sits along one axis, measured rather than assumed.

        A wall that dwarfs the body is parked at REACH on one side of the origin,
        so the gap it reports is exactly its distance from the supporting plane;
        that works whether or not the mesh is centred on its own origin.
        """
        offset = [0.0, 0.0, 0.0]
        offset[index] = sign * REACH
        walls[index].transform = Transform(translation=tuple(offset),
                                           rotation_wxyz=(1.0, 0.0, 0.0, 0.0),
                                           scale=scales[index])
        gap = min(world.distance(part, walls[index]) for part in parts)
        return REACH - WALL / 2 - gap

    worst = 0.0
    for relative in args.asset:
        path = Path(args.asset_root) / relative
        expected = file_extents(path)
        root = world.load_asset(path)
        listed = tuple(world.editor_entities())
        parents = {item.entity: item.parent for item in listed}

        def beneath(candidate: int) -> bool:
            while candidate and candidate != root.handle:
                candidate = parents.get(candidate)
            return candidate == root.handle

        parts = [world.entity(item.entity) for item in listed
                 if item.collider is not None and beneath(item.entity)]
        if not parts:
            parts = [root]
        measured = [round(plane(parts, axis, 1.0) + plane(parts, axis, -1.0), 4)
                    for axis in range(3)]
        error = max(abs(measured[index] - expected[index]) for index in range(3))
        worst = max(worst, error)
        print(f"{relative:40s} parts={len(parts):4d} mesh={expected} "
              f"collider={measured} error={error:.4f} "
              f"{'ok' if error <= args.tolerance else 'MISMATCH'}")

    simulator.close()
    print(f"verdict={'PASS' if worst <= args.tolerance else 'FAIL'} "
          f"(worst error {worst:.4f} against tolerance {args.tolerance})")
    return 0 if worst <= args.tolerance else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", default=os.environ.get("SIM_PLATFORM_LIBRARY") or LIBRARY)
    parser.add_argument("--control-root", default=CONTROL_ROOT)
    parser.add_argument("--asset-root", default=ASSET_ROOT)
    parser.add_argument("--asset", action="append", dest="asset")
    parser.add_argument("--tolerance", type=float, default=0.02)
    args = parser.parse_args()
    args.asset = args.asset or list(DEFAULT_ASSETS)
    # The native library writes its log directory relative to the current directory.
    os.chdir(tempfile.mkdtemp(prefix="geometry-cwd-"))
    return measure(args)


if __name__ == "__main__":
    raise SystemExit(main())
