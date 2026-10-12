"""Shipped descriptor clones for the isolated local tool-selection fixture.

Metadata construction imports trusted shipped Python modules. It never calls
registry.build, loads executable extensions, or starts an MCP server. Only
explicitly supplied guarded handlers survive; all other executors are refusals.
This configuration boundary is not an OS sandbox or full-catalog qualification.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from claude_agent_sdk import SdkMcpTool

_RESERVED = frozenset({"tool_schema", "tool_lookup", "tool_call", "task", "snip", "fork_verifier_agent"})
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_TOOLS = 256
_MAX_BYTES = 512 * 1024


async def _deny(arguments):
    return {"is_error": True, "content": [{"type": "text", "text":
        "This catalog-selection fixture refuses non-fixture execution. Use its guarded file tools."}]}


def _shipped_tools():
    # Same shipped composition as scripts/harness_report.py. Reading this list
    # imports trusted package modules, but never calls their original handlers.
    from .tools.registry import _BASE_TOOLS
    from .tools.native import NATIVE_TOOLS
    from .tools.demonstration_tools import DEMONSTRATION_TOOLS
    from .tools.capability_tools import CAPABILITY_TOOLS
    return [*_BASE_TOOLS, *NATIVE_TOOLS, *DEMONSTRATION_TOOLS, *CAPABILITY_TOOLS]


def build_tool_catalog(allowed_tools, *, source_tools=None):
    """Copy bounded exact metadata, replacing every non-allowed callable.

    source_tools is a trusted test seam, not a model-facing extension API. Names
    must be unique even ignoring case. Supplied guarded tools must already exist
    in the shipped catalog; they cannot add new tools or backend dispatch hatches.
    """
    sources = _shipped_tools() if source_tools is None else source_tools
    if not isinstance(sources, (list, tuple)) or len(sources) > _MAX_TOOLS:
        raise ValueError("Fixture catalog requires at most 256 tool descriptors")
    allowed_tools = list(allowed_tools)
    if len(allowed_tools) > _MAX_TOOLS:
        raise ValueError("Fixture guarded tool list exceeds its bound")
    seen, descriptors = set(), []
    for source in sources:
        if (not isinstance(source, SdkMcpTool) or not isinstance(source.name, str)
                or not _NAME.fullmatch(source.name) or source.name.lower() in _RESERVED
                or source.name.lower() in seen or not isinstance(source.description, str)
                or not isinstance(source.input_schema, dict)):
            raise ValueError("Fixture catalog contains invalid, duplicate or reserved metadata")
        seen.add(source.name.lower())
        descriptors.append({"name": source.name, "description": source.description,
                            "input_schema": deepcopy(source.input_schema)})
    try:
        encoded = json.dumps(descriptors, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError("Fixture tool metadata must be bounded JSON") from exc
    if len(encoded) > _MAX_BYTES:
        raise ValueError("Fixture tool metadata exceeds 512 KiB")
    names = {source.name for source in sources}
    allowed = {}
    for tool in allowed_tools:
        if (not isinstance(tool, SdkMcpTool) or tool.name not in names
                or tool.name in allowed or not callable(tool.handler)):
            raise ValueError("Guarded fixture tools must be unique existing catalog names")
        allowed[tool.name] = tool.handler
    clones = [SdkMcpTool(name=d["name"], description=d["description"],
                        input_schema=deepcopy(d["input_schema"]),
                        handler=allowed.get(d["name"], _deny)) for d in descriptors]
    return clones, {"kind": "builtin_tools", "count": len(clones),
                    "allowed": [tool.name for tool in clones if tool.name in allowed],
                    "denied": [tool.name for tool in clones if tool.name not in allowed],
                    "metadata_sha256": hashlib.sha256(encoded).hexdigest(),
                    "metadata_bytes": len(encoded), "skills_loaded": False,
                    "scope": "Shipped tool metadata; non-fixture handlers refused. No plugin/MCP/private/skill catalog."}
