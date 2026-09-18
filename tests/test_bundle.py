"""super_inline_html's engine: local assets become data, remote stays, escapes hold."""

from __future__ import annotations

import base64

import pytest

from dream.gui.bundle import bundle

PAGE = """<!doctype html><html><head>
<template id="__bundler_thumbnail"><svg viewBox="0 0 10 10"><rect width="10" height="10" fill="#0af"/></svg></template>
<link rel="stylesheet" href="css/site.css">
<script src="app.js"></script>
<script src="https://cdn.example.com/lib.js"></script>
<style>body{background:url(img/bg.png)}</style>
</head><body>
<img src="img/logo.png"><img src="https://x.test/remote.png"><img src="../secret.png">
<div style="background-image:url('img/bg.png')"></div>
</body></html>"""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"; (root / "css").mkdir(parents=True); (root / "img").mkdir()
    (root / "index.html").write_text(PAGE)
    (root / "css" / "site.css").write_text("h1{color:red;background:url(../img/bg.png)}")
    (root / "app.js").write_text("console.log('a </script> b')")
    (root / "img" / "logo.png").write_bytes(b"\x89PNG-logo")
    (root / "img" / "bg.png").write_bytes(b"\x89PNG-bg")
    (tmp_path / "secret.png").write_bytes(b"nope")
    return root


def test_local_assets_are_inlined_and_remote_or_outside_are_left(site):
    out, report = bundle(site / "index.html")
    logo = base64.b64encode(b"\x89PNG-logo").decode()
    bg = base64.b64encode(b"\x89PNG-bg").decode()
    assert f"data:image/png;base64,{logo}" in out
    assert out.count(f"data:image/png;base64,{bg}") == 3, "style, stylesheet, inline style"
    assert "console.log('a <\\/script> b')" in out, "a script's own closing tag is escaped"
    assert 'src="app.js"' not in out and "<style>h1{color:red" in out
    assert "https://cdn.example.com/lib.js" in out and "https://x.test/remote.png" in out
    assert "../secret.png" in out and "nope" not in out
    assert any("left alone: script https://" in r for r in report)
    assert sum(r.startswith("inlined") for r in report) >= 5


def test_the_thumbnail_template_is_required(tmp_path):
    (tmp_path / "p.html").write_text("<!doctype html><p>hi</p>")
    with pytest.raises(ValueError, match="__bundler_thumbnail"):
        bundle(tmp_path / "p.html")


def test_a_huge_asset_is_left_alone_with_a_note(site):
    big = site / "img" / "big.png"
    big.write_bytes(b"\0" * (8 * 1024 * 1024 + 1))
    (site / "index.html").write_text(PAGE.replace('<img src="img/logo.png">', '<img src="img/big.png">'))
    out, report = bundle(site / "index.html")
    assert 'src="img/big.png"' in out and any("over 8 MB" in r for r in report)
