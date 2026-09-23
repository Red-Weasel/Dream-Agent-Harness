"""Borrowed eyes (DREAM-098): a vision-capable API provider describes images for a session whose own model cannot see.

Only when the owner opts in (the `vision_helper` profile setting / DREAM_VISION_HELPER), because the image leaves the
machine. One single-turn request per `see` call: the image(s) and the model's question -- no session, no tools, no
memory -- and only the provider's words come back. Two wire shapes, both over httpx (the transport Dream's
OpenAI-compatible backend already uses; the Claude backend drives the Claude Agent SDK, which has no Messages call,
and the `anthropic` package is not a dependency of Dream):

- kind "anthropic": POST https://api.anthropic.com/v1/messages with base64 image blocks (x-api-key, anthropic-version);
- kind "openai": POST <base_url>/chat/completions with image_url data-URI parts (Authorization: Bearer).

The model asked is `DREAM_VISION_HELPER_MODEL` when set, else the provider's declared default model, else this module's
default for the kind (`DEFAULT_MODELS`; DREAM-099).

`TRANSPORT` lets tests answer instead of the network; `describe(..., transport=...)` does the same for one call.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from .providers import PROVIDERS, Provider

HELPER_KINDS = ("anthropic", "openai")   # reached over an API; a CLI cannot take one image and answer once
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL_ENV = "DREAM_VISION_HELPER_MODEL"
# When neither the env nor the provider names a model. The anthropic provider declares no default model (the Claude
# Code login picks its own, which a raw Messages request cannot reuse); a description does not need the largest model.
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-4o"}
MAX_TOKENS = 4096                   # a description; on the Claude side the model's own thinking counts against it
TIMEOUT_S = 120.0
DEFAULT_QUESTION = ("Describe this image precisely for someone who cannot see it: the layout, all visible text "
                    "verbatim, the colours, and anything that looks broken, empty or unexpected.")
TRANSPORT: httpx.AsyncBaseTransport | None = None   # tests set an httpx.MockTransport here; None means the network


class HelperError(Exception):
    """The helper produced no description; the message says why, in the provider's own terms."""


def helper_keys() -> list[str]:
    """Provider keys that may describe images: declared multimodal and reached over an API (CLI kinds excluded)."""
    return [key for key, provider in PROVIDERS.items() if provider.kind in HELPER_KINDS and provider.multimodal]


def helper_provider(key: str) -> Provider:
    if key not in helper_keys():
        raise ValueError(f"{key!r} cannot be a vision helper; use one of: {', '.join(helper_keys())}")
    return PROVIDERS[key]


def helper_model(provider: Provider) -> str:
    """The model the description is asked of: DREAM_VISION_HELPER_MODEL when set (for whichever helper is configured),
    else the provider's declared default model, else DEFAULT_MODELS for its kind."""
    return os.environ.get(MODEL_ENV, "").strip() or provider.default_model or DEFAULT_MODELS[provider.kind]


def _api_key(provider: Provider) -> tuple[str, str]:
    """(the key, the environment variable it comes from). Claude's is ANTHROPIC_API_KEY, as for the Anthropic SDK;
    the Claude backend's own sign-in (the Claude Code login) is not reachable from a raw request."""
    if provider.kind == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", ""), "ANTHROPIC_API_KEY"
    return provider.api_key(), provider.api_key_env or "the provider's API key"


def build_request(provider: Provider, images: list[dict[str, Any]], question: str | None
                  ) -> tuple[str, dict[str, str], dict[str, Any]]:
    """(url, headers, body) for the one user turn: every image, then the question. Pure, so tests can pin the shape.

    `images` are MCP image blocks as `see` builds them: {"data": <base64>, "mimeType": <image/...>, ...}.
    """
    text = question.strip() if isinstance(question, str) and question.strip() else DEFAULT_QUESTION
    key, env = _api_key(provider)
    if not key:
        raise HelperError(f"no API key for {provider.label}: set {env}")
    if provider.kind == "anthropic":
        content = [{"type": "image", "source": {"type": "base64", "media_type": img["mimeType"], "data": img["data"]}}
                   for img in images] + [{"type": "text", "text": text}]
        return (ANTHROPIC_MESSAGES_URL,
                {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
                {"model": helper_model(provider), "max_tokens": MAX_TOKENS,
                 "messages": [{"role": "user", "content": content}]})
    content = [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": f"data:{img['mimeType']};base64,{img['data']}", "detail": "auto"}}
        for img in images]
    return ((provider.base_url or "").rstrip("/") + "/chat/completions",
            {"Authorization": f"Bearer {key}", "content-type": "application/json"},
            {"model": helper_model(provider), "max_tokens": MAX_TOKENS,
             "messages": [{"role": "user", "content": content}]})


def reply_text(provider: Provider, data: Any) -> str:
    """The description in a reply, or a HelperError naming what came back instead (a refusal, nothing)."""
    label = provider.label
    if not isinstance(data, dict):
        raise HelperError(f"{label} returned no description")
    if provider.kind == "anthropic":
        if data.get("stop_reason") == "refusal":
            raise HelperError(f"{label} declined to describe the image (stop reason: refusal)")
        blocks = data.get("content") if isinstance(data.get("content"), list) else []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    else:
        choices = data.get("choices") if isinstance(data.get("choices"), list) else []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        if choice.get("finish_reason") == "content_filter":
            raise HelperError(f"{label} declined to describe the image (finish reason: content_filter)")
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        if message.get("refusal"):
            raise HelperError(f"{label} declined to describe the image: {str(message['refusal'])[:200]}")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        text = content if isinstance(content, str) else ""
    text = text.strip()
    if not text:
        raise HelperError(f"{label} returned no description")
    return text


def _detail(response: httpx.Response) -> str:
    """The provider's error message when there is one; never the request (it held the image)."""
    try:
        error = response.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
        if message:
            return str(message)[:300]
    except (ValueError, AttributeError):
        pass
    return response.text[:300].strip() or response.reason_phrase or "no body"


async def describe(images: list[dict[str, Any]], question: str | None, provider: Provider, *,
                   transport: httpx.AsyncBaseTransport | None = None) -> str:
    """One request, one description. Raises HelperError for everything that is not a description: no key, a transport
    failure, an HTTP error status, a refusal, an empty or non-JSON reply."""
    url, headers, body = build_request(provider, images, question)
    label = provider.label
    try:
        async with httpx.AsyncClient(transport=transport if transport is not None else TRANSPORT,
                                     timeout=httpx.Timeout(TIMEOUT_S, connect=15.0)) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise HelperError(f"could not reach {label}: {type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        raise HelperError(f"{label} answered HTTP {response.status_code}: {_detail(response)}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HelperError(f"{label} returned a reply that is not JSON ({response.text[:120]!r})") from exc
    return reply_text(provider, data)
