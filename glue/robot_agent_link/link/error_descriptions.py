"""Reuse the control's own failure wording instead of inventing a second dialect.

``demo/error_messages.py`` turns a report error payload into a sentence that keeps
the measured evidence -- "apple 与 table 穿透 3.142 mm" rather than a collision
object repr -- and ``demo/run_demo.py`` pairs every error code and failure stage
with a short Chinese gloss.  Both are read here rather than imported, because the
control package pulls in MuJoCo, FCL and a scene registry at import time and only
the child process may load it; the Brain needs the same sentence for a report it
reads after that child is gone.

A missing or restructured checkout degrades to the raw report message, never to a
crash: this is presentation, and a broken gloss must not hide the failure itself.
"""
from __future__ import annotations

import ast
import importlib.util
import threading
from pathlib import Path

MESSAGE_FILE = Path("demo") / "error_messages.py"
GLOSS_FILE = Path("demo") / "run_demo.py"
ERROR_GLOSS = "ERROR_DESCRIPTIONS"
STAGE_GLOSS = "STAGE_DESCRIPTIONS"
NO_REASON = "步骤执行失败"

_cache: dict[str, object] = {}
_lock = threading.Lock()


def _read_glosses(source: Path) -> tuple[dict, dict]:
    """Lift the two literal dicts out of the child's module without running it."""
    glosses: dict[str, dict] = {ERROR_GLOSS: {}, STAGE_GLOSS: {}}
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in glosses:
                glosses[target.id] = ast.literal_eval(node.value)
    return glosses[ERROR_GLOSS], glosses[STAGE_GLOSS]


def _load(control_root: Path):
    module_file = control_root / MESSAGE_FILE
    spec = importlib.util.spec_from_file_location("control_error_messages", module_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.describe_error, _read_glosses(control_root / GLOSS_FILE)


def _describe_factory(control_root: str):
    describe_error, (error_gloss, stage_gloss) = _load(Path(control_root).resolve())

    def describe(payload) -> str:
        """The child's own progress line, minus its "error: " prefix."""
        error = payload if isinstance(payload, dict) else {}
        code = error.get("error_code")
        gloss = (error_gloss.get(code) if code else None) \
            or stage_gloss.get(error.get("failed_stage")) or NO_REASON
        text = describe_error(error, gloss)
        return f"{text}（{code}）" if code else text

    return describe


def describe_for(control_root: str | None):
    """A cached ``payload -> 中文`` callable, or None when the control cannot be read."""
    if not control_root:
        return None
    key = str(Path(control_root).resolve())
    with _lock:
        if key not in _cache:
            try:
                _cache[key] = _describe_factory(key)
            except Exception:  # noqa: BLE001 - a missing or restructured checkout only costs the gloss
                _cache[key] = None
    return _cache[key]
