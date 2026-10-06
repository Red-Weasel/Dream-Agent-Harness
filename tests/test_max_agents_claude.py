"""Max agents on a Claude-led session (the owner's settings plan of 2026-10-01).

The Claude SDK runs the lead's sub-agents itself. Read from the bundled CLI (claude-agent-sdk 0.2.152, CLI 2.1.259):
the Agent tool's own permission check allows a launch without asking the host in Dream's "default" permission mode, so
Dream's can_use_tool never sees one -- unless the session's settings carry an "ask" rule for it. With the owner's
limit set when the session starts, they do (Agent; Task its older name), and the backend's permission callback holds
each launch of the lead's in line while `limit` of them run: it goes when an earlier one's result comes back to the
lead, or the turn ends. Never refused for count. Without a limit nothing changes: no rule, no hold. The model is told
the limit in its system prompt.

The CLI's side is a fake modelled on what the bundle does (its streaming tool executor): the lead's Agent calls run
each in a task of its own (the Agent tool is concurrency-safe), each asks can_use_tool first -- as the SDK spawns each
request handler on its own task -- and a call's result is yielded to the stream as soon as it finishes. No CLI, no
model, no network.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import aclosing

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, TaskNotificationMessage, ToolResultBlock, \
    UserMessage
from claude_agent_sdk.types import ToolPermissionContext

from dream.core import settings
from dream.core.backends import anthropic as anthropic_module
from dream.core.backends.anthropic import AnthropicBackend, sdk_options
from dream.core.profiles import settings_path
from test_claude_subagent_cards import launch, lead_gets, nothing_external, rows_of, turn_done  # noqa: F401

SAID = ("\n\n## Sub-agents at once\nAt most {n} of your sub-agents (the Agent tool) run at the same time in this "
        "session: the owner's limit. Launch as many as the job needs; the others wait their turn and start as earlier "
        "ones finish.")


def _save(limit):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, **({"nested": {"max_agents": limit}} if limit else {})}), encoding="utf-8")


def _backend(events=None, permission_cb=None):
    return AnthropicBackend(system_prompt="fixture", mcp_server={}, preapproved_tool_ids=[], agents=None,
                            permission_cb=permission_cb, model="claude-lead", cwd="/tmp",
                            activity_emit=None if events is None else events.append)


class FakeCli:
    """The CLI's side of one lead turn that launches `calls` (tool use ids) with the Agent tool. It asks Dream's
    can_use_tool before a launch only when the session's settings carry the ask rule (a limit when it started), as the
    bundled CLI does; otherwise the Agent tool's own check allows it."""

    def __init__(self, backend, calls, *, hold=0.03, early=False, nested=()):
        self.backend, self.calls, self.hold, self.early, self.nested = backend, calls, hold, early, set(nested)
        self.asks = "permissions" in json.loads(backend._build_options().settings)
        self.running = self.peak = 0
        self.started, self.decisions = [], {}
        self._results: asyncio.Queue = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = {}

    async def query(self, prompt):
        pass

    async def interrupt(self):
        pass

    def _spawn(self):
        self.tasks = {call: asyncio.create_task(self._run(call)) for call in self.calls}

    async def receive_response(self):
        if self.early:                    # the permission requests may come before Dream reads the launching reply
            self._spawn()
            await asyncio.sleep(0)
        yield launch(*[(call, "researcher", "Agent") for call in self.calls])
        if not self.early:
            self._spawn()
        for _ in self.calls:
            yield await self._results.get()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        yield turn_done()

    async def _run(self, call):
        context = ToolPermissionContext(tool_use_id=call, agent_id="agent-1" if call in self.nested else None)
        decision = self.decisions[call] = (await self.backend._permission_handler(
            "Agent", {"subagent_type": "researcher", "description": "fixture", "prompt": "work"}, context)
            if self.asks else PermissionResultAllow())
        if not isinstance(decision, PermissionResultAllow):
            await self._results.put(lead_gets(call, "denied", is_error=True))
            return
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.started.append(call)
        await asyncio.sleep(self.hold)
        self.running -= 1
        await self._results.put(lead_gets(call, f"did {call}"))


async def _turn(backend):
    async with aclosing(backend.ask("go")) as events:
        return [event async for event in events]


def _calls(n):
    return [f"toolu_{i:02d}" for i in range(1, n + 1)]


# --- the options ---------------------------------------------------------------------------------------------------

