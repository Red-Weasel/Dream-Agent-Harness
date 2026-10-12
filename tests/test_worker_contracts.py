"""DREAM-252: a `task` call can say what its worker needs and what it must leave behind, and Dream checks both.

Two optional, machine-checkable fields on the `task` tool (a call without them runs exactly as before):

- ``required_tools``: tool names the worker must really have. Checked against the role's EFFECTIVE tools -- the names
  its spec lists that exist on this backend and are enabled in settings -- before any model request. A role that lacks
  one is refused with what it has; nothing is added to a role to make a contract fit.
- ``outputs``: workspace-relative files the worker must produce, ``{"path", "format": "json"|"text",
  "required_keys": [...]}``. A malformed contract is refused before the worker starts. After the worker's last reply
  each file must exist inside the workspace (no symbolic link on its path is followed: a link, wherever it points, is
  refused), be a regular single-link file that changed while this run lasted, be small enough to read, and pass its
  format check -- read, never executed -- before the run may be reported complete. "Changed while the run lasted" is
  not "written by this worker": another writer in the workspace could have done it, and the result says so.

Every refusal and failed check carries a typed ``reason=<code>``: invalid_contract, incompatible_role, no_workspace,
output_missing, output_unchanged, output_invalid, output_outside_workspace, output_too_large.

Scripted transports and a temporary workspace on the real filesystem: no engine, no model, no GPU.
"""
from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from dream.core.backends.openai_compat import _AGENT_ACTIVITY
from dream.core.subagents import LocalSubagentSpec, local_subagents
from test_local_subagents import _PostResp, _StreamCtx, _backend, _lead_final, _sse, _sub_final, _tool

CODER = ("read_file", "write_file", "run_bash")
READER = ("read_file", "list_dir")
GOOD = json.dumps({"nodes": [{"id": "file:a.py"}], "edges": []})


def _spec(*tools, name="worker"):
    return {name: LocalSubagentSpec(name=name, description=f"{name} role", prompt=f"You are the {name}.",
                                    tool_names=tuple(tools))}


def _call(name, args, *, reasoning=None, content=None):
    """One worker reply that asks for one tool call."""
    message = {"content": content, "tool_calls": [{"id": "s1", "function": {"name": name, "arguments": json.dumps(args)}}]}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message}]}


class _Engine:
    """A worker's plain replies in order (the last repeats) and the lead's streamed rounds. A request whose index is
    in `gates` waits for its event. A recovery request (Dream's tools-off "STOP." instruction) is answered with
    `recovery` -- a reply, or an exception to raise -- and is not counted against the script."""

    def __init__(self, *replies, lead=(), recovery=None):
        self.replies, self.lead, self.recovery = list(replies), list(lead), recovery
        self.posted, self.recoveries, self.gates, self.run_id, self.streamed = [], [], {}, None, 0

    def stream(self, method, url, json=None):
        i = min(self.streamed, len(self.lead) - 1)
        self.streamed += 1
        return _StreamCtx(self.lead[i])

    async def post(self, url, json=None):
        activity = _AGENT_ACTIVITY.get()
        if activity is not None:
            self.run_id = activity["run_id"]
        last = json["messages"][-1]
        if last.get("name") == "dream_recovery_instruction" and "STOP." in str(last.get("content")):
            self.recoveries.append(copy.deepcopy(json))
            if isinstance(self.recovery, Exception):
                raise self.recovery
            return _PostResp(self.recovery if self.recovery is not None else _sub_final(""))
        n = len(self.posted)
        self.posted.append(copy.deepcopy(json))
        gate = self.gates.get(n)
        if gate is not None:
            await gate.wait()
        return _PostResp(self.replies[min(n, len(self.replies) - 1)])


@contextmanager
def _workspace(root: Path):
    """The session's tool context bound to a real directory, as the engine binds it around a turn."""
    from dream.tools.context import ToolContext, bind_context
    with bind_context(ToolContext(None, None, None, "dream-252", workspace=root)):
        yield root


def _files(root: Path, ran: list | None = None):
    """read_file, write_file, list_dir and run_bash that act on the real directory. write_file writes `content` at
    `path`, or with `link_to` makes `path` a symlink to it (what a shell could do); run_bash only records."""
    def text(value):
        return {"content": [{"type": "text", "text": value}]}

    async def read_file(a):
        return text((root / a["path"]).read_text(encoding="utf-8"))

    async def list_dir(a):
        return text("\n".join(sorted(p.name for p in (root / a.get("path", ".")).iterdir())))

    async def write_file(a):
        if ran is not None:
            ran.append(("write_file", a.get("path")))
        target = root / a["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "link_to" in a:
            os.symlink(a["link_to"], target)
            return text(f"linked {a['path']}")
        if "megabytes" in a:          # a large valid JSON file, made here so its bytes never cross the worker's context
            target.write_text(json.dumps({"nodes": ["x" * 1024] * (1024 * a["megabytes"]), "edges": []}), encoding="utf-8")
            return text(f"wrote {a['path']}")
        target.write_text(a["content"], encoding="utf-8")
        return text(f"wrote {a['path']}")

    async def run_bash(a):
        if ran is not None:
            ran.append(("run_bash", a.get("command")))
        return text("ok")
    return [_tool("read_file", read_file), _tool("write_file", write_file), _tool("list_dir", list_dir),
            _tool("run_bash", run_bash)]


def _session(root: Path, spec, *replies, ran=None, tools=None, **engine):
    b = _backend(_files(root, ran) if tools is None else tools, spec)
    b._client = _Engine(*replies, **engine)
    events = []
    b._background_emit = events.append
    return b, events


def _statuses(events):
    return [e.data.get("status") for e in events if e.kind == "agent_activity" and e.data.get("kind") == "status"]


def _task(prompt="write the file", agent="worker", **contract):
    return {"subagent_type": agent, "prompt": prompt, **contract}


def _out(path="out/result.json", **more):
    return [{"path": path, "format": "json", "required_keys": ["nodes", "edges"], **more}]


# --- the schema, and calls without a contract --------------------------------------------------------------------

def test_the_task_schema_offers_the_contract_fields_and_requires_neither(tmp_path):
    b, _ = _session(tmp_path, _spec(*CODER), _sub_final("done"))
    task = next(s for s in b.tool_schemas if s["function"]["name"] == "task")["function"]["parameters"]
    assert task["required"] == ["subagent_type", "prompt"], "a call without a contract stays valid"
    assert task["properties"]["subagent_type"]["enum"] == ["worker"]
    tools, outputs = task["properties"]["required_tools"], task["properties"]["outputs"]
    assert tools["type"] == "array" and tools["items"]["type"] == "string"
    assert outputs["type"] == "array" and outputs["items"]["type"] == "object"
    assert outputs["items"]["required"] == ["path"]
    assert set(outputs["items"]["properties"]) >= {"path", "format", "required_keys"}
    assert outputs["items"]["properties"]["format"]["enum"] == ["json", "text"]


@pytest.mark.parametrize("contract", [{}, {"required_tools": [], "outputs": []}], ids=["no contract", "empty contract"])
async def test_a_call_without_a_contract_runs_as_before(tmp_path, contract):
    b, events = _session(tmp_path, _spec(*READER), _sub_final("done"))
    assert await b._exec_tool("task", _task(**contract)) == ("done", False)
    assert len(b._client.posted) == 1
    assert {t["function"]["name"] for t in b._client.posted[0]["tools"]} == set(READER)
    assert _statuses(events) == ["running", "completed"]


# --- required tools: the role's effective tools, checked before any request ------------------------------------------

@pytest.mark.parametrize("contract", [{"required_tools": ["write_file"]}, {"required_tools": ["read_file", "run_bash"]},
                                      {"outputs": _out()}],
                         ids=["a tool it lacks", "one of two it lacks", "outputs need a tool that writes"])
async def test_a_role_without_a_required_tool_is_refused_before_any_request(tmp_path, contract):
    ran = []
    b, events = _session(tmp_path, _spec(*READER), _sub_final("done"), ran=ran)
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(**contract))
    assert failed and "reason=incompatible_role" in out, out
    assert "read_file" in out and "list_dir" in out, "it says what the role really has"
    missing = "write_file" if "outputs" in contract or "write_file" in contract["required_tools"] else "run_bash"
    assert missing in out
    assert b._client.posted == [] and ran == [], "no model request and no tool ran"
    assert b._subagents["worker"].tool_names == READER, "the role was not widened to fit the contract"
    assert "completed" not in _statuses(events)


