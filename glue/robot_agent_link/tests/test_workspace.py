"""What the bridge promises the control side, checked against a real run's bytes.

The control resolver requires three copies of one scene identity -- the catalog
entry, the scene file and the commands document -- and refuses the task when any
of them disagrees (``execution/resources.py:159-168``).  The bridge cannot write
the catalog entry itself, because the control's registrar owns that file, so the
guarantee it can give is the one those two then depend on: the scene file is the
browser's document unchanged, and the commands document is stamped from it.
"""
import json
from pathlib import Path

import pytest
from link.workspace import ExecutionWorkspace, build, load_task, scene_bytes

ASSET_ROOT = "/home/cscvlab/lht/webeditor_runtime/ObjectLibraryServer-1.0.8-rel/data"


def materialize(tmp_path, fixtures, **kwargs):
    task = dict(fixtures.json("commands_link_0002.json"))
    task.update(kwargs)
    return build(tmp_path, scene_document=fixtures.bytes("scene3.json"), task=task,
                 asset_root=ASSET_ROOT, request_id=task["request_id"])


def test_the_commands_document_is_stamped_from_the_scene_file(tmp_path, fixtures):
    scene = fixtures.bytes("scene3.json")
    header = json.loads(scene)
    # A task that claims another identity is corrected rather than trusted: the
    # scene file is the single source, so catalog and commands cannot drift from it.
    ws, commands_path, document = materialize(tmp_path, fixtures, scene_id=999, scene_version=42)
    written = json.loads(commands_path.read_text(encoding="utf-8"))
    assert (written["scene_id"], written["scene_version"]) == (header["scene_id"], header["scene_version"])
    assert document == written
    assert ws.scene_path(header["scene_id"]).read_bytes() == scene
    assert {k: v for k, v in written.items() if k != 'scene_file'} == json.loads(fixtures.bytes("commands_link_0002.json"))
    assert (commands_path.parent / written['scene_file']).read_bytes() == scene


def test_one_directory_per_request_and_an_absolute_asset_root(tmp_path, fixtures):
    ws, commands_path, document = materialize(tmp_path, fixtures)
    assert ws.root == tmp_path / document["request_id"]
    assert commands_path == ws.root / "commands.json"
    catalog = json.loads(ws.catalog_path.read_text(encoding="utf-8"))
    # The control joins both asset_root and each scene path against the catalog's
    # own directory, so a relative root would silently read another library.
    assert Path(catalog["asset_root"]).is_absolute()
    assert catalog["asset_root"] == str(Path(ASSET_ROOT).resolve())
    assert catalog["scenes"] == {}
    assert (ws.root / "output").is_dir()


def test_rebuilding_a_request_never_erases_execution_evidence(tmp_path, fixtures):
    ws, _, document = materialize(tmp_path, fixtures)
    (ws.out / "report.json").write_text("stale", encoding="utf-8")
    with pytest.raises(FileExistsError):
        materialize(tmp_path, fixtures)
    assert (ws.out / "report.json").read_text() == 'stale'


def test_the_control_registrar_accepts_the_workspace_it_is_given(tmp_path, fixtures,
                                                                control_catalog_registrar):
    scene = fixtures.bytes("scene3.json")
    ws, commands_path, document = materialize(tmp_path, fixtures)
    catalog = control_catalog_registrar.register_scene(
        ws.catalog_path, ws.root / "scenes", document["scene_id"], document["scene_version"])
    record = catalog["scenes"][str(document["scene_id"])]
    # Byte-identical to the entry the real run wrote, which is the whole point of
    # keeping the scene document unchanged.
    assert record == fixtures.json("catalog_after_registration.json")["scenes"]["3"]
    resolved = (ws.catalog_path.parent / record["path"]).resolve()
    assert resolved.read_bytes() == scene
    assert (ws.catalog_path.parent / catalog["asset_root"] / "models").is_dir()


def test_a_commands_document_that_lies_about_its_version_is_not_registered(
        tmp_path, fixtures, control_catalog_registrar):
    ws, commands_path, document = materialize(tmp_path, fixtures, scene_version=4)
    # The identity is stamped from the scene file, so a task that arrived with a
    # stale version is corrected before the control ever reads it.
    assert document["scene_version"] == json.loads(commands_path.read_text(
        encoding="utf-8"))["scene_version"] == 3
    # A commands document that did lie is the one thing the resolver cannot paper
    # over: it registers a scene only after finding a file that agrees with it.
    # That is why the scene file has to stay the browser's bytes unchanged.
    catalog = control_catalog_registrar.register_scene(
        ws.catalog_path, ws.root / "scenes", document["scene_id"], 4)
    assert catalog["scenes"] == {}


def test_a_scene_document_with_another_identity_is_refused_before_writing(tmp_path, fixtures):
    ws = ExecutionWorkspace.open(tmp_path, "link-x")
    with pytest.raises(ValueError, match="scene_document_identity"):
        ws.write_scene(11, fixtures.bytes("scene3.json"))
    assert not ws.scene_path(11).exists()


def test_only_a_current_task_document_reaches_the_runner(tmp_path):
    path = tmp_path / "task.json"
    valid = json.loads((Path(__file__).with_name("fixtures") / "commands_link_0002.json")
                       .read_text(encoding="utf-8"))
    path.write_text(json.dumps({**valid, "schema_version": "1.0"}), encoding="utf-8")
    with pytest.raises(ValueError, match="task_schema_version_invalid"):
        load_task(path)
    path.write_text(json.dumps({**valid, "commands": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="task_commands_empty"):
        load_task(path)
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert [command["skill_name"] for command in load_task(path)["commands"]][:3] == \
        ["approach", "pick", "lift_up"]
    assert scene_bytes("x") == b"x" and json.loads(scene_bytes(valid)) == valid