def _options(**kw):
    return sdk_options(system_prompt="s", mcp_servers={}, preapproved_tool_ids=[], agents=None, can_use_tool=None,
                       model=None, effort=None, cwd="/tmp", **kw)


def test_without_a_limit_the_options_are_todays_byte_for_byte():
    assert _options().settings == '{"disableAllHooks":true}'
    backend = _backend()
    backend._agent_slots = anthropic_module._agent_slots()
    assert backend._agent_slots is None
    options = backend._build_options()
    assert options.settings == '{"disableAllHooks":true}' and options.system_prompt == "fixture"


def test_with_a_limit_the_cli_asks_dream_before_each_launch_and_the_model_is_told():
    assert json.loads(_options(agent_limit=3).settings) == {"disableAllHooks": True,
                                                             "permissions": {"ask": ["Agent", "Task"]}}
    _save(3)
    backend = _backend()
    backend._agent_slots = anthropic_module._agent_slots()
    options = backend._build_options()
    assert json.loads(options.settings)["permissions"] == {"ask": ["Agent", "Task"]}
    assert options.system_prompt == "fixture" + SAID.format(n=3)
    assert options.permission_mode == "default" and options.can_use_tool == backend._permission_handler


def test_the_consultations_never_get_the_rule():
    """Council and review consultations build their options with sdk_options too: no limit is passed there."""
    from dream.core.backends.anthropic import consult_options
    _save(2)
    options = consult_options(system_prompt="s", cwd="/tmp", mode="ask", model=None, effort=None)
    assert "permissions" not in json.loads(options.settings)


# --- the hold --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("early", [False, True])
async def test_launches_past_the_limit_wait_in_line_and_every_one_runs(early):
    _save(2)
    events = []
    backend = _backend(events)
    backend._agent_slots = anthropic_module._agent_slots()
    cli = backend.client = FakeCli(backend, _calls(6), early=early)
    lead = await asyncio.wait_for(_turn(backend), 10)
    assert cli.peak == 2 and sorted(cli.started) == _calls(6), "two at a time, and every launch ran"
    assert all(isinstance(d, PermissionResultAllow) for d in cli.decisions.values()), "none refused"
    assert [e.data["content"] for e in lead if e.kind == "tool_result"] == [f"did {c}" for c in cli.started]
    rows = rows_of(events)
    queued = [r for r in rows if r["kind"] == "status" and r.get("status") == "queued"]
    assert len(queued) == 4 and queued[0]["text"] == "Waiting its turn (2 of 2 running at once: your max agents)"
    for call in _calls(6):                                  # each card that waited went on to run and to finish
        statuses = [r["status"] for r in rows_of(events, call) if r["kind"] == "status"]
        assert statuses[-1] == "completed"
        if "queued" in statuses:
            assert statuses[statuses.index("queued") + 1] == "running"
    assert backend._agent_slots.running == set() and lead[-1].kind == "result"


async def test_without_a_limit_nothing_is_held():
    backend = _backend([])
    backend._agent_slots = anthropic_module._agent_slots()
    cli = backend.client = FakeCli(backend, _calls(6))
    assert not cli.asks, "no rule: the CLI does not ask Dream about a launch"
    await asyncio.wait_for(_turn(backend), 10)
    assert cli.peak == 6, "every launch at once, as before"


async def test_a_sub_agents_own_launch_is_not_held_and_not_counted():
    _save(1)
    backend = _backend([])
    backend._agent_slots = anthropic_module._agent_slots()
    cli = backend.client = FakeCli(backend, _calls(3), nested={"toolu_02", "toolu_03"})
    await asyncio.wait_for(_turn(backend), 10)
    assert cli.peak == 3 and backend._agent_slots.running == set()


async def test_a_launch_is_allowed_without_the_owners_prompt_as_the_cli_allowed_it_before():
    """Dream's rule only routes the launch here to queue it: the owner's permission callback is not asked about it
    (the CLI allowed a launch without asking in this mode); a sub-agent's own tool calls still are."""
    _save(2)
    asked = []

    async def permission_cb(name, args):
        asked.append(name)
        return False
    backend = _backend([], permission_cb=permission_cb)
    backend._agent_slots = anthropic_module._agent_slots()
    decision = await backend._permission_handler("Agent", {"prompt": "x"}, ToolPermissionContext(tool_use_id="t1"))
    assert isinstance(decision, PermissionResultAllow) and asked == []
    decision = await backend._permission_handler("Write", {"file_path": "x"}, ToolPermissionContext(tool_use_id="t2"))
    assert isinstance(decision, PermissionResultDeny) and asked == ["Write"]


