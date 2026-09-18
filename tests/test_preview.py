"""The hidden model frame: a real headless Chromium, driven the way the Studio
tools drive it. No network — the frame must block it, and these tests prove it."""

from __future__ import annotations

import pytest

from dream.gui.preview import Preview

pytestmark = pytest.mark.asyncio

PAGE = """<!doctype html><html><head><style>body{background:#123;color:#fff}</style></head>
<body><h1 id="t">hello</h1>
<script>
console.log("log line"); console.warn("warn line");
window.n = 41;
setTimeout(() => { throw new Error("late boom") }, 20);
</script></body></html>"""


@pytest.fixture
async def pv():
    p = Preview()
    yield p
    await p.aclose()


async def test_load_collects_console_and_late_errors(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    logs = await pv.load(f)
    assert "console.log: log line" in logs and "console.warning: warn line" in logs
    assert any(l.startswith("error:") and "late boom" in l for l in logs)
    assert pv.loaded == f.resolve() and pv.running


async def test_a_reload_clears_the_previous_logs(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    g = tmp_path / "b.html"
    g.write_text("<!doctype html><p>quiet</p>")
    assert await pv.load(g) == []
    assert pv.logs == []


async def test_eval_returns_expressions_and_statement_blocks(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    assert await pv.eval("window.n + 1") == 42
    assert await pv.eval("document.getElementById('t').textContent") == "hello"
    assert await pv.eval("const x = 2; const y = 3; return x * y;") == 6
    with pytest.raises(Exception):
        await pv.eval("throw new Error('nope')")


async def test_state_survives_between_evals(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    await pv.eval("window.k = 'kept'")
    assert await pv.eval("window.k") == "kept"


async def test_network_is_blocked_but_local_files_load(pv, tmp_path):
    (tmp_path / "style.css").write_text("body{background:rgb(1, 2, 3)}")
    f = tmp_path / "net.html"
    f.write_text('<!doctype html><link rel="stylesheet" href="style.css">'
                 '<img id="i" src="http://127.0.0.1:9/never.png"><p>x</p>')
    await pv.load(f)
    assert await pv.eval("getComputedStyle(document.body).backgroundColor") == "rgb(1, 2, 3)"
    got = await pv.eval("fetch('http://example.com/').then(() => 'ok').catch(() => 'blocked')")
    assert got == "blocked"
    assert any(u.startswith("http://127.0.0.1:9/") for u in pv.blocked)
    assert any(l.startswith("blocked: http://example.com/") for l in pv.logs)


async def test_screenshot_steps_capture_in_order_with_prefixes(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    out = await pv.screenshot(
        [{"delay": 50}, {"code": "document.body.style.background='#fff'", "delay": 50}],
        save_to=tmp_path / "shots" / "hero.png",
    )
    assert [p.name for p in out] == ["01-hero.png", "02-hero.png"]
    a, b = (p.read_bytes() for p in out)
    assert a[:8] == b"\x89PNG\r\n\x1a\n" and b[:8] == a[:8]
    assert a != b, "the second step changed the page, so the capture must differ"


async def test_single_step_saves_without_a_prefix_and_jpeg_by_default(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    (out,) = await pv.screenshot([{}], save_to=tmp_path / "one.jpg")
    assert out.name == "one.jpg" and out.read_bytes()[:3] == b"\xff\xd8\xff"


async def test_in_memory_captures_are_png_under_the_key(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    assert await pv.screenshot([{}, {}], key="deck") == []
    blobs = pv.captures["deck"]
    assert len(blobs) == 2 and all(b[:8] == b"\x89PNG\r\n\x1a\n" for b in blobs)


async def test_screenshot_refuses_bad_arguments(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    with pytest.raises(ValueError):
        await pv.screenshot([{}])  # neither destination
    with pytest.raises(ValueError):
        await pv.screenshot([{}], save_to=tmp_path / "x.png", key="k")  # both
    with pytest.raises(ValueError):
        await pv.screenshot([], save_to=tmp_path / "x.png")


async def test_aclose_is_idempotent_and_a_new_load_relaunches(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    await pv.aclose()
    await pv.aclose()
    assert not pv.running and pv.loaded is None
    await pv.load(f)
    assert pv.running


# --- hardening: floods, wedged scripts, popups, interleaving ----------------------------


async def test_a_console_flood_is_bounded(pv, tmp_path):
    from dream.gui.preview import MAX_LOG_LINES

    f = tmp_path / "flood.html"
    f.write_text("<!doctype html><script>for(let i=0;i<5000;i++) console.log('line '+i)</script>")
    logs = await pv.load(f)
    assert len(logs) == MAX_LOG_LINES + 1
    assert logs[-1].startswith("[… ") and "dropped" in logs[-1]


async def test_an_eval_that_never_finishes_times_out_and_the_frame_recovers(pv, tmp_path, monkeypatch):
    from dream.gui import preview as mod

    monkeypatch.setattr(mod, "EVAL_TIMEOUT_MS", 800)
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    with pytest.raises(TimeoutError) as e:
        await pv.eval("new Promise(() => {})")
    assert "reset" in str(e.value)
    assert pv.loaded is None
    # usable again after a reload
    await pv.load(f)
    assert await pv.eval("1 + 1") == 2


async def test_a_load_that_never_completes_errors_and_the_frame_recovers(pv, tmp_path, monkeypatch):
    from dream.gui import preview as mod

    monkeypatch.setattr(mod, "LOAD_TIMEOUT_MS", 800)
    f = tmp_path / "spin.html"
    f.write_text("<!doctype html><script>while(true){}</script>")
    with pytest.raises(Exception):
        await pv.load(f)
    g = tmp_path / "ok.html"
    g.write_text(PAGE)
    await pv.load(g)
    assert await pv.eval("window.n") == 41


async def test_a_popup_is_closed_and_logged(pv, tmp_path):
    f = tmp_path / "pop.html"
    f.write_text("<!doctype html><script>window.open('about:blank')</script><p>x</p>")
    logs = await pv.load(f)
    await pv._page.wait_for_timeout(100)
    assert any("popup" in l for l in pv.logs)
    assert len(pv._context.pages) == 1


async def test_concurrent_loads_do_not_interleave(pv, tmp_path):
    import asyncio

    a = tmp_path / "a.html"; a.write_text("<!doctype html><script>console.log('A')</script>")
    b = tmp_path / "b.html"; b.write_text("<!doctype html><script>console.log('B')</script>")
    la, lb = await asyncio.gather(pv.load(a), pv.load(b))
    assert la == ["console.log: A"] and lb == ["console.log: B"]


# --- Gate 4a observations folded in ---------------------------------------------------


async def test_a_malformed_svg_is_reported_not_clean(pv, tmp_path):
    f = tmp_path / "bad.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width=10 height=10><rect/></svg>')
    logs = await pv.load(f)
    assert any("did not parse" in l for l in logs)


async def test_a_page_that_navigates_away_is_reported_and_eval_refuses(pv, tmp_path):
    f = tmp_path / "leave.html"
    f.write_text("<!doctype html><script>location.href = 'http://example.com/'</script>")
    logs = await pv.load(f)
    await pv._page.wait_for_timeout(200)
    assert any("navigated away" in l for l in pv.logs) or any("blocked" in l for l in logs)
    if pv._page.url != f.resolve().as_uri():
        with pytest.raises(RuntimeError):
            await pv.eval("1")


async def test_a_thrown_syntax_error_runs_the_code_once(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    with pytest.raises(Exception):
        await pv.eval("window.count = (window.count || 0) + 1; throw new SyntaxError('runtime')")
    assert await pv.eval("window.count") == 1


async def test_prefixes_are_wide_enough_for_the_step_count(pv, tmp_path):
    f = tmp_path / "a.html"
    f.write_text(PAGE)
    await pv.load(f)
    out = await pv.screenshot([{"delay": 1}] * 11, save_to=tmp_path / "s.jpg")
    assert out[0].name == "01-s.jpg" and out[-1].name == "11-s.jpg"