async def test_a_tool_the_spec_names_but_the_backend_lacks_does_not_count(tmp_path):
    tools = [t for t in _files(tmp_path) if t.name != "write_file"]
    b, _ = _session(tmp_path, _spec(*CODER), _sub_final("done"), tools=tools)
    out, failed = await b._exec_tool("task", _task(required_tools=["write_file"]))
    assert failed and "reason=incompatible_role" in out and "write_file" in out, out
    assert b._client.posted == []


async def test_a_tool_disabled_in_settings_does_not_count(tmp_path, monkeypatch):
    from dream import extensions
    monkeypatch.setattr(extensions, "tool_enabled", lambda tool: tool.name != "write_file")
    ran = []
    b, _ = _session(tmp_path, _spec(*CODER), _sub_final("done"), ran=ran)
    out, failed = await b._exec_tool("task", _task(required_tools=["read_file", "write_file"]))
    assert failed and "reason=incompatible_role" in out and "write_file" in out, out
    assert b._client.posted == [] and ran == []


async def test_a_role_that_has_every_required_tool_runs(tmp_path):
    b, events = _session(tmp_path, _spec(*CODER), _sub_final("done"))
    assert await b._exec_tool("task", _task(required_tools=list(CODER))) == ("done", False)
    assert len(b._client.posted) == 1 and _statuses(events) == ["running", "completed"]


# --- a malformed contract is refused before the worker starts ------------------------------------------------------------

MALFORMED = {
    "required_tools is a string": {"required_tools": "write_file"},
    "required_tools holds a number": {"required_tools": ["write_file", 7]},
    "required_tools holds an empty name": {"required_tools": [""]},
    "outputs is a string": {"outputs": "out/result.json"},
    "an output is a string": {"outputs": ["out/result.json"]},
    "an output has no path": {"outputs": [{"format": "json"}]},
    "an empty path": {"outputs": [{"path": ""}]},
    "an absolute path": {"outputs": [{"path": "/etc/passwd"}]},
    "a home path": {"outputs": [{"path": "~/result.json"}]},
    "a parent path": {"outputs": [{"path": "../result.json"}]},
    "a path that climbs out": {"outputs": [{"path": "out/../../result.json"}]},
    "a path with a NUL": {"outputs": [{"path": "out/re\x00sult.json"}]},
    "the workspace itself": {"outputs": [{"path": "."}]},
    "an unknown format": {"outputs": [{"path": "out/result.json", "format": "exe"}]},
    "required_keys is a string": {"outputs": [{"path": "out/result.json", "format": "json", "required_keys": "nodes"}]},
    "required_keys on text": {"outputs": [{"path": "out/notes.txt", "format": "text", "required_keys": ["nodes"]}]},
    "an unknown field": {"outputs": [{"path": "out/result.json", "run": "python3 out/result.json"}]},
    "the same path twice": {"outputs": [{"path": "out/result.json"}, {"path": "out/./result.json"}]},
    "too many outputs": {"outputs": [{"path": f"out/r{k}.json"} for k in range(200)]},
}


@pytest.mark.parametrize("contract", MALFORMED.values(), ids=MALFORMED.keys())
async def test_a_malformed_contract_is_refused_before_the_worker_starts(tmp_path, contract):
    ran = []
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": GOOD}),
                         _sub_final("done"), ran=ran)
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(**contract))
    assert failed and "reason=invalid_contract" in out, out
    assert b._client.posted == [] and ran == [], "nothing was sent and nothing ran"
    assert not (tmp_path / "out").exists()
    assert "completed" not in _statuses(events)


async def test_outputs_need_a_bound_workspace(tmp_path):
    """Without the session's workspace there is nothing to check a file against: refused, not skipped."""
    ran = []
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": GOOD}),
                    _sub_final("done"), ran=ran)
    out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=no_workspace" in out, out
    assert b._client.posted == [] and ran == []


# --- declared outputs are checked before the run is reported complete ------------------------------------------------------

async def test_a_worker_that_wrote_its_declared_output_completes(tmp_path):
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": GOOD}),
                         _sub_final("task 1-1: out/result.json\ncheck: ok"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(required_tools=["write_file"], outputs=_out()))
    assert not failed, out
    assert out.startswith("task 1-1: out/result.json\ncheck: ok"), "the worker's own reply comes first, unchanged"
    assert "out/result.json" in out and "verified" in out
    assert json.loads((tmp_path / "out/result.json").read_text()) == json.loads(GOOD)
    assert _statuses(events) == ["running", "completed"]


