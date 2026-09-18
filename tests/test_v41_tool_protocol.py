"""Optional CPU integration against the built engine's native V4.1 protocol bridge.

Set DREAM_DS41_PROTOCOL_FIXTURE to build/tools/ie-ds41-protocol-fixture.
No GPU, model load, real file tool, or network connection is used.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_backend_resilience import _backend, _tool, _ScriptedClient

BINARY = os.environ.get("DREAM_DS41_PROTOCOL_FIXTURE")
pytestmark = pytest.mark.skipif(not BINARY, reason="requires built V4.1 CPU protocol fixture")
EOS = "<｜end▁of▁sentence｜>"
CALL = ('reason</think>Checking.\n\n<｜DSML｜ calls>\n'
        '<｜DSML｜ invoke name="read_file">\n'
        '<｜DSML｜ parameter name="path" string="true">README.md</｜DSML｜ parameter>\n'
        '</｜DSML｜ invoke>\n</｜DSML｜ calls>' + EOS)


def engine(case):
    assert Path(BINARY).is_file()
    proc = subprocess.run([BINARY], input=json.dumps(case) + "\n", text=True,
                          capture_output=True, timeout=10, check=True)
    return json.loads(proc.stdout)


async def test_native_call_executes_once_and_tool_history_reencodes():
    effects = []

    async def read(args):
        effects.append(args)
        return {"content": [{"type": "text", "text": "fixture README contents"}]}

    tool = _tool("read_file", read)
    tool.input_schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    backend = _backend([tool])
    backend.model = "DeepSeek-V4.1-Flash"
    first = engine({"completion": CALL, "thinking": True})
    final = engine({"completion": "Read the file.</think>The README is available." + EOS, "thinking": True})
    backend._client = _ScriptedClient([first["sse"].splitlines(), final["sse"].splitlines()])
    events = [event async for event in backend.ask("Read README.md")]
    assert effects == [{"path": "README.md"}]
    assert backend._client.attempts == 2
    assert not next(e.data for e in events if e.kind == "result")["is_error"]
    assert all("<｜DSML｜" not in e.data for e in events if e.kind == "text_delta")
    payload = backend._client.payloads[1]
    assert payload["tools"]
    encoded = engine({"request": payload})
    assert not encoded["error"]
    assert '<｜DSML｜ invoke name="read_file">' in encoded["prompt"]
    assert "<tool_result>" in encoded["prompt"]
    assert "fixture README contents" in encoded["prompt"]
    assert "<tool_call>" not in encoded["prompt"]


@pytest.mark.parametrize("completion", [CALL[:-len(EOS)], CALL.replace('string="true"', 'string="false"')])
async def test_invalid_native_calls_never_execute(completion):
    effects = []

    async def read(args):
        effects.append(args)
        return {}

    backend = _backend([_tool("read_file", read)])
    failed = engine({"completion": completion, "thinking": True})
    assert failed["error"] and not failed["tool_calls"]
    backend._client = _ScriptedClient([failed["sse"].splitlines()])
    events = [e async for e in backend.ask("Read README.md")]
    assert effects == []
    assert next(e.data for e in events if e.kind == "result")["is_error"]
