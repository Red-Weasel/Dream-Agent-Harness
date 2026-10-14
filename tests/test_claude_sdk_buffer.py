"""The owner's Claude session of 2026-10-13 failed on a `see` of a page screenshot: "CLIJSONDecodeError: Failed to
decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes". The SDK's default buffer is 1 MiB; a `see`
result is one message holding up to 6 images of up to 8 MB each, base64-encoded."""
import base64
import json

from dream.core.backends import anthropic
from dream.tools import vision


def test_the_buffer_holds_the_largest_see_result():
    largest = vision._MAX_IMAGES * len(base64.b64encode(b"\0" * vision._MAX_BYTES))
    assert anthropic.SDK_MAX_BUFFER > largest + 1024 * 1024        # and the conversation's text around it


def test_both_claude_paths_set_the_buffer():
    main = anthropic.sdk_options(system_prompt="S", mcp_servers={}, preapproved_tool_ids=[], agents={},
                                 can_use_tool=None, model=None, effort=None, cwd=".")
    consult = anthropic.claude_code_options(system_prompt="S", cwd=".", mode=None, model=None, effort=None)
    assert main.max_buffer_size == consult.max_buffer_size == anthropic.SDK_MAX_BUFFER


def _read_one(max_buffer_size, line: str):
    """The SDK's own reader (SubprocessCLITransport.read_messages) over a fake CLI that writes `line` in 64 KB chunks."""
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    class Process:
        async def wait(self):
            return 0

    async def chunks():
        for i in range(0, len(line), 65536):
            yield line[i:i + 65536]

    async def run():
        transport = SubprocessCLITransport(prompt="", options=ClaudeAgentOptions(max_buffer_size=max_buffer_size))
        transport._process, transport._stdout_stream = Process(), chunks()
        return [message async for message in transport.read_messages()]
    return asyncio.run(run())


def test_a_screenshot_sized_message_fails_at_the_sdk_default_and_reads_at_dreams():
    import pytest
    from claude_agent_sdk._errors import CLIJSONDecodeError
    line = json.dumps({"type": "user", "image": "A" * 3_000_000}) + "\n"     # a ~2.2 MB PNG as base64
    with pytest.raises(CLIJSONDecodeError, match="exceeded maximum buffer size of 1048576"):
        _read_one(None, line)
    assert _read_one(anthropic.SDK_MAX_BUFFER, line) == [json.loads(line)]