async def test_a_worker_that_claims_done_without_its_output_is_not_reported_complete(tmp_path):
    b, events = _session(tmp_path, _spec(*CODER), _sub_final("task 1-1: out/result.json\ncheck: ok"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=output_missing" in out and "out/result.json" in out, out
    assert "task 1-1: out/result.json" in out, "the lead still sees what the worker claimed"
    statuses = _statuses(events)
    assert statuses[-1] == "failed" and "completed" not in statuses, statuses
    assert len(b._client.posted) == 1, "a failed check does not send the worker back on its own"


async def test_the_lead_gets_a_failed_task_result_for_a_missing_output(tmp_path):
    """The same check through the lead's own turn: its `task` call carries the contract, its result is an error."""
    args = json.dumps(_task(outputs=_out()))
    lead = [[_sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function": {
                "name": "task", "arguments": args}}]}, "finish_reason": None}]}),
             _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}), "data: [DONE]"],
            _lead_final("the worker did not write its file")]
    b, events = _session(tmp_path, _spec(*CODER), _sub_final("all written"), lead=lead)
    with _workspace(tmp_path):
        turn = [event async for event in b.ask("map it")]
    result = next(e.data for e in turn if e.kind == "tool_result" and e.data["name"] == "task")
    assert result["is_error"] and "reason=output_missing" in result["content"], result
    tool_message = next(m for m in b.messages if m.get("role") == "tool")
    assert "reason=output_missing" in tool_message["content"] and "out/result.json" in tool_message["content"]
    assert "completed" not in _statuses(events)
    assert not next(e.data for e in turn if e.kind == "result").get("is_error"), "the lead's turn goes on"


async def test_a_file_that_was_already_there_and_untouched_is_not_this_workers_output(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.json").write_text(GOOD)
    b, events = _session(tmp_path, _spec(*CODER), _sub_final("done"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=output_unchanged" in out and "out/result.json" in out, out
    assert "completed" not in _statuses(events)


async def test_a_file_that_was_already_there_and_rewritten_counts(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.json").write_text('{"nodes": "half-writ')
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": GOOD}),
                    _sub_final("rewritten whole"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert not failed and out.startswith("rewritten whole"), out


INVALID = {
    "not JSON": '{"nodes": [',
    "an empty file": "",
    "JSON that is not an object": "[1, 2, 3]",
    "a required key is missing": json.dumps({"nodes": []}),
}


@pytest.mark.parametrize("content", INVALID.values(), ids=INVALID.keys())
async def test_an_output_that_fails_its_format_check_fails_the_run(tmp_path, content):
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": content}),
                         _sub_final("done"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=output_invalid" in out and "out/result.json" in out, out
    assert "completed" not in _statuses(events)


async def test_a_directory_is_not_an_output_file(tmp_path):
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json/x.json", "content": GOOD}),
                    _sub_final("done"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=output_invalid" in out, out


@pytest.mark.parametrize("kind", ["a symlinked file", "a symlinked folder", "a dangling symlink"])
async def test_an_output_that_resolves_outside_the_workspace_is_refused(tmp_path, kind):
    workspace, outside = tmp_path / "ws", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "result.json").write_text(GOOD)            # valid content: only where it lives is wrong
    link = {"a symlinked file": {"path": "out/result.json", "link_to": str(outside / "result.json")},
            "a symlinked folder": {"path": "out", "link_to": str(outside)},
            "a dangling symlink": {"path": "out/result.json", "link_to": str(outside / "nothing.json")}}[kind]
    b, events = _session(workspace, _spec(*CODER), _call("write_file", link), _sub_final("done"))
    with _workspace(workspace):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    # where the path resolves is checked before whether anything is there, so a dangling link out is an escape too
    assert failed and "reason=output_outside_workspace" in out and "out/result.json" in out, out
    assert GOOD not in out and "file:a.py" not in out, "nothing read from outside the workspace is repeated"
    assert (outside / "result.json").read_text() == GOOD
    assert "completed" not in _statuses(events)


async def test_an_output_too_large_to_check_is_refused_unread(tmp_path):
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "megabytes": 3}),
                    _sub_final("done"))                                          # about 3 MiB of valid JSON
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert (tmp_path / "out/result.json").stat().st_size > 3 * 1024 * 1024
    assert failed and "reason=output_too_large" in out, out
    assert len(out) < 4000, "the result names the file; it never carries the file"


async def test_a_text_output_is_read_and_never_run(tmp_path):
    script = "#!/bin/sh\ntouch ran-by-the-check\n"
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/build.sh", "content": script}),
                    _sub_final("wrote the script"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=[{"path": "out/build.sh", "format": "text"}]))
    assert not failed and out.startswith("wrote the script"), out
    assert not (tmp_path / "ran-by-the-check").exists()
    assert not os.access(tmp_path / "out/build.sh", os.X_OK), "the check did not change the file's mode"


async def test_every_declared_output_is_checked(tmp_path):
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/one.json", "content": GOOD}),
                    _sub_final("both written"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out("out/one.json") + _out("out/two.json")))
    assert failed and "reason=output_missing" in out and "out/two.json" in out, out


async def test_a_worker_that_ended_incomplete_is_not_completed_by_a_file_that_is_there(tmp_path):
    """The check never upgrades a run: a worker whose last reply was filtered stays incomplete though its file exists."""
    cut = {"choices": [{"message": {"content": "half an ans", "tool_calls": []}, "finish_reason": "content_filter"}]}
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": GOOD}), cut)
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "treat as incomplete" in out and "verified" not in out, out
    assert "completed" not in _statuses(events)


# --- security regressions: what a declared output may never be, and what a passed check claims -------------------------------

PROTECTED = [".git/config", ".dream/state.json", "sub/.dream/approvals.json", ".remember/ledger.jsonl", ".env",
             "conf/.env.local", ".ssh/id_ed25519", "keys/id_rsa", ".GIT/hooks/pre-commit", "out\\result.json",
             "out/re\nsult.json"]


@pytest.mark.parametrize("path", PROTECTED)
async def test_dreams_gits_and_credential_files_are_never_a_declared_output(tmp_path, path):
    ran = []
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "x", "content": GOOD}),
                         _sub_final("done"), ran=ran)
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=[{"path": path}]))
    assert failed and "reason=invalid_contract" in out, out
    assert path not in out, "a refused value is not repeated"
    assert b._client.posted == [] and ran == [] and "completed" not in _statuses(events)


def _made_by(root: Path, make):
    """The file tools, with a write_file that leaves at `path` whatever `make` puts there."""
    async def write_file(a):
        target = root / a["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        make(target)
        return {"content": [{"type": "text", "text": "made"}]}
    return [t for t in _files(root) if t.name != "write_file"] + [_tool("write_file", write_file)]


async def test_a_link_to_a_file_inside_the_workspace_is_refused_too(tmp_path):
    """No link is followed, wherever it points: the check cannot tell a link's target from a later swap of it."""
    (tmp_path / "real.json").write_text(GOOD)
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "link_to": "../real.json"}),
                         _sub_final("done"))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=output_outside_workspace" in out and "symbolic link" in out, out
    assert "file:a.py" not in out
    rows = [e.data for e in events if e.kind == "agent_activity" and e.data.get("status") == "failed"]
    assert rows and all("reason=output_outside_workspace" in r["text"] for r in rows)
    assert all("result.json" not in r["text"] and "done" not in r["text"] for r in rows), "the row has no path, no reply"


