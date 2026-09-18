"""Four failure modes that must not degrade quietly: an evaluator that can't run,
an advisor that errored instead of answering, a CLI event line bigger than the
stream buffer, and a dead-but-unreaped model server.

Nothing here spawns a real CLI, loads a model, or touches the network: the loop's
evaluator query, the council's backend and the codex binary are all stood in for,
and the "server" is a `true` that exits and is left unreaped on purpose.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import dream.config as config
from dream.core import loop as loop_mod
from dream.core import moe
from dream.core.backends.base import Event
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter
from dream.local import machx


# --- 1. the evaluator gate must never fail open ------------------------------


class _StubEngine:
    """Engine stand-in for the loop: replies from a script, one per ask()."""

    model = "stub-model"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ask(self, prompt: str):
        self.prompts.append(prompt)
        yield Event("assistant_done", self.replies.pop(0) if self.replies else "STATUS: DONE")

    async def interrupt(self) -> None:
        return None


def _raising_query(**kwargs):
    raise RuntimeError("evaluator socket closed")


async def test_unavailable_evaluator_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_mod, "query", _raising_query)
    lp = loop_mod.AutonomousLoop(_StubEngine([]))
    lp.workspace = tmp_path

    verdict, gaps = await lp._evaluate("some goal")

    assert verdict != "PASS"                    # a broken gate grades nothing
    assert "evaluator socket closed" in gaps    # the real error stays visible


async def test_loop_reports_unverified_when_the_evaluator_cannot_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOOP_DIR", tmp_path / "loops")
    monkeypatch.setattr(loop_mod, "query", _raising_query)
    engine = _StubEngine(["- criterion one", "did the work\nSTATUS: DONE\nNEXT: nothing"])

    result = await loop_mod.AutonomousLoop(engine).run("some goal")

    assert result.status != "done"              # unverified work is not verified work
    assert result.status == "unverified"
    assert "evaluator socket closed" in result.message


async def test_verified_pass_still_reports_done(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOOP_DIR", tmp_path / "loops")

    async def _passing(self, goal):
        return "PASS", "none"

    monkeypatch.setattr(loop_mod.AutonomousLoop, "_evaluate", _passing)
    engine = _StubEngine(["- criterion one", "did the work\nSTATUS: DONE\nNEXT: nothing"])

    result = await loop_mod.AutonomousLoop(engine).run("some goal")
    assert result.status == "done"


# --- 2. an advisor that errored never looks like an advisor that agreed -------


class _FakeBackend:
    """Backend stand-in that replays a fixed event stream through one ask()."""

    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.disconnected = False

    async def connect(self) -> None:
        return None

    async def ask(self, prompt: str):
        for ev in self.events:
            yield ev

    async def disconnect(self) -> None:
        self.disconnected = True


async def test_backend_error_event_is_reported_not_swallowed():
    backend = _FakeBackend([Event("error", "MachX HTTP 500: upstream exploded")])

    out = await moe._run_backend(backend, "q")

    assert out                                   # never silent emptiness
    assert "MachX HTTP 500" in out
    assert backend.disconnected


async def test_partial_answer_keeps_both_text_and_error():
    backend = _FakeBackend([
        Event("assistant_done", "half an answer"),
        Event("error", "MachX request failed: ReadTimeout"),
    ])

    out = await moe._run_backend(backend, "q")

    assert "half an answer" in out
    assert "ReadTimeout" in out


async def test_council_entry_carries_an_advisor_http_failure(monkeypatch):
    monkeypatch.setattr(
        moe, "OpenAICompatBackend",
        lambda **kw: _FakeBackend([Event("error", "MachX HTTP 429: rate limited")]),
    )

    (entry,) = await moe.council(["machx"], "q")

    assert entry["advisor"] == "machx"
    assert "429" in entry["answer"]


# --- 3. one oversized CLI event line must not kill the turn ------------------


def _fake_codex_catting(tmp_path: Path, fixture: Path) -> Path:
    fake = tmp_path / "codex"
    fake.write_text(f'#!/usr/bin/env bash\ncat "{fixture}"\n')
    fake.chmod(0o755)
    return fake


async def test_oversized_stdout_line_is_streamed_not_fatal(tmp_path, monkeypatch):
    # codex item.completed events carry whole command outputs; anything past the
    # StreamReader default (64 KiB) used to raise ValueError out of ask().
    big = "x" * 200_000
    fixture = tmp_path / "big.jsonl"
    fixture.write_text(
        json.dumps({"type": "item.completed",
                    "item": {"type": "agent_message", "text": big}}) + "\n"
        + json.dumps({"type": "turn.completed", "usage": {}}) + "\n",
        encoding="utf-8",
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "cmd", str(_fake_codex_catting(tmp_path, fixture)))
    backend = CliAgentBackend(adapter, system_prompt="S", cwd=str(tmp_path))
    await backend.connect()

    events = [e async for e in backend.ask("ping")]

    assert [e.kind for e in events] == ["assistant_done", "result"]
    assert events[0].data == big
    await backend.disconnect()


# --- 4. a zombie is not a running server -------------------------------------


def _wait_for_zombie(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        if stat.rpartition(")")[2].split()[0] == "Z":
            return
        time.sleep(0.02)
    raise AssertionError(f"pid {pid} never became a zombie")


def test_zombie_child_is_not_alive():
    # start_new_session: stop() signals the whole process group, so a test corpse
    # must never share pytest's.
    proc = subprocess.Popen(["true"], start_new_session=True)  # not reaped on purpose
    try:
        _wait_for_zombie(proc.pid)
        assert machx._alive(proc.pid) is False
    finally:
        proc.wait()


def test_running_process_is_still_alive():
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert machx._alive(proc.pid) is True
    finally:
        proc.kill()
        proc.wait()


def test_stop_does_not_claim_to_have_stopped_a_zombie(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    proc = subprocess.Popen(["true"], start_new_session=True)
    try:
        _wait_for_zombie(proc.pid)
        (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")

        t0 = time.monotonic()
        assert machx.stop(timeout=8) is False       # nothing live was stopped
        assert time.monotonic() - t0 < 2.0          # and no wait-for-death stall
        assert not (tmp_path / "machx.pid").exists()
    finally:
        proc.wait()
