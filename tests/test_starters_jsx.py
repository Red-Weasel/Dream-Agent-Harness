"""The six JSX starters, rendered in the hidden frame from disk: React, ReactDOM,
and Babel are vendored beside them, and Babel loads each .jsx by XHR — which is
why the hidden frame launches with file access from files."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dream.gui.preview import Preview

STARTERS = Path(__file__).parent.parent / "dream" / "gui" / "starters"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="vendor/react.production.min.js"></script>
<script src="vendor/react-dom.production.min.js"></script>
<script src="vendor/babel.min.js"></script>
</head><body><div id="root"></div>
<script type="text/babel" src="{starter}"></script>
<script type="text/babel">
ReactDOM.createRoot(document.getElementById('root')).render({render});
</script></body></html>"""


def _errors(logs):
    return [l for l in logs if l.startswith(("error:", "console.error:"))]


@pytest.fixture
async def frame(tmp_path):
    shutil.copytree(STARTERS, tmp_path, dirs_exist_ok=True)
    pv = Preview()

    async def render(starter: str, jsx: str):
        f = tmp_path / f"{starter}.html"
        f.write_text(PAGE.format(starter=starter, render=jsx))
        logs = await pv.load(f)
        await pv._page.wait_for_timeout(300)
        assert _errors(pv.logs) == [], pv.logs
        return pv

    yield render
    await pv.aclose()


@pytest.mark.asyncio
async def test_the_vendored_libraries_and_babel_load_from_disk(frame):
    pv = await frame("design_canvas.jsx", "<DesignCanvas title='t'><Option label='A'>x</Option></DesignCanvas>")
    assert await pv.eval("typeof React.version") == "string"
    assert await pv.eval("!!window.Babel && !!window.DesignCanvas")


@pytest.mark.asyncio
async def test_design_canvas_lays_out_labeled_options(frame):
    pv = await frame("design_canvas.jsx", """
      <DesignCanvas title="Button" subtitle="three directions" cols={3}>
        <Option label="A · Solid" note="matches the kit"><button>Go</button></Option>
        <Option label="B · Ghost"><button>Go</button></Option>
        <Option label="C · Pill" height={120}><button>Go</button></Option>
      </DesignCanvas>""")
    assert await pv.eval("document.querySelectorAll('[data-design-canvas] figure').length") == 3
    assert await pv.eval("[...document.querySelectorAll('figure')].map(f => f.dataset.screenLabel)") == ["A · Solid", "B · Ghost", "C · Pill"]
    assert await pv.eval("getComputedStyle(document.querySelector('[data-design-canvas] > div:last-child')).gridTemplateColumns.split(' ').length") == 3
    assert "matches the kit" in await pv.eval("document.body.textContent")


@pytest.mark.asyncio
async def test_ios_frame_draws_status_bar_content_and_keyboard(frame):
    pv = await frame("ios_frame.jsx", """
      <IosFrame time="9:41" keyboard label="01 Home"><h1 id="c">Screen</h1></IosFrame>""")
    assert await pv.eval("document.querySelector('[data-device=ios]').dataset.screenLabel") == "01 Home"
    assert "9:41" in await pv.eval("document.querySelector('[data-device=ios]').textContent")
    assert await pv.eval("document.querySelector('[data-device=ios] [data-screen] #c').textContent") == "Screen"
    assert await pv.eval("!!document.querySelector('[data-ios-keyboard]')")
    w = await pv.eval("document.querySelector('[data-device=ios] > div').getBoundingClientRect().width")
    assert abs(w - 393) < 1


