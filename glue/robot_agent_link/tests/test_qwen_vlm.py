"""Perception redirection: what the child is told, and what the control receives.

The control side owns two things this bridge must satisfy without being edited:
a client whose only method is ``responses.create``, and a parser that reads the
reply from its first character. The second is why this module exists at all --
a local model answers a JSON request with a Markdown fence, and the control
throws a correct grasp point away because of the three characters in front of
it.
"""
import json
import sys
from types import SimpleNamespace

import pytest
from link.qwen_vlm import BASE_URL_ENV, LocalVlmClient, VlmReply, install, json_body, reply_text


@pytest.fixture(scope="session")
def estimator_class(control_root):
    """The real class the control instantiates, so the contract is not imagined."""
    sys.path.insert(0, str(control_root))
    try:
        from atomic_action.perception.vlm_estimate.estimator import VLMPointEstimator
    except ImportError as exc:  # a simulator dependency this env lacks
        pytest.skip(f"control estimator not importable here: {exc}")
    finally:
        sys.path.remove(str(control_root))
    return VLMPointEstimator


@pytest.mark.parametrize("reply", [
    '```json\n{"grasp_point": {"x": 0.4, "y": 0.6}}\n```',
    '好的，结果如下：\n{"grasp_point": {"x": 0.4, "y": 0.6}}',
    '{"grasp_point": {"x": 0.4, "y": 0.6}}',
    '```\n{.not json.}\n```\n{"grasp_point": {"x": 0.4, "y": 0.6}}',
])
def test_every_shape_a_chat_model_replies_in_yields_the_same_point(reply):
    assert json.loads(json_body(reply)) == {"grasp_point": {"x": 0.4, "y": 0.6}}


def test_prose_with_no_object_at_all_is_passed_unchanged_so_the_control_names_the_failure():
    text = "I could not see the apple."
    assert json_body(text) == text


def test_the_control_parser_rejects_the_fence_and_accepts_the_cut(estimator_class):
    fenced = '```json\n{"grasp_point": {"x": 0.4, "y": 0.6}}\n```'
    with pytest.raises(ValueError, match="must start with a JSON object"):
        estimator_class._parse_point(fenced)
    assert estimator_class._parse_point(json_body(fenced))["x"] == 0.4


def test_the_two_ways_the_control_reads_a_reply_agree():
    fenced = '```json\n{"x": 0.5, "y": 0.5}\n```'
    nested = SimpleNamespace(output_text=None, output=[SimpleNamespace(type="message", content=[
        SimpleNamespace(text=fenced)])])
    assert reply_text(SimpleNamespace(output_text=fenced)) == reply_text(nested) == '{"x": 0.5, "y": 0.5}'


def test_the_reply_is_readable_through_both_of_the_controls_access_paths():
    reply = VlmReply(output_text='{"x": 0.5, "y": 0.5}')
    assert reply.output_text == '{"x": 0.5, "y": 0.5}'
    assert [part.text for item in reply.output if item.type == "message"
            for part in item.content] == ['{"x": 0.5, "y": 0.5}']


class FakeSdk:
    """The OpenAI client, reduced to the one call the estimator makes."""

    def __init__(self, reply='```json\n{"grasp_point": {"x": 0.4, "y": 0.6}}\n```'):
        self.calls, self._reply = [], reply
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self._reply)


def test_the_control_dialect_is_transmitted_and_the_answer_arrives_cut():
    sdk = FakeSdk()
    client = LocalVlmClient(base_url="http://127.0.0.1:8080/v1", sdk=sdk)
    reply = client.responses.create(
        model="qwen3.8-flash",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "抓哪里"}]}])
    assert json.loads(reply.output_text)["grasp_point"] == {"x": 0.4, "y": 0.6}
    # Untouched, because a deployment that names no model must still honour the
    # control's own ``QWEN_MODEL`` default across the hop.
    assert sdk.calls == [{"model": "qwen3.8-flash",
                          "input": [{"role": "user",
                                     "content": [{"type": "input_text", "text": "抓哪里"}]}]}]


def test_the_deployment_can_name_the_model_once_for_every_call():
    sdk = FakeSdk()
    client = LocalVlmClient(base_url="http://127.0.0.1:8080/v1",
                            model="/models/qwen.gguf", sdk=sdk)
    client.responses.create(model="qwen3.8-flash", input=[])
    assert sdk.calls[0]["model"] == "/models/qwen.gguf"


def test_no_endpoint_named_means_no_redirection_and_no_client_built():
    assert install(env={}) is None


def test_the_control_stops_building_its_own_client_once_redirected(estimator_class):
    # Replacing the factory is the whole fix: the control's hard-coded cloud
    # URL and its dead key are never consulted again, by any of the two call
    # sites that construct an estimator.
    client = LocalVlmClient(base_url="http://127.0.0.1:8080/v1", sdk=FakeSdk())
    original = estimator_class._get_client
    try:
        install(env={BASE_URL_ENV: client.base_url}, client=client)
        estimator = estimator_class()
        assert estimator._get_client() is client
        point = estimator_class._parse_point(
            estimator._get_client().responses.create(model=estimator.model, input=[]).output_text)
        assert point["x"] == 0.4
    finally:
        estimator_class._get_client = original
