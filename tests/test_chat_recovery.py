"""Durable ordinary-chat outcomes use the existing transcript storage only."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.core.chat_recovery import parse_last_ordinary_chat_status, status_record
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from test_engine_profile_task import task_engine


def chat_engine(tmp_path, events):
    store = MemoryStore(tmp_path / "chat.db")
    store.start_session("chat")
    engine = Engine.__new__(Engine)
    engine.working = WorkingMemory(store, "chat")
    engine.backend = SimpleNamespace()
    engine._chat_steering_requested = False
    engine._steering_inbox = None

    async def ask(prompt):
        for event in events:
            if isinstance(event, BaseException):
                raise event
            yield event

    engine.ask = ask
    return engine, store


async def test_chat_success_records_started_and_terminal_success(tmp_path):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])
    events = [event async for event in engine.ask_chat("request")]
    statuses = [turn for turn in store.session_turns("chat") if turn["role"] == "turn_status"]
    assert events[-1].data["subtype"] == "success"
    assert len(statuses) == 2
    assert json.loads(statuses[0]["content"])["state"] == "started"
    assert json.loads(statuses[1]["content"])["state"] == "success"
    store.close()


@pytest.mark.parametrize(("result", "state"), [
    ({"is_error": True, "subtype": "error"}, "error"),
    ({"is_error": True, "subtype": "length"}, "incomplete"),
])
async def test_chat_unsuccessful_result_never_records_protocol_success(tmp_path, result, state):
    engine, store = chat_engine(tmp_path, [Event("result", result)])
    [event async for event in engine.ask_chat("request")]
    statuses = [json.loads(turn["content"]) for turn in store.session_turns("chat") if turn["role"] == "turn_status"]
    assert statuses[-1]["state"] == state
    assert statuses[-1]["needs_inspection"] is True
    store.close()


async def test_chat_without_terminal_result_records_incomplete(tmp_path):
    engine, store = chat_engine(tmp_path, [Event("assistant_done", "partial")])
    [event async for event in engine.ask_chat("request")]
    status = json.loads([turn for turn in store.session_turns("chat") if turn["role"] == "turn_status"][-1]["content"])
    assert status["state"] == "incomplete"
    assert status["needs_inspection"] is True
    store.close()


async def test_chat_result_without_explicit_success_is_incomplete(tmp_path):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": None})])
    [event async for event in engine.ask_chat("request")]
    status = json.loads([turn for turn in store.session_turns("chat") if turn["role"] == "turn_status"][-1]["content"])
    assert status["state"] == "incomplete"
    store.close()


async def test_chat_close_failure_downgrades_success_before_terminal_record(tmp_path):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])

    class BrokenInbox:
        async def close(self):
            raise OSError("inbox close failed")

    original = engine.ask

    async def ask(prompt):
        engine._steering_inbox = BrokenInbox()
        async for event in original(prompt):
            yield event

    engine.ask = ask
    with pytest.raises(OSError, match="inbox close failed"):
        [event async for event in engine.ask_chat("request")]
    status = json.loads([turn for turn in store.session_turns("chat") if turn["role"] == "turn_status"][-1]["content"])
    assert status["state"] == "error"
    store.close()


async def test_chat_partial_text_is_durable_before_incomplete_terminal_status(tmp_path):
    engine, store = chat_engine(tmp_path, [Event("text_delta", "useful partial"), Event("result", {"is_error": True, "subtype": "length"})])
    [event async for event in engine.ask_chat("request")]
    turns = store.session_turns("chat")
    partial = next(turn for turn in turns if turn["role"] == "assistant_partial")
    terminal = [turn for turn in turns if turn["role"] == "turn_status"][-1]
    assert partial["content"] == "useful partial"
    assert json.loads(terminal["content"])["state"] == "incomplete"
    store.close()


async def test_chat_cancellation_records_interrupted_before_propagating(tmp_path):
    engine, store = chat_engine(tmp_path, [])
    entered = asyncio.Event()

    async def waiting(prompt):
        entered.set()
        await asyncio.Event().wait()
        yield Event("result", {"is_error": False, "subtype": "success"})

    engine.ask = waiting
    async def consume():
        return [event async for event in engine.ask_chat("request")]

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    status = json.loads([turn for turn in store.session_turns("chat") if turn["role"] == "turn_status"][-1]["content"])
    assert status["state"] == "interrupted"
    store.close()


async def test_chat_start_write_failure_never_dispatches_provider(tmp_path, monkeypatch):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])
    called = False

    async def dispatched(prompt):
        nonlocal called
        called = True
        yield Event("result", {"is_error": False, "subtype": "success"})

    engine.ask = dispatched
    monkeypatch.setattr(engine.working, "log_turn", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        [event async for event in engine.ask_chat("request")]
    assert called is False
    store.close()


async def test_chat_terminal_write_failure_never_returns_success(tmp_path, monkeypatch):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])
    original = engine.working.log_turn

    def fail_terminal(role, content, *args, **kwargs):
        if role == "turn_status" and json.loads(content)["state"] == "success":
            raise OSError("status write failed")
        return original(role, content, *args, **kwargs)

    monkeypatch.setattr(engine.working, "log_turn", fail_terminal)
    with pytest.raises(OSError, match="status write failed"):
        [event async for event in engine.ask_chat("request")]
    statuses = [turn for turn in store.session_turns("chat") if turn["role"] == "turn_status"]
    assert [json.loads(turn["content"])["state"] for turn in statuses] == ["started", "error"]
    store.close()


async def test_cancellation_during_success_status_write_leaves_non_success_tail(tmp_path, monkeypatch):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])
    entered, release = threading.Event(), threading.Event()
    original = engine.working.log_turn

    def hold_success(role, content, *args, **kwargs):
        if role == "turn_status" and json.loads(content)["state"] == "success":
            entered.set()
            assert release.wait(5)
        return original(role, content, *args, **kwargs)

    monkeypatch.setattr(engine.working, "log_turn", hold_success)
    async def consume():
        return [event async for event in engine.ask_chat("request")]

    task = asyncio.create_task(consume())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    states = [json.loads(turn["content"])["state"] for turn in store.session_turns("chat")
              if turn["role"] == "turn_status"]
    assert states[-1] == "interrupted"
    assert states.count("success") == 1
    store.close()


async def test_post_commit_success_status_failure_appends_error_correction(tmp_path, monkeypatch):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])
    original = engine.working.log_turn

    def commit_then_fail(role, content, *args, **kwargs):
        result = original(role, content, *args, **kwargs)
        if role == "turn_status" and json.loads(content)["state"] == "success":
            raise OSError("jsonl close failed after commit")
        return result

    monkeypatch.setattr(engine.working, "log_turn", commit_then_fail)
    with pytest.raises(OSError, match="jsonl close failed"):
        [event async for event in engine.ask_chat("request")]
    states = [json.loads(turn["content"])["state"] for turn in store.session_turns("chat")
              if turn["role"] == "turn_status"]
    assert states == ["started", "success", "error"]
    store.close()


async def test_second_chat_is_rejected_while_first_start_record_is_writing(tmp_path, monkeypatch):
    engine, store = chat_engine(tmp_path, [Event("result", {"is_error": False, "subtype": "success"})])
    entered, release = threading.Event(), threading.Event()
    original = engine.working.log_turn

    def hold_start(role, content, *args, **kwargs):
        if role == "turn_status" and json.loads(content)["state"] == "started":
            entered.set()
            assert release.wait(5)
        return original(role, content, *args, **kwargs)

    monkeypatch.setattr(engine.working, "log_turn", hold_start)
    first = asyncio.create_task(anext(engine.ask_chat("first")))
    await asyncio.to_thread(entered.wait, 5)
    with pytest.raises(RuntimeError, match="already active"):
        [event async for event in engine.ask_chat("second")]
    release.set()
    await first
    store.close()


def test_parser_requires_matching_started_record_and_marks_later_activity_unknown():
    attempt = "a" * 32
    rows = [
        {"id": 9, "role": "turn_status", "content": status_record(attempt, "success", needs_inspection=False)},
        {"id": 8, "role": "turn_status", "content": status_record(attempt, "started", needs_inspection=True)},
    ]
    success = parse_last_ordinary_chat_status(rows)
    stale = parse_last_ordinary_chat_status(rows, latest_activity_id=10)
    assert success.state == "success" and success.needs_inspection is False
    assert "protocol" in success.summary
    assert stale.state == "unknown" and stale.needs_inspection is True


def test_parser_does_not_promote_orphan_terminal_to_success():
    recovery = parse_last_ordinary_chat_status([
        {"id": 9, "role": "turn_status", "content": status_record("b" * 32, "success", needs_inspection=False)},
    ])
    assert recovery.state == "unknown"
    assert recovery.needs_inspection is True


@pytest.mark.parametrize("content", [
    '{"schema":true,"kind":"ordinary_chat","attempt_id":"c' + 'c' * 31 + '","state":"success","needs_inspection":false,"explanation":"x"}',
    '{"schema":1,"kind":"ordinary_chat","attempt_id":"d' + 'd' * 31 + '","state":[],"needs_inspection":false,"explanation":"x"}',
    '{"schema":1,"schema":1,"kind":"ordinary_chat","attempt_id":"e' + 'e' * 31 + '","state":"success","needs_inspection":false,"explanation":"x"}',
])
def test_parser_treats_hostile_status_as_unknown_without_raising(content):
    recovery = parse_last_ordinary_chat_status([
        {"id": 9, "role": "turn_status", "content": content},
        {"id": 8, "role": "turn_status", "content": status_record("e" * 32, "started", needs_inspection=True)},
    ])
    assert recovery.state == "unknown"
    assert "UNKNOWN" in recovery.summary


def test_parser_rejects_deep_json_and_inconsistent_started_record():
    nested = "[" * 1000 + "0" + "]" * 1000
    recovery = parse_last_ordinary_chat_status([
        {"id": 9, "role": "turn_status", "content": nested},
        {"id": 8, "role": "turn_status", "content": status_record("f" * 32, "started", needs_inspection=True)},
    ])
    assert recovery.state == "unknown"


async def test_actual_engine_chat_success_persists_a_paired_protocol_status(task_engine):
    engine, _, _ = await task_engine(control="false_done", history=False)
    try:
        events = [event async for event in engine.ask_chat("Reply with a fixture answer only.")]
        statuses = [json.loads(turn["content"]) for turn in engine.store.session_turns(engine.session_id)
                    if turn["role"] == "turn_status"]
        assert any(event.kind == "result" and event.data["subtype"] == "success" for event in events)
        assert [status["state"] for status in statuses] == ["started", "success"]
    finally:
        await engine.backend.close_background()


class EventBackend:
    def __init__(self, events):
        self.events = events

    async def ask(self, prompt):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


@pytest.mark.parametrize(("events", "state"), [
    ([Event("error", "fixture provider error"), Event("result", {"is_error": True, "subtype": "error"})], "error"),
    ([Event("text_delta", "fixture partial")], "incomplete"),
])
async def test_actual_engine_chat_non_success_has_durable_non_success_status(task_engine, events, state):
    engine, _, _ = await task_engine(control="false_done", history=False)
    engine.backend = EventBackend(events)
    [event async for event in engine.ask_chat("bounded fixture")]
    turns = engine.store.session_turns(engine.session_id)
    statuses = [json.loads(turn["content"])["state"] for turn in turns if turn["role"] == "turn_status"]
    assert statuses == ["started", state]
    if state == "incomplete":
        assert any(turn["role"] == "assistant_partial" and turn["content"] == "fixture partial" for turn in turns)


async def test_actual_engine_chat_cancellation_records_interrupted(task_engine):
    engine, _, _ = await task_engine(control="false_done", history=False)
    entered = asyncio.Event()

    class BlockingBackend:
        async def ask(self, prompt):
            entered.set()
            await asyncio.Event().wait()
            yield Event("result", {"is_error": False, "subtype": "success"})

    engine.backend = BlockingBackend()
    task = asyncio.create_task(anext(engine.ask_chat("cancel fixture")))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    statuses = [json.loads(turn["content"])["state"] for turn in engine.store.session_turns(engine.session_id)
                if turn["role"] == "turn_status"]
    assert statuses == ["started", "interrupted"]
    with pytest.raises(ValueError):
        status_record("f" * 32, [], needs_inspection=True)
    with pytest.raises(ValueError):
        status_record("f" * 32, "started", needs_inspection=False)
    invalid_started = json.dumps({"schema": 1, "kind": "ordinary_chat", "attempt_id": "f" * 32,
                                  "state": "started", "needs_inspection": False, "explanation": "x"})
    recovery = parse_last_ordinary_chat_status([
        {"id": 9, "role": "turn_status", "content": status_record("f" * 32, "error", needs_inspection=True)},
        {"id": 8, "role": "turn_status", "content": invalid_started},
    ])
    assert recovery.state == "unknown"
