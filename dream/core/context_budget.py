"""Admission control for the entire visible request, with explicit estimation.

The server's last usage is evidence about the previous request, not a tokenizer
for newly appended text. We measure the current request and expose that limitation.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence


class ContextOverflow(ValueError):
    pass


# The flat per-image reservation estimate() makes. A backend that has learned the server's
# real per-image count prices images itself from estimate_parts (Dream fix #65).
IMAGE_TOKENS = 4096


def estimate(value: Any) -> int:
    # Visual tokenization is provider-dependent. Reserve a conservative 4096
    # per bounded image rather than counting base64 as textual model input.
    text, images = estimate_parts(value)
    return text + images * IMAGE_TOKENS


def estimate_parts(value: Any) -> tuple[int, int]:
    """(estimated text tokens, image count) of a value: estimate() without pricing the images."""
    images = 0
    def visible(item):
        nonlocal images
        if isinstance(item, dict):
            if item.get("type") == "image_url":
                images += 1
                return {"type": "image_url", "image_url": "[visual input]"}
            return {k: visible(v) for k, v in item.items()}
        if isinstance(item, list):
            return [visible(v) for v in item]
        return item
    text = value if isinstance(value, str) else json.dumps(visible(value), ensure_ascii=False)
    # UTF-8 bytes are more conservative than characters for CJK and source code.
    # This remains an estimate; provider tokenization can still differ.
    return math.ceil(len(text.encode("utf-8")) / 3.5), images


@dataclass(frozen=True)
class ContextReport:
    window: int
    instructions: int
    history: int
    tools: int
    output: int
    margin: int
    method: str = "estimated UTF-8 bytes / 3.5; 4096 tokens per image (provider dependent)"

    @property
    def input_tokens(self) -> int:
        return self.instructions + self.history + self.tools

    @property
    def remaining(self) -> int:
        return self.window - self.input_tokens - self.output - self.margin

    def as_dict(self) -> dict:
        return {**asdict(self), "input_tokens": self.input_tokens, "remaining": self.remaining,
                "admitted": self.remaining >= 0}


def account(messages: Sequence[dict], tools: Sequence[dict], window: int, output: int,
            *, counter: Callable[[Any], int] = estimate, margin: int = 512,
            method: str | None = None) -> ContextReport:
    system = [m for m in messages if m.get("role") in {"system", "developer"}]
    history = [m for m in messages if m.get("role") not in {"system", "developer"}]
    return ContextReport(window, counter(system), counter(history), counter(tools), output,
                         min(margin, max(64, window // 16)),
                         method or ("provider counter" if counter is not estimate else "estimated UTF-8 bytes / 3.5; 4096 tokens per image (provider dependent)"))


def admit(messages: Sequence[dict], tools: Sequence[dict], window: int, output: int,
          *, minimum_output: int = 256, counter: Callable[[Any], int] = estimate,
          method: str | None = None) -> ContextReport:
    report = account(messages, tools, window, output, counter=counter, method=method)
    if report.remaining < 0:
        available = window - report.input_tokens - report.margin
        if available >= minimum_output:
            report = account(messages, tools, window, min(output, available), counter=counter, method=method)
        else:
            raise ContextOverflow(
                f"Context needs about {report.input_tokens + report.margin + minimum_output:,} tokens "
                f"but the usable window is {window:,}. Instructions {report.instructions:,}, "
                f"history {report.history:,}, tools {report.tools:,}. "
                "Choose a leaner profile, shorten the input, or increase the loaded context. "
                "Your instructions and current request were preserved; no request was sent.")
    return report