@pytest.mark.parametrize("kind", ["a hard link", "a named pipe"])
async def test_an_output_that_is_not_a_plain_single_file_is_refused(tmp_path, kind):
    (tmp_path / "elsewhere.json").write_text(GOOD)
    make = {"a hard link": lambda target: os.link(tmp_path / "elsewhere.json", target),
            "a named pipe": os.mkfifo}[kind]
    b, _ = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json"}), _sub_final("done"),
                    tools=_made_by(tmp_path, make))
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))      # a pipe must not hang the check
    assert failed and "reason=output_invalid" in out and "file:a.py" not in out, out


def test_a_file_swapped_for_a_link_during_the_check_is_not_followed(tmp_path, monkeypatch):
    """The swap lands between the check's look at the name and its open: the open refuses the link."""
    from dream.core import worker_result
    workspace, outside = tmp_path / "ws", tmp_path / "outside"
    (workspace / "out").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.json").write_text(json.dumps({"nodes": ["SECRET"], "edges": []}))
    check = worker_result.OutputCheck(workspace, worker_result.parse_contract(None, _out()).outputs)
    target = workspace / "out/result.json"
    target.write_text(GOOD)
    real = os.stat

    def swap(path, *args, **kwargs):
        seen = real(path, *args, **kwargs)
        if path == "result.json" and kwargs.get("dir_fd") is not None:
            os.unlink(target)
            os.symlink(outside / "secret.json", target)
        return seen
    monkeypatch.setattr(worker_result.os, "stat", swap)
    passed, report, row = check.finish()
    monkeypatch.undo()
    assert not passed and "reason=output_outside_workspace" in report, report
    assert "SECRET" not in report and "verified" not in report
    assert "result.json" not in row and "reason=output_outside_workspace" in row


def test_a_passed_check_pins_the_bytes_it_read_and_does_not_claim_who_wrote_them(tmp_path):
    import hashlib
    from dream.core import worker_result
    check = worker_result.OutputCheck(tmp_path, worker_result.parse_contract(None, _out()).outputs)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.json").write_text(GOOD)                    # written by this test, not by any worker
    passed, report, row = check.finish()
    assert passed and row == "", report
    assert hashlib.sha256(GOOD.encode()).hexdigest() in report and f"{len(GOOD)} bytes" in report
    assert "Not checked: who wrote it" in report and "not only this worker" in report
    assert "file:a.py" not in report, "the report names the file and its hash; it never carries the content"


def test_the_check_reads_a_bounded_amount_even_when_the_file_grows(tmp_path, monkeypatch):
    """The size is looked at, then the read itself is capped: a file that grows after the look is still refused."""
    from dream.core import worker_result
    check = worker_result.OutputCheck(tmp_path, worker_result.parse_contract(None, _out()).outputs)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.json").write_text(GOOD)
    from types import SimpleNamespace
    real = os.fstat

    def small(fd):                                                     # the look says 1 byte; the file holds more
        seen = real(fd)
        return SimpleNamespace(st_mode=seen.st_mode, st_dev=seen.st_dev, st_ino=seen.st_ino, st_nlink=seen.st_nlink,
                               st_mtime_ns=seen.st_mtime_ns, st_ctime_ns=seen.st_ctime_ns, st_size=1)
    monkeypatch.setattr(worker_result, "MAX_OUTPUT_BYTES", len(GOOD) - 1)
    monkeypatch.setattr(worker_result.os, "fstat", small)
    passed, report, _ = check.finish()
    monkeypatch.undo()
    assert not passed and "reason=output_too_large" in report and "grew past" in report, report
    assert "file:a.py" not in report


# --- review fixes: the verdict survives the lead's result cap ----------------------------------------------------------------

class _Recording(_Engine):
    """An _Engine that also keeps each request body the lead streamed, as it was sent."""

    def __init__(self, *replies, **engine):
        super().__init__(*replies, **engine)
        self.streams = []

    def stream(self, method, url, json=None):
        self.streams.append(copy.deepcopy(json))
        return super().stream(method, url, json=json)


def _lead_task(args, final="noted"):
    """The lead's two rounds: one `task` call with these arguments, then its last words."""
    return [[_sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function": {
                "name": "task", "arguments": json.dumps(args)}}]}, "finish_reason": None}]}),
             _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}), "data: [DONE]"],
            _lead_final(final)]


DEEP = "/".join(["d" * 14] * 30)                       # 449 characters, 30 folders: a path near both limits
MANY = [{"path": f"{DEEP}/r{k:02}.json", "format": "json", "required_keys": ["nodes", "edges"]} for k in range(32)]


def _boast(size):
    """A worker reply longer than `size` that says, over and over in different words, that everything is done."""
    lines, k = [], 0
    while sum(map(len, lines)) <= size:
        k += 1
        lines.append(f"Item {k}: the file is written, checked and complete; nothing is left to do for part {k * 7}.\n")
    return "START-OF-REPLY\n" + "".join(lines) + "END-OF-REPLY"


@pytest.mark.parametrize("outputs", [_out(), MANY], ids=["one output", "32 outputs with long paths"])
async def test_the_lead_sees_the_failed_check_though_the_worker_reply_is_longer_than_the_result_cap(tmp_path, outputs):
    """The lead's history keeps a prefix of each result. A check reported after a long reply was cut away whole: the
    lead read a confident answer and nothing else. Looked at where the lead reads it: the tool message in its history
    and in the next request it sends."""
    from dream.core.backends import openai_compat
    cap = openai_compat._TOOL_RESULT_CAP
    reply = _boast(3 * cap)
    b, events = _session(tmp_path, _spec(*CODER))
    b._client = _Recording(_sub_final(reply), lead=_lead_task(_task(outputs=outputs)))
    with _workspace(tmp_path):
        turn = [event async for event in b.ask("map it")]
    total = len(outputs)
    kept = next(m for m in b.messages if m.get("role") == "tool")["content"]
    assert len(b._client.streams) == 2, "the lead was asked again after the task returned"
    sent = [str(m.get("content")) for m in b._client.streams[1]["messages"] if m.get("role") == "tool"]
    assert sent, "the next request carries the task's result"
    for content in (kept, sent[-1]):
        assert content.startswith("[Dream output check failed: reason=output_missing]"), content[:300]
        assert "NOT complete" in content
        assert f"{total} of {total} declared output(s) did not pass" in content
        assert "chars elided]" not in content and len(content) <= cap, "built to fit: the cap cut nothing off"
        assert content.index("NOT complete") < content.index("START-OF-REPLY"), "the verdict is ahead of the reply"
        assert "END-OF-REPLY" in content, "the reply is bounded by its middle; its start and end are kept"
    result = next(e.data for e in turn if e.kind == "tool_result" and e.data["name"] == "task")
    assert result["is_error"] and "reason=output_missing" in result["content"]
    statuses = _statuses(events)
    assert statuses[-1] == "failed" and "completed" not in statuses, statuses
    assert len(b._client.posted) == 1, "the worker was asked once and not sent back"


