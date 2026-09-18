"""Visible media delivery stays self-contained and bounded."""
import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup

from dream.gui import bundle as assets
from dream.tools import studio as studio_tools


def prepare(tmp_path, html):
    page = tmp_path / "page.html"
    page.write_text(html)
    return assets.prepare_studio_media(page, html)


def test_local_media_and_poster_inline_without_changing_file(tmp_path):
    for name in ("clip one.mp4", "voice.mp3", "captions.vtt", "poster.png"):
        (tmp_path / name).write_bytes(b"fixture")
    html = '<video poster="poster.png"><source src="clip%20one.mp4?v=1#t=1"><track src="captions.vtt"></video><audio src="voice.mp3"></audio>'
    out = prepare(tmp_path, html)
    soup = BeautifulSoup(out, "html.parser")
    for tag, attr, mime in [("video", "poster", "image/png"), ("source", "src", "video/mp4"), ("track", "src", "text/vtt"), ("audio", "src", "audio/mpeg")]:
        assert soup.find(tag)[attr] == f"data:{mime};base64," + base64.b64encode(b"fixture").decode()
    assert (tmp_path / "page.html").read_text() == html


@pytest.mark.parametrize("html", ["<!doctype html><h1>Hi</h1>", '<video src="https://example.test/v.mp4"></video>', '<video src="data:video/mp4;base64,AA=="></video>', '<script>let x = `<video src="missing.mp4">`;</script>'])
def test_no_local_media_preserves_exact_html(tmp_path, html):
    assert prepare(tmp_path, html) == html


@pytest.mark.parametrize("ref", ["missing.mp4", "../outside.mp4", "%2e%2e/outside.mp4", "/etc/passwd", "file:///etc/passwd", "notes.txt"])
def test_invalid_local_media_is_explicit(tmp_path, ref):
    (tmp_path / "notes.txt").write_text("private")
    with pytest.raises(ValueError):
        prepare(tmp_path, f'<video src="{ref}"></video>')


def test_symlink_escape_refused(tmp_path):
    page_dir = tmp_path / "page"
    page_dir.mkdir()
    (tmp_path / "outside.mp4").write_bytes(b"private")
    (page_dir / "clip.mp4").symlink_to(tmp_path / "outside.mp4")
    with pytest.raises(ValueError):
        prepare(page_dir, '<video src="clip.mp4"></video>')


def test_size_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "MAX_ASSET_BYTES", 4)
    monkeypatch.setattr(assets, "MAX_STUDIO_MEDIA_BYTES", 6)
    (tmp_path / "clip.mp4").write_bytes(b"12345")
    with pytest.raises(ValueError, match="limit"):
        prepare(tmp_path, '<video src="clip.mp4"></video>')
    (tmp_path / "clip.mp4").write_bytes(b"1234")
    with pytest.raises(ValueError, match="limit"):
        prepare(tmp_path, '<video src="clip.mp4"></video><video src="clip.mp4"></video>')


def test_picture_source_preserved(tmp_path):
    html = '<picture><source src="picture.png"></picture>'
    assert prepare(tmp_path, html) == html


@pytest.mark.parametrize("swap", ["file", "ancestor", "fifo"])
def test_path_replacement_cannot_redirect_read(tmp_path, monkeypatch, swap):
    page_dir = tmp_path / "page"
    page_dir.mkdir()
    clip = page_dir / "clip.mp4"
    clip.write_bytes(b"safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "clip.mp4").write_bytes(b"private")
    original_open = os.open
    triggered = False

    def race_open(path, flags, *args, **kwargs):
        nonlocal triggered
        target = "page" if swap == "ancestor" else "clip.mp4"
        if path == target and not triggered:
            triggered = True
            if swap == "ancestor":
                page_dir.rename(tmp_path / "original")
                page_dir.symlink_to(outside, target_is_directory=True)
            else:
                clip.unlink()
                if swap == "fifo":
                    os.mkfifo(clip)
                else:
                    clip.symlink_to(outside / "clip.mp4")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", race_open)
    with pytest.raises(ValueError, match="Studio media preparation"):
        prepare(page_dir, '<video src="clip.mp4"></video>')
    assert triggered


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [studio_tools.show_to_user, studio_tools.done])
async def test_delivery_reports_preparation_failure(tmp_path, monkeypatch, tool):
    page = tmp_path / "page.html"
    page.write_text('<video src="missing.mp4"></video>')
    events = []
    monkeypatch.setattr(studio_tools, "ctx", lambda: SimpleNamespace(workspace=tmp_path, emit=events.append))
    monkeypatch.setattr(studio_tools, "studio", lambda: SimpleNamespace(ready=True))
    monkeypatch.setattr(studio_tools, "_load", AsyncMock(return_value=[]))
    result = await tool.handler({"path": "page.html"})
    assert result.get("is_error")
    assert "media" in result["content"][0]["text"].lower()
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [studio_tools.show_to_user, studio_tools.done])
async def test_delivery_retains_and_emits_embedded_video(tmp_path, monkeypatch, tool):
    page = tmp_path / "page.html"
    page.write_text('<video controls src="clip.mp4"></video>')
    (tmp_path / "clip.mp4").write_bytes(b"fixture")
    events, retained = [], []
    monkeypatch.setattr(studio_tools, "ctx", lambda: SimpleNamespace(workspace=tmp_path, emit=events.append))
    monkeypatch.setattr(studio_tools, "studio", lambda: SimpleNamespace(ready=True, retain_show=retained.append))
    monkeypatch.setattr(studio_tools, "_load", AsyncMock(return_value=[]))
    result = await tool.handler({"path": "page.html"})
    assert not result.get("is_error")
    assert len(events) == 1 and retained == events
    assert "data:video/mp4;base64," in events[0].data["content"]
    assert "data:" not in page.read_text()
