"""DREAM-098: borrowed eyes -- a vision helper describes images for a model that cannot see.

A session on a text-only local model (MachX serving a model without a vision tower, or with it disabled) refuses
`see`. When the owner has opted in with the `vision_helper` profile setting (env DREAM_VISION_HELPER=<provider key>),
because the image leaves the machine, `see` sends the image(s) and the model's optional question to that provider as
ONE single-turn request (no session, no tools, no memory) and returns "Described by <label>: ..."; the header chip
reads "Vision · borrowed (<label>)" and the Runtime note tells the model its images are described, not seen. Nothing
changes when the setting is unset. No network: every helper call here answers through an httpx.MockTransport.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dream.core import profiles, vision_helper
from dream.core.engine import Engine
from dream.core.profiles import (_validate_overrides, guidance, read_settings, resolve_profile, save_settings,
                                 session_vision)
from dream.core.providers import get_provider
from dream.local.settings import session_options
from dream.tools import vision

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
B64 = base64.b64encode(PNG).decode("ascii")
def borrowed_state(reason, label="OpenAI"):
    """session_vision's answer for a borrowed session: the helper named, and the model's own off reason kept after it
    (DREAM-099, the DREAM-098 gate's note: the chip's tooltip says both)."""
    return {"state": "borrowed", "enabled": False,
            "source": f"Described by {label} (vision helper); the model itself: {reason}", "helper": label}


BORROWED = borrowed_state("MachX capability report")   # a MachX session whose engine report says the model cannot see


def caps(vision):
    features = {"prompt_cache": True} if vision is None else {"prompt_cache": True, "vision": vision}
    return {"schema_version": 1, "supported": True, "architecture": "fixture", "sampling": ["max_tokens"], "load": [],
            "features": features}


def image(name="a.png", data=PNG, mime="image/png"):
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime, "name": name}


def openai_reply(text, finish="stop"):
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}]}


def anthropic_reply(text, stop="end_turn"):
    return {"type": "message", "role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}


def answering(status, payload, seen=None):
    def handler(request):
        if seen is not None:
            seen["request"] = request
            seen["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload) if isinstance(payload, dict) else httpx.Response(status, text=payload)
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "settings_path", lambda: tmp_path / "runtime-settings.json")
    for name in ("DREAM_PROFILE", "DREAM_VISION", "DREAM_VISION_HELPER", "DREAM_VISION_HELPER_MODEL",
                 "DREAM_MACHX_SESSION_OPTIONS", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vision_helper, "TRANSPORT", None)


# ---- (a) the setting: a multimodal API provider, default none, an invalid value is a clear error --------------------

def test_helper_keys_are_the_multimodal_api_providers_only():
    assert vision_helper.helper_keys() == ["anthropic", "openai"]   # codex is multimodal but a CLI; machx/xai/grok/gemini cannot see


def test_the_setting_is_none_unless_set():
    assert resolve_profile(get_provider("machx"), None, model="m").vision_helper is None


@pytest.mark.parametrize("key", ["anthropic", "openai"])
def test_the_env_names_the_helper(monkeypatch, key):
    monkeypatch.setenv("DREAM_VISION_HELPER", key)
    assert resolve_profile(get_provider("machx"), None, model="m").vision_helper == key


@pytest.mark.parametrize("value", ["codex", "grok", "gemini", "machx", "xai", "nope", "OpenAI"])
def test_an_invalid_env_value_is_a_clear_validation_error(monkeypatch, value):
    monkeypatch.setenv("DREAM_VISION_HELPER", value)
    with pytest.raises(ValueError, match="DREAM_VISION_HELPER") as failure:
        resolve_profile(get_provider("machx"), None, model="m")
    text = str(failure.value)
    assert repr(value) in text and "anthropic" in text and "openai" in text
    if value in ("codex", "grok", "gemini"):
        assert "CLI" in text
    if value in ("machx", "xai"):
        assert "multimodal" in text


def test_an_empty_env_value_clears_a_saved_helper(monkeypatch, tmp_path):
    save_settings("auto", {"vision_helper": "openai"})
    assert resolve_profile(get_provider("machx"), None, model="m").vision_helper == "openai"
    monkeypatch.setenv("DREAM_VISION_HELPER", "")
    assert resolve_profile(get_provider("machx"), None, model="m").vision_helper is None


def test_the_override_validates_like_the_env():
    assert _validate_overrides({"vision_helper": "anthropic"}) == {"vision_helper": "anthropic"}
    assert _validate_overrides({"vision_helper": None}) == {"vision_helper": None}
    for bad in ("codex", "machx", "nope", 5, True, ["openai"]):
        with pytest.raises(ValueError, match="vision_helper"):
            _validate_overrides({"vision_helper": bad})
    with pytest.raises(ValueError, match=r"models\['machx:m'\]\.vision_helper"):
        profiles._validate_settings({"models": {"machx:m": {"vision_helper": "grok"}}})


def test_the_setting_round_trips_through_the_settings_file(tmp_path):
    path = tmp_path / "settings.json"
    save_settings("auto", {"vision_helper": "openai"}, path=path)
    assert read_settings(path)["overrides"] == {"vision_helper": "openai"}
    path.write_text(json.dumps({"version": 1, "profile": "auto", "overrides": {"vision_helper": "codex"}}))
    with pytest.raises(ValueError, match="vision_helper"):
        read_settings(path)


def test_an_exact_model_entry_can_name_the_helper():
    save_settings("auto", {})
    settings = read_settings()
    settings["models"] = {"machx:seeing-less": {"vision_helper": "anthropic"}}
    profiles.settings_path().write_text(json.dumps(settings))
    assert resolve_profile(get_provider("machx"), None, model="seeing-less").vision_helper == "anthropic"
    assert resolve_profile(get_provider("machx"), None, model="other").vision_helper is None


# ---- session_vision: the "borrowed" state -------------------------------------------------------------------------

def test_a_session_without_image_input_borrows_eyes_when_a_helper_is_set(monkeypatch):
    machx = get_provider("machx")
    monkeypatch.setenv("DREAM_VISION_HELPER", "openai")
    # off by the engine's report, and unreported: both lean on the helper, and each keeps its own reason in the source
    for reported, reason in ((False, "MachX capability report"),
                             (None, "MachX reported no vision capability for this model")):
        with session_options({}, "fixture", capabilities=caps(reported)):
            assert session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture") == borrowed_state(reason)
    monkeypatch.setenv("DREAM_VISION_HELPER", "anthropic")
    with session_options({}, "fixture", capabilities=caps(False)):
        claude_eyes = session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture")
    assert claude_eyes == borrowed_state("MachX capability report", label="Claude · Anthropic")
    assert claude_eyes["source"] == ("Described by Claude · Anthropic (vision helper); "
                                     "the model itself: MachX capability report")


def test_a_model_that_sees_keeps_its_own_eyes(monkeypatch):
    machx = get_provider("machx")
    monkeypatch.setenv("DREAM_VISION_HELPER", "openai")
    with session_options({}, "fixture", capabilities=caps(True)):
        seeing = session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture")
    assert seeing == {"state": "on", "enabled": True, "source": "MachX capability report"}
    claude = get_provider("anthropic")
    assert session_vision(claude, resolve_profile(claude, None, model="m"), "m")["state"] == "on"


def test_the_owner_setting_still_decides_the_models_own_image_input(monkeypatch):
    machx = get_provider("machx")
    monkeypatch.setenv("DREAM_VISION_HELPER", "openai")
    monkeypatch.setenv("DREAM_VISION", "1")
    with session_options({}, "fixture", capabilities=caps(False)):
        assert session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture") == {
            "state": "on", "enabled": True, "source": "Profile setting"}
    monkeypatch.setenv("DREAM_VISION", "0")
    with session_options({}, "fixture", capabilities=caps(True)):
        assert session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture") == borrowed_state("Profile setting")


def test_a_rejected_image_this_session_also_borrows(monkeypatch):
    claude = get_provider("anthropic")
    monkeypatch.setenv("DREAM_VISION_HELPER", "openai")
    rejected = session_vision(claude, resolve_profile(claude, None, model="m"), "m", image_rejected=True)
    assert rejected == borrowed_state("The model server rejected an image this session")


def test_without_the_setting_the_decisions_are_the_old_ones():
    machx = get_provider("machx")
    with session_options({}, "fixture", capabilities=caps(False)):
        off = session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture")
    assert off == {"state": "off", "enabled": False, "source": "MachX capability report"}
    with session_options({}, "fixture", capabilities=caps(None)):
        unreported = session_vision(machx, resolve_profile(machx, None, model="fixture"), "fixture")
    assert unreported == {"state": "unreported", "enabled": False,
                          "source": "MachX reported no vision capability for this model"}


# ---- (c) the Runtime note tells the model its images are described, not seen -------------------------------------

def test_the_runtime_note_names_the_describing_provider(monkeypatch):
    machx = get_provider("machx")
    monkeypatch.setenv("DREAM_VISION_HELPER", "openai")
    with session_options({}, "fixture", capabilities=caps(False)):
        profile = resolve_profile(machx, None, model="fixture")
        decision = session_vision(machx, profile, "fixture")
    note = guidance(replace(machx, multimodal=False), profile, "fixture", vision=decision)
    assert "Image input is off in this session" in note
    assert "`see`" in note and "OpenAI" in note
    assert "description by OpenAI" in note and "not as your own sight" in note
    assert "leaves this machine" in note
    assert "Reason:" not in note                      # the borrowed sentence replaces the off reason
    assert "measure_image" in note and "visual_check" in note   # the exact-facts tools stay named
    # The plain off note (no helper) keeps DREAM-093/096's words.
    plain = guidance(replace(machx, multimodal=False), resolve_profile(machx, None, model="fixture"), "fixture",
                     vision={"state": "off", "enabled": False, "source": "MachX capability report"})
    assert "Image input is off in this session: you cannot see images, screenshots or rendered frames." in plain
    assert "Reason: MachX capability report." in plain and "OpenAI" not in plain and "description by" not in plain
    seeing = guidance(replace(machx, multimodal=True), profile, "fixture",
                      vision={"state": "on", "enabled": True, "source": "MachX capability report"})
    assert "Image input" not in seeing and "OpenAI" not in seeing


# ---- (b) the helper request: one turn, the images and the question, no tools --------------------------------------

@pytest.mark.asyncio
async def test_the_openai_helper_gets_one_turn_with_the_images_and_the_question(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}
    text = await vision_helper.describe([image("a.png"), image("b.png", b"\x89PNGjpeg", "image/jpeg")],
                                        "Which one is clipped?", get_provider("openai"),
                                        transport=answering(200, openai_reply("The second."), seen))
    assert text == "The second."
    request, body = seen["request"], seen["body"]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions" and request.method == "POST"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-4o"
    assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
    parts = body["messages"][0]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url", "image_url"]
    assert parts[0]["text"] == "Which one is clipped?"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + B64
    assert parts[2]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"\x89PNGjpeg").decode()
    for absent in ("tools", "tool_choice", "functions", "stream"):
        assert absent not in body


@pytest.mark.asyncio
async def test_the_anthropic_helper_gets_one_messages_request_with_image_blocks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    seen = {}
    text = await vision_helper.describe([image("shot.png")], "What does the header say?", get_provider("anthropic"),
                                        transport=answering(200, anthropic_reply("It says DREAM."), seen))
    assert text == "It says DREAM."
    request, body = seen["request"], seen["body"]
    assert str(request.url) == "https://api.anthropic.com/v1/messages" and request.method == "POST"
    assert request.headers["x-api-key"] == "sk-ant-test" and request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    assert body["model"] == "claude-sonnet-5" and isinstance(body["max_tokens"], int)   # the default (DREAM-099)
    assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}},
        {"type": "text", "text": "What does the header say?"}]
    for absent in ("tools", "tool_choice", "system", "stream"):
        assert absent not in body


@pytest.mark.asyncio
async def test_without_a_question_the_helper_is_asked_to_describe(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}
    await vision_helper.describe([image()], None, get_provider("openai"), transport=answering(200, openai_reply("x"), seen))
    prompt = seen["body"]["messages"][0]["content"][0]["text"]
    assert prompt == vision_helper.DEFAULT_QUESTION and "escribe" in prompt


@pytest.mark.asyncio
async def test_a_parts_style_openai_reply_is_read(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reply = {"choices": [{"message": {"content": [{"type": "text", "text": "A "}, {"type": "text", "text": "square."}]},
                         "finish_reason": "stop"}]}
    assert await vision_helper.describe([image()], None, get_provider("openai"), transport=answering(200, reply)) == "A square."


# ---- (d) failures are clear errors: no key, HTTP error, network, refusal, no description --------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("key,env", [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
async def test_no_key_fails_before_any_request(key, env):
    def handler(request):
        raise AssertionError("no request may leave without a key")
    with pytest.raises(vision_helper.HelperError, match=env) as failure:
        await vision_helper.describe([image()], None, get_provider(key), transport=httpx.MockTransport(handler))
    assert get_provider(key).label in str(failure.value)


@pytest.mark.asyncio
async def test_http_errors_carry_the_status_and_the_servers_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(vision_helper.HelperError, match="HTTP 401") as failure:
        await vision_helper.describe([image()], None, get_provider("openai"),
                                     transport=answering(401, {"error": {"message": "Incorrect API key provided"}}))
    assert "Incorrect API key provided" in str(failure.value) and "OpenAI" in str(failure.value)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with pytest.raises(vision_helper.HelperError, match="HTTP 529"):
        await vision_helper.describe([image()], None, get_provider("anthropic"), transport=answering(529, "<html>overloaded</html>"))


@pytest.mark.asyncio
async def test_a_network_failure_is_a_helper_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)
    with pytest.raises(vision_helper.HelperError, match="ConnectError") as failure:
        await vision_helper.describe([image()], None, get_provider("openai"), transport=httpx.MockTransport(handler))
    assert "OpenAI" in str(failure.value)


@pytest.mark.asyncio
async def test_a_refusal_or_an_empty_reply_is_a_helper_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with pytest.raises(vision_helper.HelperError, match="refusal"):
        await vision_helper.describe([image()], None, get_provider("anthropic"),
                                     transport=answering(200, anthropic_reply("", stop="refusal")))
    with pytest.raises(vision_helper.HelperError, match="content_filter"):
        await vision_helper.describe([image()], None, get_provider("openai"),
                                     transport=answering(200, openai_reply(None, finish="content_filter")))
    with pytest.raises(vision_helper.HelperError, match="no description"):
        await vision_helper.describe([image()], None, get_provider("openai"), transport=answering(200, openai_reply("")))
    with pytest.raises(vision_helper.HelperError, match="no description"):
        await vision_helper.describe([image()], None, get_provider("openai"), transport=answering(200, {"choices": []}))
    with pytest.raises(vision_helper.HelperError, match="not JSON"):
        await vision_helper.describe([image()], None, get_provider("openai"), transport=answering(200, "<html>login</html>"))


def test_only_a_multimodal_api_provider_can_be_a_helper():
    assert vision_helper.helper_provider("openai").label == "OpenAI"
    for key in ("codex", "machx", "nope"):
        with pytest.raises(ValueError, match="vision helper"):
            vision_helper.helper_provider(key)


# ---- DREAM-099 (the DREAM-098 gate's note): the helper's model is configurable ------------------------------------

def test_the_helper_model_is_configurable(monkeypatch):
    anthropic, openai = get_provider("anthropic"), get_provider("openai")
    assert vision_helper.helper_model(anthropic) == "claude-sonnet-5"   # the anthropic provider declares no default model
    assert vision_helper.helper_model(openai) == "gpt-4o"               # the provider's own default
    assert vision_helper.helper_model(replace(anthropic, default_model="claude-opus-5")) == "claude-opus-5"
    monkeypatch.setenv("DREAM_VISION_HELPER_MODEL", "claude-opus-5")
    assert vision_helper.helper_model(anthropic) == "claude-opus-5"     # the env names the model for whichever helper is set
    assert vision_helper.helper_model(openai) == "claude-opus-5"
    monkeypatch.setenv("DREAM_VISION_HELPER_MODEL", "  ")
    assert vision_helper.helper_model(openai) == "gpt-4o"               # blank counts as unset


@pytest.mark.asyncio
async def test_the_env_model_reaches_the_request(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DREAM_VISION_HELPER_MODEL", "claude-opus-5")
    seen = {}
    await vision_helper.describe([image()], None, get_provider("anthropic"),
                                 transport=answering(200, anthropic_reply("x"), seen))
    assert seen["body"]["model"] == "claude-opus-5"
    monkeypatch.setenv("DREAM_VISION_HELPER_MODEL", "gpt-4.1")
    await vision_helper.describe([image()], None, get_provider("openai"), transport=answering(200, openai_reply("x"), seen))
    assert seen["body"]["model"] == "gpt-4.1"


# ---- `see` in a borrowed session: the tool sends the pixels to the helper, never to the model ----------------------

@pytest.fixture
def borrowed(tmp_path, monkeypatch):
    shots = []
    for name in ("shot.png", "other.png"):
        shot = tmp_path / name
        shot.write_bytes(PNG)
        shots.append(shot)

    class Ctx:
        multimodal = False
        vision_helper = "openai"
        workspace = tmp_path
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return shots


@pytest.mark.asyncio
async def test_see_returns_the_helpers_description_and_no_pixels(borrowed, monkeypatch):
    seen = {}
    monkeypatch.setattr(vision_helper, "TRANSPORT", answering(200, openai_reply("A one-pixel square."), seen))
    result = await vision.see.handler({"path": str(borrowed[0]), "question": "What is it?"})
    assert not result.get("is_error")
    assert [block["type"] for block in result["content"]] == ["text"]
    assert result["content"][0]["text"] == "Described by OpenAI: A one-pixel square."
    parts = seen["body"]["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "What is it?"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + B64    # the bytes went to the helper ...
    assert B64 not in json.dumps(result)                                       # ... and not into the model's result


@pytest.mark.asyncio
async def test_see_sends_several_images_in_one_request(borrowed, monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=openai_reply("Two squares."))
    monkeypatch.setattr(vision_helper, "TRANSPORT", httpx.MockTransport(handler))
    result = await vision.see.handler({"paths": [str(borrowed[0]), str(borrowed[1])]})
    assert result["content"][0]["text"] == "Described by OpenAI: Two squares."
    assert len(calls) == 1
    assert [part["type"] for part in calls[0]["messages"][0]["content"]] == ["text", "image_url", "image_url"]
    assert calls[0]["messages"][0]["content"][0]["text"] == vision_helper.DEFAULT_QUESTION


@pytest.mark.asyncio
async def test_a_helper_failure_is_a_tool_error_the_session_survives(borrowed, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    result = await vision.see.handler({"path": str(borrowed[0])})
    assert result.get("is_error")
    text = result["content"][0]["text"]
    assert "OpenAI" in text and "OPENAI_API_KEY" in text and "unverified" in text and "visual_check" in text
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(vision_helper, "TRANSPORT", answering(500, {"error": {"message": "server exploded"}}))
    result = await vision.see.handler({"path": str(borrowed[0])})
    assert result.get("is_error") and "HTTP 500" in result["content"][0]["text"]
    assert "server exploded" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_an_unexpected_transport_exception_is_the_same_tool_error(borrowed, monkeypatch):
    # The DREAM-098 gate's note: a transport exception that is not an httpx.HTTPError escaped `see` without the
    # "unverified" wording (DREAM-099).
    def handler(request):
        raise RuntimeError("socket vanished")
    monkeypatch.setattr(vision_helper, "TRANSPORT", httpx.MockTransport(handler))
    result = await vision.see.handler({"path": str(borrowed[0])})
    assert result.get("is_error")
    text = result["content"][0]["text"]
    assert "Vision helper OpenAI could not describe shot.png" in text
    assert "RuntimeError: socket vanished" in text and "unverified" in text and "visual_check" in text


@pytest.mark.asyncio
async def test_see_still_checks_the_files_before_asking_the_helper(borrowed, monkeypatch, tmp_path):
    def handler(request):
        raise AssertionError("no request may leave for a missing file")
    monkeypatch.setattr(vision_helper, "TRANSPORT", httpx.MockTransport(handler))
    result = await vision.see.handler({"path": str(tmp_path / "nope.png")})
    assert result.get("is_error") and "nope.png" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_without_a_helper_see_refuses_as_before(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)

    class Ctx:
        multimodal = False
        vision_helper = None
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())
    result = await vision.see.handler({"path": str(shot)})
    assert result.get("is_error") and "Image input is disabled for this session" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_a_seeing_session_ignores_the_helper(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)

    class Ctx:
        multimodal = True
        vision_helper = "openai"
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())

    def handler(request):
        raise AssertionError("a model that sees gets the pixels itself")
    monkeypatch.setattr(vision_helper, "TRANSPORT", httpx.MockTransport(handler))
    result = await vision.see.handler({"path": str(shot), "question": "ignored"})
    assert [block["type"] for block in result["content"]] == ["image", "text"]


def test_the_schema_offers_a_question():
    props = vision.see.input_schema["properties"]
    assert props["question"]["type"] == "string"
    assert "paths" in props and "path" in props
    assert "required" not in vision.see.input_schema or vision.see.input_schema["required"] == []


# ---- the session wiring: engine decision, HTTP backend offers `see`, tool context carries the helper ---------------

def test_an_engine_session_reports_borrowed_and_keeps_images_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAM_VISION_HELPER", "openai")
    with session_options({}, "fixture", capabilities=caps(False)):
        engine = Engine(provider="machx", model="fixture", workspace=tmp_path)
        assert engine.provider.multimodal is False
        assert engine.vision_status() == BORROWED      # the engine report says this model cannot see
    # vision_status() re-derives the decision when asked; with the session options gone the model's own state is
    # "unreported", and the borrowed source carries that reason instead (DREAM-099).
    assert engine.vision_status() == borrowed_state("MachX reported no vision capability for this model")
    note = guidance(engine.provider, engine.profile, engine.model, vision=engine._vision)
    assert "description by OpenAI" in note


def test_the_http_backend_offers_see_when_a_helper_answers():
    from dream.core.backends.openai_compat import OpenAICompatBackend

    async def handler(args):
        raise AssertionError("not called here")
    see = SimpleNamespace(name="see", description="fixture", input_schema={"type": "object", "properties": {}}, handler=handler)
    other = SimpleNamespace(name="read_file", description="fixture", input_schema={"type": "object", "properties": {}}, handler=handler)
    provider = SimpleNamespace(key="machx", label="fixture", base_url="http://fixture.invalid/v1", multimodal=False)
    base = resolve_profile(get_provider("machx"), None, model="m")
    with_helper = OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=[see, other], permission_cb=None,
                                      profile=replace(base, vision_helper="openai"))
    assert "see" in with_helper.tools_by_name and with_helper.provider.multimodal is False
    assert any(schema["function"]["name"] == "see" for schema in with_helper.tool_schemas)
    with_helper._configure_tool_images()   # the lifecycle switch keeps `see` and keeps images off
    assert "see" in with_helper.tools_by_name and with_helper.provider.multimodal is False
    # no engine report for model "m" in this test: the model's own state is "unreported", and the source keeps that reason
    assert with_helper.vision_status() == borrowed_state("MachX reported no vision capability for this model")
    without = OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=[see, other], permission_cb=None,
                                  profile=base)
    assert "see" not in without.tools_by_name
    without._configure_tool_images()
    assert "see" not in without.tools_by_name


def test_the_tool_context_carries_the_helper():
    from dream.tools.context import ToolContext
    assert ToolContext(None, None, None, "s").vision_helper is None
    assert ToolContext(None, None, None, "s", vision_helper="openai").vision_helper == "openai"


# ---- (c) the header chip: "Vision · borrowed (<label>)" with the source in its tooltip ------------------------------

@pytest.mark.asyncio
async def test_the_header_chip_reads_borrowed_with_the_provider(monkeypatch, tmp_path):
    from playwright.async_api import async_playwright, expect
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    session = {"workspace": str(tmp_path), "model": "fixture", "session_id": "fixture-session", "vision": BORROWED}
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session=session)
    url = await srv.start()
    shots = Path(os.environ.get("DREAM_TEST_SHOT_DIR", tmp_path))
    shots.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 2554, "height": 1338}, reduced_motion="reduce")
            await page.goto(url + "&companion=1")
            chip = page.locator("#dream-vision")
            await expect(chip).to_have_text("Vision · borrowed (OpenAI)", timeout=6000)
            assert await chip.get_attribute("data-state") == "borrowed"
            title = await chip.get_attribute("title")
            assert "Described by OpenAI (vision helper)" in title
            assert "the model itself: MachX capability report" in title   # the tooltip says both (DREAM-099)
            await page.screenshot(path=str(shots / "borrowed-chip-2554x1338.png"))
            await browser.close()
    finally:
        await srv.stop()