async def test_a_passed_check_is_not_cut_off_by_an_oversized_reply_either(tmp_path):
    from dream.core.backends import openai_compat
    cap = openai_compat._TOOL_RESULT_CAP
    reply = _boast(3 * cap)
    b, _ = _session(tmp_path, _spec(*CODER))
    b._client = _Recording(_call("write_file", {"path": "out/result.json", "content": GOOD}), _sub_final(reply),
                           lead=_lead_task(_task(outputs=_out())))
    with _workspace(tmp_path):
        turn = [event async for event in b.ask("map it")]
    kept = next(m for m in b.messages if m.get("role") == "tool")["content"]
    assert kept.startswith("START-OF-REPLY"), "a passed check keeps the worker's reply first"
    assert "[Dream output check: 1 declared output(s) verified]" in kept and "Not checked: who wrote it" in kept
    assert "chars elided]" not in kept and len(kept) <= cap
    assert not next(e.data for e in turn if e.kind == "tool_result" and e.data["name"] == "task")["is_error"]


def test_the_report_and_the_joined_result_stay_inside_their_limits(tmp_path):
    from dream.core import worker_result
    check = worker_result.OutputCheck(tmp_path, worker_result.parse_contract(None, MANY).outputs)
    full = check.finish(limit=1_000_000)[1]
    assert len(full) > 8000, "32 long paths: the unshortened report alone is a third of the default cap"
    for limit in (8000, 4000, 1200, 400):
        passed, report, row = check.finish(limit=limit)
        assert not passed and len(report) <= limit, (limit, len(report))
        assert report.startswith("[Dream output check failed: reason=output_missing] 32 of 32"), report[:120]
        assert "NOT complete" in report and "reason=output_missing" in row
    for cap in (24000, 2000, len(report) + 10):
        joined = worker_result.with_report("x" * 100_000, report, False, cap)
        assert joined.startswith(report) and len(joined) <= cap
        done = worker_result.with_report("y" * 100_000, "[report]", True, cap)
        assert done.endswith("[report]") and len(done) <= cap
    assert worker_result.with_report("short reply", "[report]", True, 24000) == "short reply\n\n[report]"


# --- review fixes: this Dream's own configured files, where they lie inside the workspace --------------------------------------

SECRET = "sk-DREAM252-never-repeat-this"


def _colocate(root: Path, monkeypatch):
    """Dream's home as `root`, the way DREAM_ROOT lays it out. config computes its paths at import, so a relocation
    is set on the module here, as conftest does; the two the source reads live from the environment are cleared."""
    from dream import config, extensions
    for name, path in (("DATA_DIR", root / "data"), ("VAR_DIR", root / "var"), ("LOOP_DIR", root / "var" / "loops"),
                       ("MCP_CONFIG_PATH", root / "mcp.json"), ("PLUGINS_DIR", root / "plugins"),
                       ("DB_PATH", root / "data" / "dream.db")):
        monkeypatch.setattr(config, name, path)
    monkeypatch.delenv("DREAM_EXTENSION_SETTINGS", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(root))
    # the role's tools are not what is under test, and the settings file below is a decoy, not a real one
    monkeypatch.setattr(extensions, "tool_enabled", lambda tool: True)


def _watch_reads(monkeypatch):
    """Every path the output check opens, recorded."""
    from dream.core import worker_result
    seen, real = [], worker_result._read

    def read(root, root_id, path):
        seen.append(path)
        return real(root, root_id, path)
    monkeypatch.setattr(worker_result, "_read", read)
    return seen


async def _refused_unread(tmp_path, monkeypatch, path, workspace=None):
    """Declare `path` (a decoy file holding SECRET is there) and assert the call is refused before anything is read,
    sent or run, with neither the path nor the content repeated."""
    workspace = workspace or tmp_path
    decoy = workspace / path
    decoy.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"nodes": [], "edges": [], "token": SECRET})
    decoy.write_text(body)
    reads, ran = _watch_reads(monkeypatch), []
    b, events = _session(workspace, _spec(*CODER), _call("write_file", {"path": path, "content": GOOD}),
                         _sub_final("done"), ran=ran)
    with _workspace(workspace):
        out, failed = await b._exec_tool("task", _task(outputs=[{"path": path, "format": "json"}]))
    assert failed and "reason=invalid_contract" in out, out
    assert path not in out and decoy.name not in out and SECRET not in out, "neither the value nor the content"
    assert reads == [], "refused before the baseline read"
    assert b._client.posted == [] and ran == [], "no request, no tool"
    assert decoy.read_text() == body
    assert _statuses(events) == ["running", "failed"] and b._workers == {}
    assert SECRET not in repr([e.data for e in events]) and path not in repr([e.data for e in events])


@pytest.mark.parametrize("path", ["data/config/extensions.json", "data/runtime-settings.json", "data/dream.db",
                                  "var/loops/run-1/state.json", "var/moe.json", "mcp.json", "plugins/p/plugin.yaml",
                                  ".codex/auth.json", ".claude/.credentials.json", "sub/.codex/auth.json",
                                  "DATA/Runtime-Settings.json"])
async def test_this_dreams_own_configured_files_in_the_workspace_are_never_a_declared_output(tmp_path, monkeypatch, path):
    _colocate(tmp_path, monkeypatch)
    await _refused_unread(tmp_path, monkeypatch, path)


RELOCATED = {
    "DREAM_EXTENSION_SETTINGS": ("conf/ext-settings.json", lambda root, patch: patch.setenv(
        "DREAM_EXTENSION_SETTINGS", str(root / "conf" / "ext-settings.json"))),
    "DREAM_MCP_CONFIG": ("servers/my-mcp.json", lambda root, patch: patch.setattr(
        "dream.config.MCP_CONFIG_PATH", root / "servers" / "my-mcp.json")),
    "a moved data folder": ("state/runtime-settings.json", lambda root, patch: patch.setattr(
        "dream.config.DATA_DIR", root / "state")),
    "CODEX_HOME": ("agent-home/auth.json", lambda root, patch: patch.setenv("CODEX_HOME", str(root / "agent-home"))),
    "CLAUDE_CONFIG_DIR": ("claude-home/creds.json", lambda root, patch: patch.setenv(
        "CLAUDE_CONFIG_DIR", str(root / "claude-home"))),
}


