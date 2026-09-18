"""Model providers Dream can run on.

Four providers, two transports:
- **anthropic** — Claude, via the Claude Agent SDK (its own backend).
- **machx / openai / grok** — OpenAI-compatible chat-completions APIs, one shared backend.

MachX is the user's from-scratch C++/SYCL engine for Intel Arc; it serves an OpenAI-compatible
API on :11435, so it rides the same backend as OpenAI and Grok — just a different base URL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Provider:
    key: str          # machx | anthropic | openai | xai | codex | grok | gemini
    label: str        # human-facing name for the banner
    kind: str         # "anthropic" (SDK) | "openai" (OpenAI-compatible HTTP) | "cli" (subscription CLI)
    base_url: str | None = None       # for openai-kind providers
    api_key_env: str | None = None    # env var holding the key
    default_api_key: str | None = None  # fallback key (e.g. MachX needs none)
    default_model: str | None = None
    multimodal: bool = False          # whether `see`/images should be offered
    cli_cmd: str | None = None        # for kind="cli": the binary to drive (codex/grok/gemini)

    def api_key(self) -> str:
        if self.default_api_key is not None:
            return self.default_api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""


PROVIDERS: dict[str, Provider] = {
    "machx": Provider(
        key="machx",
        label="MachX",
        kind="openai",
        base_url=os.environ.get("DREAM_MACHX_URL", "http://localhost:11435/v1"),
        default_api_key="not-needed",  # local server ignores the key
        multimodal=False,
    ),
    "anthropic": Provider(
        key="anthropic",
        label="Claude · Anthropic",
        kind="anthropic",
        multimodal=True,
    ),
    "openai": Provider(
        key="openai",
        label="OpenAI",
        kind="openai",
        base_url=os.environ.get("DREAM_OPENAI_URL", "https://api.openai.com/v1"),
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-4o",
        multimodal=True,
    ),
    # xAI over the HTTP API (key-based). The subscription Grok *CLI* is the "grok"
    # cli-kind provider below; this stays for `--provider xai` with an API key.
    "xai": Provider(
        key="xai",
        label="Grok · xAI (API)",
        kind="openai",
        base_url=os.environ.get("DREAM_GROK_URL", "https://api.x.ai/v1"),
        api_key_env="XAI_API_KEY",
        default_model="grok-2-latest",
        multimodal=False,
    ),
    # Subscription coding-agent CLIs driven headless (see backends/cli_agent.py).
    "codex": Provider(key="codex", label="ChatGPT · Codex", kind="cli", cli_cmd="codex"),
    "grok": Provider(key="grok", label="Grok · xAI", kind="cli", cli_cmd="grok"),
    "gemini": Provider(key="gemini", label="Gemini · Google", kind="cli", cli_cmd="gemini"),
}


def get_provider(key: str) -> Provider:
    if key not in PROVIDERS:
        raise ValueError(f"Unknown provider '{key}'. Options: {', '.join(PROVIDERS)}")
    return PROVIDERS[key]
