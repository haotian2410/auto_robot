"""Scratch workspace that the control side reads: catalog + scene + commands.

The control runner resolves every path it reads relative to the directory
holding ``catalog.json``, and its scene registrar writes that file back in
place.  Keeping one throwaway directory per request is therefore both the
isolation mechanism and the reason no upstream file is ever modified.

The runner also compares three copies of the scene identity — catalog entry,
scene file and task document — so the scene document is the single source and
the other two are stamped from it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

SCENE_FILE_NAME = "scene.json"
CATALOG_NAME = "catalog.json"


class ExecutionWorkspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.catalog_path = self.root / CATALOG_NAME
        self.out = self.root / "out"

    @classmethod
    def open(cls, root, request_id):
        ws = cls(Path(root) / str(request_id))
        if ws.root.is_dir():
            shutil.rmtree(ws.root)
        (ws.root / "scenes").mkdir(parents=True, exist_ok=True)
        (ws.out).mkdir(parents=True, exist_ok=True)
        return ws

    def scene_path(self, scene_id):
        return self.root / "scenes" / str(scene_id) / SCENE_FILE_NAME

    def commands_path(self, request_id):
        return self.root / "commands" / str(request_id) / "commands.json"

    def write_scene(self, scene_id, document):
        """Store the browser/OLS document byte-for-byte.

        The control side requires ``scene_id``/``scene_version`` in the file to
        match the catalog entry it is about to write, so the identity is read
        back rather than assumed.
        """
        raw = scene_bytes(document)
        header = json.loads(raw)
        if header.get("scene_id") != scene_id:
            raise ValueError(
                f"scene_document_identity: file says {header.get('scene_id')!r}, task says {scene_id!r}")
        path = self.scene_path(scene_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return header["scene_version"], raw

    def write_catalog(self, asset_root):
        self.catalog_path.write_text(json.dumps(
            {"asset_root": str(Path(asset_root).resolve()), "scenes": {}},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.catalog_path

    def write_commands(self, task):
        path = self.commands_path(task["request_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


DEFAULT_REQUEST_ID = "link-0001"


def scene_bytes(document):
    if isinstance(document, bytes):
        return document
    if isinstance(document, str):
        return document.encode("utf-8")
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def load_task(path):
    task = json.loads(Path(path).read_text(encoding="utf-8"))
    if task.get("schema_version") != "2.0":
        raise ValueError("task_schema_version_invalid")
    if not isinstance(task.get("commands"), list) or not task["commands"]:
        raise ValueError("task_commands_empty")
    return task


def build(workspace_root, *, scene_document, task, asset_root, request_id=None):
    """Materialize one request's workspace; return the workspace and its task."""
    raw = scene_bytes(scene_document)
    header = json.loads(raw)
    identifier = request_id if request_id is not None else task.get("request_id", DEFAULT_REQUEST_ID)
    ws = ExecutionWorkspace.open(workspace_root, identifier)
    document = dict(task)
    document["request_id"] = identifier
    document["scene_id"] = header["scene_id"]
    document["scene_version"] = header["scene_version"]
    ws.write_scene(header["scene_id"], raw)
    ws.write_catalog(asset_root)
    return ws, ws.write_commands(document), document