@pytest.mark.parametrize("name", RELOCATED)
async def test_a_relocated_settings_file_or_credential_home_inside_the_workspace_is_refused_too(tmp_path, monkeypatch, name):
    """The floor follows where the configuration really is, not a fixed name: none of these paths looks special."""
    workspace, elsewhere = tmp_path / "ws", tmp_path / "dream-home"
    workspace.mkdir()
    _colocate(elsewhere, monkeypatch)                    # Dream's home is elsewhere; only the one file is moved in
    path, relocate = RELOCATED[name]
    relocate(workspace, monkeypatch)
    await _refused_unread(tmp_path, monkeypatch, path, workspace=workspace)


def _credential_home(kind):
    """(the folder, how it is configured) for one credential or approval root placed under the test's own folder."""
    def codex(root, patch):
        patch.setenv("CODEX_HOME", str(root))

    def claude(root, patch):
        patch.setenv("CLAUDE_CONFIG_DIR", str(root))

    def default_codex(root, patch):                       # no variable: ~/.codex, with HOME as _colocate set it
        assert root.name == ".codex"
        patch.setenv("HOME", str(root.parent))

    def approvals(root, patch):                           # the folder of the extension trust records: <data>/config
        assert root.name == "config"
        patch.setattr("dream.config.DATA_DIR", root.parent)
    return {"CODEX_HOME": ("agent-home", codex), "CLAUDE_CONFIG_DIR": ("claude-home", claude),
            "the default ~/.codex": ("user/.codex", default_codex),
            "the approvals folder": ("state/config", approvals)}[kind]


CREDENTIAL_ROOTS = ["CODEX_HOME", "CLAUDE_CONFIG_DIR", "the default ~/.codex", "the approvals folder"]
# none of these names is refused by name alone: only where the workspace sits closes them
IN_A_CREDENTIAL_ROOT = {"its credential file": ("", "auth.json"), "another file in it": ("", "creds.json"),
                        "a file in a subfolder": ("", "out/result.json"),
                        "a workspace nested in it": ("sessions/s1", "auth.json"),
                        "an ordinary name in a nested workspace": ("sessions/s1", "out/result.json")}


@pytest.mark.parametrize("case", IN_A_CREDENTIAL_ROOT)
@pytest.mark.parametrize("kind", CREDENTIAL_ROOTS)
async def test_a_workspace_that_is_or_is_inside_a_credential_root_declares_no_output(tmp_path, monkeypatch, kind, case):
    """The floor looked only for configured places strictly INSIDE the workspace, so a workspace that was CODEX_HOME
    itself (or a folder in it) could declare auth.json and have it read, hashed and reported. Refused before any
    read, whichever way round the two folders sit."""
    _colocate(tmp_path / "dream-home", monkeypatch)
    where, configure = _credential_home(kind)
    home = tmp_path / where
    sub, path = IN_A_CREDENTIAL_ROOT[case]
    workspace = home / sub if sub else home
    workspace.mkdir(parents=True)
    configure(home, monkeypatch)
    await _refused_unread(tmp_path, monkeypatch, path, workspace=workspace)