async def test_a_cancelled_wait_leaves_the_line_and_takes_no_slot():
    """The CLI cancels a pending permission request on an interrupt (control_cancel_request: the SDK cancels the
    handler's task): the launch leaves the line, holds nothing, and the next in line still gets the freed slot."""
    _save(1)
    backend = _backend([])
    slots = backend._agent_slots = anthropic_module._agent_slots()
    ask = backend._permission_handler
    first = await ask("Agent", {}, ToolPermissionContext(tool_use_id="a"))
    assert isinstance(first, PermissionResultAllow) and slots.running == {"a"}
    second = asyncio.create_task(ask("Agent", {}, ToolPermissionContext(tool_use_id="b")))
    third = asyncio.create_task(ask("Agent", {}, ToolPermissionContext(tool_use_id="c")))
    await asyncio.sleep(0.01)
    assert not second.done() and not third.done()
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    slots.release("a")
    assert isinstance(await asyncio.wait_for(third, 1), PermissionResultAllow) and slots.running == {"c"}


async def test_the_turns_end_lets_everything_go():
    _save(1)
    backend = _backend([])
    slots = backend._agent_slots = anthropic_module._agent_slots()
    await backend._permission_handler("Agent", {}, ToolPermissionContext(tool_use_id="a"))
    waiting = asyncio.create_task(backend._permission_handler("Agent", {}, ToolPermissionContext(tool_use_id="b")))
    await asyncio.sleep(0.01)
    backend.client = FakeCli(backend, [])                  # a turn whose stream ends at once
    await asyncio.wait_for(_turn(backend), 5)
    assert isinstance(await asyncio.wait_for(waiting, 1), PermissionResultAllow)
    assert slots.running == set()


async def test_a_background_launch_keeps_its_slot_until_its_task_ends():
    """CLAUDE_CODE_DISABLE_BACKGROUND_TASKS keeps sub-agents in the foreground; should the CLI still answer a launch
    with a receipt, the sub-agent runs on, and its slot is held until the SDK says its task ended."""
    _save(1)
    backend = _backend([])
    slots = backend._agent_slots = anthropic_module._agent_slots()

    class Background:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield launch(("bg", "researcher", "Agent"))
            await backend._permission_handler("Agent", {}, ToolPermissionContext(tool_use_id="bg"))
            yield UserMessage(content=[ToolResultBlock(tool_use_id="bg", content="Async agent launched successfully")],
                              tool_use_result={"status": "async_launched", "agentId": "task-9"})
            assert slots.running == {"bg"}, "the receipt is not the end"
            yield TaskNotificationMessage(subtype="task_notification", data={}, task_id="task-9", status="completed",
                                          output_file="", summary="done", uuid="u", session_id="s", tool_use_id="bg")
            assert slots.running == set()
            yield turn_done()
    backend.client = Background()
    await asyncio.wait_for(_turn(backend), 5)


async def test_a_limit_changed_mid_session_applies_to_the_launches_in_line():
    _save(1)
    backend = _backend([])
    slots = backend._agent_slots = anthropic_module._agent_slots()
    await backend._permission_handler("Agent", {}, ToolPermissionContext(tool_use_id="a"))
    waiting = [asyncio.create_task(backend._permission_handler("Agent", {}, ToolPermissionContext(tool_use_id=c)))
               for c in ("b", "c", "d")]
    await asyncio.sleep(0.01)
    _save(3)
    backend.apply_worker_cap()
    await asyncio.sleep(0.01)
    assert [t.done() for t in waiting] == [True, True, False] and slots.running == {"a", "b", "c"}
    _save(None)                                            # cleared: no limit, the rest go
    backend.apply_worker_cap()
    await asyncio.wait_for(waiting[2], 1)
    assert slots.running == {"a", "b", "c", "d"}


async def test_a_session_started_without_a_limit_takes_one_at_its_next_start():
    backend = _backend([])
    backend._agent_slots = anthropic_module._agent_slots()
    _save(2)
    backend.apply_worker_cap()                             # no rule was given to the CLI: nothing to hold now
    assert backend._agent_slots is None
    assert settings.max_agents() == 2
