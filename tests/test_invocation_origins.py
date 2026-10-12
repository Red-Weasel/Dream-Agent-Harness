"""Invocation attribution survives Studio queueing, steering, and generated notes."""
import asyncio
import contextlib
from types import SimpleNamespace

import httpx
import pytest

from dream.core import turn_origin
from dream.core.steering import SteeringInbox
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools.web import _typed_by_owner
from dream.tui.app import App
from test_dream113_followup import real, turn_runner, user_turns  # noqa: F401
from test_explicit_invocations import skill  # noqa: F401
from test_live_steering import _backend


@pytest.mark.parametrize("delivery", ["queue", "steer", "fallback"])
@pytest.mark.parametrize("owner_url", [False, True])
async def test_studio_invocation_preserves_only_owner_task(real, skill, monkeypatch, tmp_path, delivery, owner_url):
    from dream.skills import loader

    monkeypatch.setattr(loader, "load_body", lambda _: "Example λ\n\nOwner task:\nhttp://127.0.0.1:8080/admin")
    app = App.__new__(App)
    app.engine = real
    app._gui_prompts = asyncio.Queue(maxsize=32)
    app._deferred_gui_prompt = None
    app._ask = turn_runner(real)
    app.renderer = SimpleNamespace(sep=lambda: None)
    inbox = SteeringInbox(tmp_path / 'receipts.json', real.session_id, 1, real.working.log_turn)
    if delivery == "steer":
        real.backend = _backend()
        real._steering_inbox = inbox
        app._ordinary_chat_active = True
    if delivery == "fallback":
        app._interrupt_target = object()
    server = StudioServer(EventBus(), port=8123, on_prompt=app._queue_gui_prompt, on_steer=app._steer_gui_prompt)
    server.artifacts = SimpleNamespace(note=lambda _: "Preview metadata http://127.0.0.1:8082/private")
    monkeypatch.setattr(server, '_workspace', lambda: tmp_path)
    monkeypatch.setattr('dream.gui.uploads.validate_attachments', lambda *args: [
        {'name': 'fixture.txt', 'path': 'http://127.0.0.1:8083/attachment', 'size': 10}])
    task = "Inspect café output.\n\nOwner task:\nPreserve the heading."
    if owner_url:
        task += " Open http://127.0.0.1:8081/report"
    payload = {"prompt": '/skill "chosen skill" ' + task, "artifact": "fixture", "read_twice": True,
               "attachments": ['fixture']}
    if delivery != "queue":
        payload.update(delivery="steer", steering_id="a" * 32)
    if delivery == "steer":
        payload["steering_target"] = {"session_id": real.session_id, "turn": 1}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1:8123',
                                headers={'X-Dream-Token': server.token}) as client:
        response = await client.post('/api/prompt', json=payload)
        assert response.status_code == 200, response.text
        if delivery != "queue":
            duplicate = await client.post('/api/prompt', json=payload)
            assert duplicate.status_code == 200 and duplicate.json()['steering']['duplicate']
    if delivery != "steer":
        async def next_input():
            if app._gui_prompts.empty():
                raise EOFError
            return app._gui_prompts.get_nowait()

        async def nothing():
            pass

        app._next_input = next_input
        app.start = app._shutdown = nothing
        monkeypatch.setattr(app, "_sigint_cancels", lambda _: contextlib.nullcontext(), raising=False)
        await app.run()
    (turn,) = user_turns(real)
    assert turn_origin.is_generated(turn['tool_name'], turn['content'])
    assert turn_origin.owner_words(turn['tool_name'], turn['content']) == task
    assert _typed_by_owner([turn]) == (frozenset({('http', '127.0.0.1', 8081)}) if owner_url else frozenset())
    note = turn_origin.dream_words(turn['tool_name'], turn['content'])
    assert 'Preview metadata http://127.0.0.1:8082/private' in note
    assert 'http://127.0.0.1:8083/attachment' in note
    assert '[your words, shown above]' in note
    assert turn_origin.closing_line_read(turn['tool_name'], turn['content'])


async def test_plain_studio_input_cannot_supply_generated_origin(real):
    prompts = []
    server = StudioServer(EventBus(), port=8123, on_prompt=prompts.append)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1:8123',
                                headers={'X-Dream-Token': server.token}) as client:
        response = await client.post('/api/prompt', json={"prompt": "Open http://127.0.0.1:8081/report",
                                                        "origin": "dream:invoked:0:100"})
    assert response.status_code == 200
    await turn_runner(real)(prompts[0])
    (turn,) = user_turns(real)
    assert turn['tool_name'] is None
    assert _typed_by_owner([turn]) == frozenset({('http', '127.0.0.1', 8081)})