async def test_a_credential_root_reached_through_a_link_is_the_same_folder(tmp_path, monkeypatch):
    """CODEX_HOME is configured as a link; the workspace is the folder it points at. Both spellings are compared."""
    _colocate(tmp_path / "dream-home", monkeypatch)
    workspace = tmp_path / "real-home"
    workspace.mkdir()
    os.symlink(workspace, tmp_path / "linked-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "linked-home"))
    await _refused_unread(tmp_path, monkeypatch, "auth.json", workspace=workspace)


RUN_RECORDS = ["state.json", "ledger.jsonl", "contract.md", ".lock", "STATE.JSON", "Ledger.jsonl",
               ".state.json-k3x9qa",
               # the goal and the log the loop writes into the run folder, and the temporaries an atomic
               # replacement of a record leaves beside it (`.<name>-*`)
               "goal.md", "log.md", "GOAL.md", "Log.MD",
               ".goal.md-k3x9qa", ".log.md-k3x9qa", ".contract.md-k3x9qa", ".LOG.md-K3X9QA", ".goal.md-"]


@pytest.mark.parametrize("path", RUN_RECORDS)
async def test_a_loop_runs_managed_records_are_never_a_declared_output_of_its_run_folder(tmp_path, monkeypatch, path):
    """The workspace is the run folder itself, so nothing configured lies inside it -- and the run's ledger, state
    view, lock, contract, goal and log were declarable. The loop reserves them: only progress.md is the worker's
    there."""
    _colocate(tmp_path, monkeypatch)
    workspace = tmp_path / "var" / "loops" / "run-1"
    workspace.mkdir(parents=True)
    await _refused_unread(tmp_path, monkeypatch, path, workspace=workspace)


@pytest.mark.parametrize("path", ["run-1/state.json", "run-1/ledger.jsonl", "run-1/contract.md",
                                  "run-1/goal.md", "run-1/log.md", "run-1/.goal.md-k3x9qa",
                                  "run-1/.log.md-k3x9qa", "run-1/.contract.md-k3x9qa", "RUN-1/Goal.md"])
async def test_a_runs_managed_records_are_refused_from_the_loops_folder_too(tmp_path, monkeypatch, path):
    _colocate(tmp_path, monkeypatch)
    workspace = tmp_path / "var" / "loops"
    workspace.mkdir(parents=True)
    await _refused_unread(tmp_path, monkeypatch, path, workspace=workspace)


@pytest.mark.parametrize("path", ["state.json", "ledger.jsonl", "contract.md", "goal.md", "log.md",
                                  ".goal.md-k3x9qa", ".log.md-k3x9qa", ".contract.md-k3x9qa"])
async def test_a_relocated_loops_folder_keeps_its_runs_records_closed(tmp_path, monkeypatch, path):
    """Where the runs are is read from the configuration when the check is made, not fixed as var/loops."""
    _colocate(tmp_path / "dream-home", monkeypatch)
    monkeypatch.setattr("dream.config.LOOP_DIR", tmp_path / "elsewhere" / "runs")
    workspace = tmp_path / "elsewhere" / "runs" / "run-9"
    workspace.mkdir(parents=True)
    await _refused_unread(tmp_path, monkeypatch, path, workspace=workspace)


def test_the_placement_refusals_come_before_any_read_and_name_no_record(tmp_path, monkeypatch):
    """The same two refusals at the check itself, with no backend: raised by the constructor, before the baseline
    read, as invalid_contract, in words that repeat neither the path nor any managed record's name."""
    from dream.core import worker_result
    _colocate(tmp_path, monkeypatch)
    run, home = tmp_path / "var" / "loops" / "run-1", tmp_path / "agent-home"
    run.mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    reads = _watch_reads(monkeypatch)
    for workspace, path in ((run, "ledger.jsonl"), (home, "auth.json"), (home, "out/result.json")):
        (workspace / path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / path).write_text(json.dumps({"nodes": [], "edges": [], "token": SECRET}))
        outputs = worker_result.parse_contract(None, [{"path": path, "format": "json"}]).outputs
        with pytest.raises(worker_result.ContractError) as refused:
            worker_result.OutputCheck(workspace, outputs)
        text = str(refused.value)
        assert refused.value.reason == "invalid_contract" and text.startswith("outputs[0].path"), text
        assert SECRET not in text and path not in text
        assert not any(name in text for name in ("state.json", "ledger.jsonl", "contract.md", "auth.json"))
    assert reads == []
    # the goal, the log and a record's atomic-replacement temporary: the same refusal, from the run folder, from the
    # loops folder above it, and from a run of a loops folder configured elsewhere
    loops, moved = tmp_path / "var" / "loops", tmp_path / "elsewhere" / "runs"
    (moved / "run-9").mkdir(parents=True)
    records = ("goal.md", "log.md", ".goal.md-k3x9qa", ".log.md-k3x9qa", ".contract.md-k3x9qa")
    for loop_dir, workspace, prefix in ((loops, run, ""), (loops, loops, "run-1/"), (moved, moved / "run-9", ""),
                                        (moved, moved, "run-9/")):
        monkeypatch.setattr("dream.config.LOOP_DIR", loop_dir)
        for record in records:
            path = prefix + record
            (workspace / path).write_text(json.dumps({"nodes": [], "edges": [], "token": SECRET}))
            outputs = worker_result.parse_contract(None, [{"path": "out/first.json"}, {"path": path}]).outputs
            with pytest.raises(worker_result.ContractError) as refused:
                worker_result.OutputCheck(workspace, outputs)
            text = str(refused.value)
            # the refusal is of the record (the second output), before the first output's baseline is read
            assert refused.value.reason == "invalid_contract" and text.startswith("outputs[1].path"), text
            assert "harness-managed" in text and SECRET not in text and path not in text
            assert not any(name in text.lower() for name in (
                "goal.md", "log.md", "contract.md", "state.json", "ledger.jsonl", "k3x9qa", "run-1", "run-9"))
    assert reads == []
    monkeypatch.setattr("dream.config.LOOP_DIR", loops)
    # the same folders with nothing declared, and the run folder's own output, are not refused
    worker_result.OutputCheck(home, ())
    check = worker_result.OutputCheck(run, worker_result.parse_contract(None, _out()).outputs)
    assert reads == ["out/result.json"] and check.before == [None]


ALLOWED = {
    "an unrelated project's data folder": ("project", "data/result.json"),
    "an unrelated project's var/loops": ("project", "var/loops/result.json"),
    "an unrelated project's mcp.json": ("project", "mcp.json"),
    "Understand's batch file beside Dream's own folders": ("", ".ua/intermediate/batch-1.json"),
    "a workspace that is a run folder under var/loops": ("var/loops/run-1", "out/result.json"),
    "the worker's own progress.md in a run folder": ("var/loops/run-1", "progress.md"),
    "another top-level file of a run folder": ("var/loops/run-1", "result.json"),
    "a record's name in a subfolder of a run folder": ("var/loops/run-1", "out/state.json"),
    "a record's name in a workspace deeper in a run folder": ("var/loops/run-1/work", "state.json"),
    "a record's name in a project that is not a run folder": ("project", "state.json"),
    "a contract.md in an unrelated project": ("project", "contract.md"),
    "a goal.md in an unrelated project": ("project", "goal.md"),
    "a log.md in an unrelated project": ("project", "log.md"),
    "a goal.md in a subfolder of a run folder": ("var/loops/run-1", "out/goal.md"),
    "a log.md in a subfolder of a run folder": ("var/loops/run-1", "out/log.md"),
    "a log.md in a workspace deeper in a run folder": ("var/loops/run-1/work", "log.md"),
    "a name that only ends like the log's": ("var/loops/run-1", "catalog.md"),
    "a name that only starts like the goal's": ("var/loops/run-1", "goal.md.json"),
    "a dot-name that is no record's temporary": ("var/loops/run-1", ".goal.mdx-notes.json"),
    "progress.md declared from the loops folder": ("var/loops", "run-1/progress.md"),
    "a workspace that is Dream's data folder": ("data", "notes/result.json"),
    "a workspace that is Dream's var folder": ("var", "scratch/result.json"),
    "a workspace beside a credential root": ("agent-home-2", "auth.json"),
}


@pytest.mark.parametrize("name", ALLOWED)
async def test_the_floor_does_not_reach_past_this_dreams_own_files(tmp_path, monkeypatch, name):
    """`data`, `var` and `mcp.json` are ordinary names in a project that is not Dream's home; `.ua/` stays an output
    place even where Dream's home is the workspace; a workspace inside Dream's folders is not refused for that. A
    run folder keeps its outputs and its progress.md: only its managed records, at its top, are closed. A folder
    whose name merely starts like a credential root's is not that root."""
    _colocate(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "agent-home"))
    sub, path = ALLOWED[name]
    workspace = tmp_path / sub if sub else tmp_path
    workspace.mkdir(parents=True, exist_ok=True)
    b, events = _session(workspace, _spec(*CODER), _call("write_file", {"path": path, "content": GOOD}),
                         _sub_final("written"))
    with _workspace(workspace):
        out, failed = await b._exec_tool("task", _task(outputs=_out(path)))
    assert not failed and out.startswith("written") and "verified" in out, out
    assert _statuses(events) == ["running", "completed"]


# --- review fixes: a path the filesystem cannot take, or that does not show as what it is ----------------------------------------

UNPRINTABLE = {
    "a lone surrogate": "out/re\ud800sult.json",
    "a C1 control": "out/re\x85sult.json",
    "DEL": "out/re\x7fsult.json",
    "a zero-width space": "out/re​sult.json",
    "a right-to-left override": "out/‮nosj.tluser",
    "a line separator": "out/re sult.json",
    "a no-break space": "out/re sult.json",
    "a byte-order mark": "﻿out/result.json",
    "a private-use character": "out/result.json",
}


