"""Offline contract tests for the bridge: no simulator, no browser, no network.

``link`` is deliberately dependency-free, so these run under any Python 3.10+
interpreter.  The recorded control artifacts under ``fixtures/`` are the bytes a
real run left behind, which is what makes an assertion about the control side's
own expectations possible without launching it.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

LINK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LINK_ROOT))

# The control checkout is a sibling project, not a dependency of these tests.
CONTROL_ROOT = Path("/home/cscvlab/lht/robot_agent_control")
FIXTURE_DIR = Path(__file__).with_name("fixtures")


@pytest.fixture(scope="session")
def fixtures():
    class Fixtures:
        directory = FIXTURE_DIR

        @staticmethod
        def path(name):
            path = FIXTURE_DIR / name
            if not path.is_file():
                pytest.fail(f"missing recorded fixture {path}")
            return path

        @classmethod
        def json(cls, name):
            return json.loads(cls.path(name).read_text(encoding="utf-8"))

        @classmethod
        def bytes(cls, name):
            return cls.path(name).read_bytes()

    return Fixtures


@pytest.fixture(scope="session")
def control_root():
    """The read-only control checkout, when this machine has one."""
    if not CONTROL_ROOT.is_dir():
        pytest.skip(f"control checkout not at {CONTROL_ROOT}")
    return CONTROL_ROOT


@pytest.fixture(scope="session")
def control_catalog_registrar():
    """The control scene registrar, loaded straight from its file.

    ``execution/__init__.py`` imports scipy, so the package import would drag the
    whole simulator in; the module itself is stdlib only, and it is the piece that
    decides whether a workspace is readable.
    """
    path = CONTROL_ROOT / "execution" / "scene_catalog.py"
    if not path.is_file():
        pytest.skip(f"control checkout not at {CONTROL_ROOT}")
    spec = importlib.util.spec_from_file_location("control_scene_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
