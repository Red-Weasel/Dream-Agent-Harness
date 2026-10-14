"""2026-10-12 security review (DREAM-290): library_materialize's `dest` was not a path the policy checked. In Accept
edits and Auto a model could write Library bytes to any host path (~/.bashrc) without the outside-workspace ask that
write_file gets; in Ask the reason said "write in workspace", and an "always" answer, keyed by tool name, then covered
every later destination. A relative `dest` also resolved against Dream's process directory, not the workspace the
policy judged it against."""
from __future__ import annotations

import pytest

from dream.core import policy
from dream.library.store import Library
from dream.tools import library_tools
from dream.tools.context import ToolContext, bind_context


@pytest.mark.parametrize("mode", ["ask", "accept-edits", "auto"])
def test_a_destination_outside_the_workspace_is_decided_like_write_file(tmp_path, mode):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "home" / ".bashrc"
    materialize = policy.decide("library_materialize", {"file_id": "x", "dest": str(outside)}, mode, ws)
    write = policy.decide("write_file", {"path": str(outside), "content": "x"}, mode, ws)
    assert materialize == write
    assert materialize[0] != "allow" and materialize[1].startswith("outside workspace")


@pytest.mark.parametrize("mode", ["ask", "accept-edits", "auto"])
def test_a_destination_inside_the_workspace_is_decided_like_write_file(tmp_path, mode):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert (policy.decide("library_materialize", {"file_id": "x", "dest": "notes/a.md"}, mode, ws)
            == policy.decide("write_file", {"path": "notes/a.md", "content": "x"}, mode, ws))


async def test_a_relative_destination_lands_in_the_workspace(tmp_path, monkeypatch):
    store = Library(tmp_path / "library.db", tmp_path / "blobs")
    monkeypatch.setattr(library_tools, "_LIB", store)
    ws, elsewhere = tmp_path / "ws", tmp_path / "elsewhere"
    ws.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)                      # Dream's process directory is not the workspace
    f = store.create(b"content", name="a.md")
    with bind_context(ToolContext(store=None, working=None, browser=None, session_id="t", workspace=ws)):
        result = await library_tools.library_materialize.handler({"file_id": f.id, "dest": "out/a.md"})
    assert not result.get("is_error"), result
    assert (ws / "out" / "a.md").read_bytes() == b"content" and not (elsewhere / "out").exists()


@pytest.mark.parametrize("dest", ["", "   "])
async def test_an_empty_destination_is_refused_and_writes_nothing(tmp_path, monkeypatch, dest):
    """(gate round 1, finding 4) The policy has no path to judge in an empty `dest` ("write in workspace"), and the store
    resolved it to Dream's process directory: a write there with no outside-workspace ask. It is refused."""
    store = Library(tmp_path / "library.db", tmp_path / "blobs")
    monkeypatch.setattr(library_tools, "_LIB", store)
    ws, elsewhere = tmp_path / "ws", tmp_path / "elsewhere"
    ws.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    f = store.create(b"planted", name=".bashrc")
    with bind_context(ToolContext(store=None, working=None, browser=None, session_id="t", workspace=ws)):
        result = await library_tools.library_materialize.handler({"file_id": f.id, "dest": dest})
    assert result.get("is_error") and "dest" in result["content"][0]["text"]
    assert list(elsewhere.iterdir()) == [] and list(ws.iterdir()) == []