@pytest.mark.parametrize("path", UNPRINTABLE.values(), ids=UNPRINTABLE.keys())
async def test_an_unencodable_or_unprintable_path_is_refused_before_the_filesystem_is_touched(tmp_path, monkeypatch, path):
    reads, ran = _watch_reads(monkeypatch), []
    b, events = _session(tmp_path, _spec(*CODER), _call("write_file", {"path": "out/result.json", "content": GOOD}),
                         _sub_final("done"), ran=ran)
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=[{"path": path}]))
    assert failed and "reason=invalid_contract" in out, out
    assert path not in out and not set(out) & set(path) - set("out/result.json"), "the refused value is not repeated"
    assert reads == [] and b._client.posted == [] and ran == [] and not (tmp_path / "out").exists()
    assert _statuses(events) == ["running", "failed"], "the card ends: it is not left running"
    assert b._workers == {}, "the worker is no longer registered"


async def test_a_lone_surrogate_from_the_leads_json_ends_the_call_as_a_refusal_and_the_turn_goes_on(tmp_path):
    """Through the lead's turn, the path as JSON carries it: an escaped unpaired surrogate. It raised
    UnicodeEncodeError out of the preflight, past a worker card left `running`."""
    args = _task(outputs=[{"path": "out/re\ud800sult.json", "format": "json"}])
    assert "\\ud800" in json.dumps(args)
    b, events = _session(tmp_path, _spec(*CODER))
    b._client = _Recording(_sub_final("done"), lead=_lead_task(args, "the contract was refused"))
    with _workspace(tmp_path):
        turn = [event async for event in b.ask("map it")]
    result = next(e.data for e in turn if e.kind == "tool_result" and e.data["name"] == "task")
    assert result["is_error"] and "reason=invalid_contract" in result["content"], result
    assert "\ud800" not in result["content"] and "UnicodeEncodeError" not in result["content"]
    kept = next(m for m in b.messages if m.get("role") == "tool")["content"]
    assert "reason=invalid_contract" in kept and "\ud800" not in kept
    assert b._client.posted == [], "no worker request"
    statuses = _statuses(events)
    assert statuses and statuses[-1] == "failed" and "completed" not in statuses, statuses
    assert b._workers == {}
    assert not next(e.data for e in turn if e.kind == "result").get("is_error"), "the lead's turn goes on"
    assert len(b._client.streams) == 2


async def test_an_error_inside_the_contract_preflight_is_a_typed_refusal_with_a_terminal_row(tmp_path, monkeypatch):
    """Whatever the preflight raises: a refusal, a failed row, the worker unregistered -- and only the error's type,
    because its message can quote the value."""
    b, events = _session(tmp_path, _spec(*CODER), _sub_final("done"))

    def broken(spec, subagent_type, raw):
        raise RuntimeError("QUOTED-VALUE")
    monkeypatch.setattr(b, "_task_contract", broken)
    with _workspace(tmp_path):
        out, failed = await b._exec_tool("task", _task(outputs=_out()))
    assert failed and "reason=invalid_contract" in out and "RuntimeError" in out and "QUOTED-VALUE" not in out, out
    assert b._client.posted == [] and _statuses(events) == ["running", "failed"] and b._workers == {}
    assert "QUOTED-VALUE" not in repr([e.data for e in events])


# --- Understand: its file-writing worker tasks carry the contract ----------------------------------------------------------

def test_required_keys_checks_presence_only_and_understand_says_no_more_than_that(tmp_path):
    """`required_keys` is key presence. A batch file whose "nodes" is not a list passes Dream's check and is caught
    by the glue's own; the skill's wording claims the first and no more."""
    from dream.core import worker_result
    from test_understand_skill import BIG, _glue, _hand_project
    check = worker_result.OutputCheck(tmp_path, worker_result.parse_contract(None, _out()).outputs)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.json").write_text(json.dumps({"nodes": "not a list", "edges": 3}))
    passed, report, _ = check.finish()
    assert passed and "keys nodes, edges present" in report, report
    project = tmp_path / "project"
    project.mkdir()
    _hand_project(project, BIG)
    listing = _glue(project, "tasks")
    assert "both keys present" in listing and "not the graph's shape" in listing
    assert "both lists" not in listing
    skill = (Path(__file__).resolve().parent.parent / "skills" / "understand" / "SKILL.md").read_text(encoding="utf-8")
    assert "not that they are lists, not the graph schema" in skill


def test_understand_plans_a_contract_for_every_file_writing_task(tmp_path):
    from test_understand_skill import BIG, _glue, _hand_project, _plan
    _hand_project(tmp_path, BIG)
    listing = _glue(tmp_path, "tasks")
    plan = _plan(tmp_path)
    assert len(plan) > 3
    for task in plan:
        contract = task["contract"]
        assert set(contract) == {"required_tools", "outputs"}
        assert contract["required_tools"] == ["read_file", "write_file", "run_bash"]
        assert contract["outputs"] == [{"path": task["file"], "format": "json", "required_keys": ["nodes", "edges"]}]
    # the lead is told to send it: the two fields by name, and each id's file
    assert 'subagent_type "coder"' in listing, "the role that can write stays the one named"
    assert "required_tools" in listing and "outputs" in listing
    assert all(f"{task['id']}={task['file']}" in listing for task in plan)


async def test_an_understand_task_contract_passes_for_coder_and_refuses_explorer(tmp_path):
    from test_understand_skill import BIG, _glue, _hand_project, _part, _plan
    _hand_project(tmp_path, BIG)
    _glue(tmp_path, "tasks")
    task = _plan(tmp_path)[0]
    contract = task["contract"]
    roles = {name: spec for name, spec in local_subagents().items() if name in ("coder", "explorer")}
    assert "write_file" in roles["coder"].tool_names and "write_file" not in roles["explorer"].tool_names
    ran = []
    recall = _tool("recall", _files(tmp_path)[0].handler)
    b, events = _session(tmp_path, roles, tools=_files(tmp_path, ran) + [recall])
    prompt = f"Run `python3 .ua/tmp/ua_glue.py brief {task['id']}` with run_bash and do exactly what it prints."
    with _workspace(tmp_path):
        # the read-only role is refused before it is asked anything; it is not given a way to write
        out, failed = await b._exec_tool("task", _task(prompt, "explorer", **contract))
        assert failed and "reason=incompatible_role" in out and "write_file" in out, out
        assert b._client.posted == [] and ran == [] and not (tmp_path / task["file"]).exists()
        # the coder writes the batch file its brief asks for; the contract's own check passes it
        b._client.replies = [_call("write_file", {"path": task["file"], "content": json.dumps(_part(task))}),
                             _sub_final(f"task {task['id']}: {task['file']}\ncheck: ok\nnotes: none")]
        out, failed = await b._exec_tool("task", _task(prompt, "coder", **contract))
    assert not failed and out.startswith(f"task {task['id']}: {task['file']}"), out
    assert _glue(tmp_path, "check", task["id"]).startswith("ok: " + task["file"]), "the glue's own check agrees"
    assert _statuses(events)[-1] == "completed"
