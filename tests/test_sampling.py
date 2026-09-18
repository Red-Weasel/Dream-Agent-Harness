"""The local-model backstops against runaways: a hard max_tokens ceiling and gentle
anti-loop sampling, both configurable and both present in the request payload."""

from types import SimpleNamespace

import dream.config as config
from dream.core.backends.openai_compat import OpenAICompatBackend


def _backend(provider="machx") -> OpenAICompatBackend:
    p = SimpleNamespace(
        key=provider, label=provider, base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s", tools=[], permission_cb=None
    )


def test_sampling_defaults_present():
    s = OpenAICompatBackend._build_sampling()
    assert s["frequency_penalty"] == 0.3
    assert s["repetition_penalty"] == 1.05
    assert "presence_penalty" not in s  # 0.0 → omitted, never sent as an "off" knob


def test_disabled_knobs_are_omitted(monkeypatch):
    monkeypatch.setattr(config, "FREQUENCY_PENALTY", 0.0)
    monkeypatch.setattr(config, "PRESENCE_PENALTY", 0.0)
    monkeypatch.setattr(config, "REPETITION_PENALTY", 1.0)
    assert OpenAICompatBackend._build_sampling() == {}


def test_max_tokens_is_a_hard_number():
    # The guaranteed stop: an integer ceiling always rides on the request.
    assert isinstance(config.MAX_OUTPUT_TOKENS, int) and config.MAX_OUTPUT_TOKENS > 0


def test_backend_caches_sampling_at_construction():
    b = _backend()
    assert b._sampling["frequency_penalty"] == 0.3
    assert "repetition_penalty" in b._sampling


def test_effort_merges_into_payload_params():
    b = _backend("openai")
    assert b._effort_params() == {}  # none set → nothing added
    b.set_effort("high")
    assert b._effort_params() == {"reasoning_effort": "high"}
    b.set_effort("max")  # clamps to the top of the OpenAI ladder
    assert b._effort_params() == {"reasoning_effort": "xhigh"}
    b.set_effort(None)
    assert b._effort_params() == {}
