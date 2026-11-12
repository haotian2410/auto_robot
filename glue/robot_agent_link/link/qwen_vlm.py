"""Send the control side's perception VLM to a local model server.

Two facts force this into the child process rather than into the Brain. The
control repository is a read-only checkout, and the client it builds for
perception is constructed inside ``VLMPointEstimator`` itself -- an object that
only ever exists on the far side of ``runner.run``.

Redirecting the URL is only half of the job. The control's parser calls
``raw_decode`` on the whole reply, so a response that opens with a Markdown
fence or a sentence is rejected even when the grasp point inside it is
correct. The local Qwen answers that way to every JSON request, so the reply is
cut down to its object here, at the boundary, before the control sees it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

BASE_URL_ENV = "ROBOT_LINK_VLM_BASE_URL"
API_KEY_ENV = "ROBOT_LINK_VLM_API_KEY"
TIMEOUT_ENV = "ROBOT_LINK_VLM_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_API_KEY = "local"


def json_body(text: str) -> str:
    """Return the first JSON object in *text*, prose and fences dropped.

    Every ``{`` is tried in turn because a reasoning model may open with a
    sentence that itself contains braces; the first slice ``raw_decode``
    accepts is the one the control's own parse would have accepted had the
    preamble not been there. The end offset is kept so the closing fence goes
    with it, and unparseable input comes back untouched so the control reports
    the failure in its own words.
    """
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            _, end = decoder.raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        return text[start:start + end]
    return text


@dataclass(frozen=True)
class TextPart:
    type: str = "output_text"
    text: str | None = None


@dataclass(frozen=True)
class ReplyMessage:
    type: str = "message"
    role: str = "assistant"
    content: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class VlmReply:
    """A reply the control can read through either of its two access paths."""

    output_text: str

    @property
    def output(self):
        return (ReplyMessage(content=(TextPart(text=self.output_text),)),)


def reply_text(response) -> str:
    """Flatten an SDK response to the text the control asks for, fences removed.

    The control reads ``output_text`` first and falls back to walking
    ``output[].content[].text``; both are honoured here so a server that fills
    only one of them still works.
    """
    text = getattr(response, "output_text", None)
    if not text:
        text = "".join(part.text for item in getattr(response, "output", None) or []
                       if getattr(item, "type", None) == "message"
                       for part in getattr(item, "content", None) or []
                       if getattr(part, "text", None))
    return json_body(text or "")


class ResponsesAdapter:
    def __init__(self, client):
        self._client = client

    def create(self, *, model=None, input=None, **kwargs):
        response = self._client.sdk.responses.create(
            model=self._client.model or model, input=input, **kwargs)
        return VlmReply(output_text=reply_text(response))


class LocalVlmClient:
    """The small slice of the OpenAI client that the control's one call site uses.

    Only ``responses.create`` is implemented: that is the whole surface
    ``VLMPointEstimator`` touches, and a fuller client would be a second API to
    keep in step with the upstream one.
    """

    def __init__(self, *, base_url, model=None, api_key=DEFAULT_API_KEY,
                 timeout=DEFAULT_TIMEOUT_SECONDS, sdk=None):
        if sdk is None:
            from openai import OpenAI
            sdk = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.base_url, self.model, self.sdk = base_url, model, sdk
        self.responses = ResponsesAdapter(self)


def install(*, base_url=None, model=None, env=None, client=None):
    """Rewire ``VLMPointEstimator`` to *client*; returns ``None`` when nothing is configured.

    Raising on a failed import is deliberate: a renamed control module has to
    be distinguishable from a deployment that simply left the endpoint unset,
    because only the first means the run is about to fail on a dead credential.
    """
    env = os.environ if env is None else env
    base_url = base_url or env.get(BASE_URL_ENV)
    if client is None:
        if not base_url:
            return None
        client = LocalVlmClient(base_url=base_url, model=model,
                                api_key=env.get(API_KEY_ENV, DEFAULT_API_KEY),
                                timeout=float(env.get(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)))
    from atomic_action.perception.vlm_estimate.estimator import VLMPointEstimator

    VLMPointEstimator._get_client = lambda self: client
    return client