@pytest.mark.asyncio
async def test_android_frame_offers_gesture_or_button_navigation(frame):
    pv = await frame("android_frame.jsx", """
      <div>
        <AndroidFrame time="12:30" label="a"><p>one</p></AndroidFrame>
        <AndroidFrame nav="buttons" dark keyboard label="b"><p>two</p></AndroidFrame>
      </div>""")
    assert await pv.eval("document.querySelectorAll('[data-device=android]').length") == 2
    assert await pv.eval("!!document.querySelector('[data-nav=gesture]') && !!document.querySelector('[data-nav=buttons]')")
    assert await pv.eval("!!document.querySelector('[data-android-keyboard]')")
    assert "12:30" in await pv.eval("document.querySelector('[data-device=android]').textContent")


@pytest.mark.asyncio
async def test_macos_and_browser_windows_show_chrome_and_content(frame):
    pv = await frame("macos_window.jsx", """<MacosWindow title="Settings" width={640} height={400}><p id="m">inside</p></MacosWindow>""")
    assert await pv.eval("document.querySelector('[data-window=macos] [data-title]').textContent") == "Settings"
    assert await pv.eval("document.querySelector('[data-window=macos] [data-screen] #m').textContent") == "inside"
    assert abs(await pv.eval("document.querySelector('[data-window=macos]').getBoundingClientRect().width") - 640) < 1
    pv = await frame("browser_window.jsx", """<BrowserWindow url="https://acme.app/dashboard?x=1" tabs={['Dashboard','Billing']} active={1} width={900} height={500}><p id="w">web</p></BrowserWindow>""")
    addr = await pv.eval("document.querySelector('[data-window=browser] [data-address]').textContent")
    assert "acme.app" in addr and "/dashboard?x=1" in addr
    assert await pv.eval("document.querySelector('[data-tab=active]').textContent") == "Billing"
    assert await pv.eval("document.querySelector('[data-window=browser] [data-screen] #w').textContent") == "web"


@pytest.mark.asyncio
async def test_animations_stage_sprites_and_seek(frame):
    pv = await frame("animations.jsx", """
      <Stage duration={6} autoplay={false}>
        <Sprite start={0} end={3}><FadeIn duration={0.5}><h1 id="a">A</h1></FadeIn></Sprite>
        <Sprite start={2.5}><SlideIn from="bottom" duration={1}><p id="b">B</p></SlideIn></Sprite>
      </Stage>""")
    assert await pv.eval("window.stageDuration") == 6
    assert await pv.eval("!!document.querySelector('[data-stage]') && !!document.querySelector('[data-stage-controls]')")
    assert await pv.eval("interpolate(5, [0, 10], [0, 100])") == 50
    assert await pv.eval("interpolate(20, [0, 10], [0, 100])") == 100
    assert await pv.eval("Object.keys(Easing).sort()") == sorted(["linear", "easeIn", "easeOut", "easeInOut", "easeOutBack", "easeOutElastic"])
    await pv.eval("stageSeek(1)"); await pv._page.wait_for_timeout(60)
    assert await pv.eval("!!document.getElementById('a') && !document.getElementById('b')")
    assert await pv.eval("getComputedStyle(document.getElementById('a').parentElement).opacity") == "1"
    await pv.eval("stageSeek(2.75)"); await pv._page.wait_for_timeout(60)
    assert await pv.eval("!!document.getElementById('a') && !!document.getElementById('b')")
    # B is 0.25 s into a 1 s slide from the bottom: still translated, not yet at rest
    assert "matrix" in await pv.eval("getComputedStyle(document.getElementById('b').parentElement).transform")
    await pv.eval("stageSeek(4)"); await pv._page.wait_for_timeout(60)
    assert await pv.eval("!document.getElementById('a') && !!document.getElementById('b')")
    # at rest: a zero translate computes to the identity matrix
    assert await pv.eval("getComputedStyle(document.getElementById('b').parentElement).transform") in ("none", "matrix(1, 0, 0, 1, 0, 0)")
    # position is remembered
    assert abs(float(await pv.eval("localStorage.getItem('stage:' + location.pathname)")) - 4) < 1e-6
    # the canvas is scaled to fit the viewport
    assert "matrix" in await pv.eval("getComputedStyle(document.querySelector('[data-stage]')).transform")
