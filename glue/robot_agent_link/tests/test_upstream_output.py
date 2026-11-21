import json
from types import SimpleNamespace

import pytest

from link.runner import ExecutionResult, read_report
from link.workspace import ExecutionWorkspace


def output(tmp_path):
    (tmp_path / 'commands').mkdir()
    (tmp_path / 'snapshots').mkdir()
    (tmp_path / 'trajectories').mkdir()
    (tmp_path / 'summary.json').write_text(json.dumps({
        'success': True, 'request_id': 'r1', 'command_count': 1,
        'final_state_file': 'snapshots/state.json', 'execution_started': True}))
    (tmp_path / 'commands/results.jsonl').write_text(json.dumps({
        'command_id': 'c1', 'step_index': 1, 'success': True}) + '\n')
    (tmp_path / 'snapshots/state.json').write_text('{"objects": {}, "joints": {}}')
    (tmp_path / 'snapshots/final_scene.json').write_text('{"objects": []}')
    (tmp_path / 'trajectories/execution.traj').write_bytes(b'trajectory')


def test_new_report_and_web_artifact_paths(tmp_path):
    output(tmp_path)
    report = read_report(tmp_path)
    assert report['steps'][0]['command_id'] == 'c1'
    assert report['final_state']['objects'] == {}
    result = ExecutionResult(0, False, report, SimpleNamespace(out=tmp_path))
    assert result.succeeded
    assert result.traj_path == tmp_path / 'trajectories/execution.traj'
    assert result.final_scene_path.is_file()
    assert 'snapshots/final_scene.json' in result.artifacts


def test_missing_final_summary_is_not_success(tmp_path):
    output(tmp_path)
    (tmp_path / 'summary.json').unlink()
    report = read_report(tmp_path)
    assert not report['success']
    assert 'final_state' not in report


def test_incomplete_jsonl_only_tolerated_during_progress(tmp_path):
    output(tmp_path)
    with (tmp_path / 'commands/results.jsonl').open('a') as f:
        f.write('{"command_id":')
    assert len(read_report(tmp_path, partial=True)['steps']) == 1
    with pytest.raises(ValueError):
        read_report(tmp_path)


def test_final_state_path_cannot_escape_request(tmp_path):
    output(tmp_path)
    summary = json.loads((tmp_path / 'summary.json').read_text())
    summary['final_state_file'] = '../other-request/state.json'
    (tmp_path / 'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='control_state_path_invalid'):
        read_report(tmp_path)


@pytest.mark.parametrize('identifier', ['../r', '.', '..', '/tmp/r', 'a/b', 'a\\b'])
def test_request_id_cannot_escape(tmp_path, identifier):
    with pytest.raises(ValueError):
        ExecutionWorkspace.open(tmp_path, identifier)
