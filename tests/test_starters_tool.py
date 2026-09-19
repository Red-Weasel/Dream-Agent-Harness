"""copy_starter_component: exact kinds, the vendor folder for JSX, the echo."""

from __future__ import annotations

import pytest

from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.starters import KINDS, STARTERS_DIR, VENDOR, copy_starter_component


@pytest.fixture
def ws(tmp_path):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    yield tmp_path
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


def test_every_kind_ships():
    for k in KINDS:
        if k == "three":   # a library folder, not a component file
            assert (STARTERS_DIR / "vendor" / "three" / "three.module.min.js").stat().st_size > 1000
            continue
        assert (STARTERS_DIR / k).is_file(), k
    for v in VENDOR:
        assert (STARTERS_DIR / "vendor" / v).stat().st_size > 1000, v


@pytest.mark.asyncio
async def test_a_js_starter_copies_alone_and_echoes_its_content_and_tag(ws):
    res = await copy_starter_component.handler({"kind": "deck_stage.js"})
    assert not res.get("is_error")
    assert (ws / "deck_stage.js").read_text() == (STARTERS_DIR / "deck_stage.js").read_text()
    assert not (ws / "vendor").exists()
    out = _text(res)
    assert '<script src="deck_stage.js"></script>' in out
    assert "customElements.define('deck-stage'" in out


@pytest.mark.asyncio
async def test_a_jsx_starter_brings_vendor_and_the_babel_tags_into_a_subdirectory(ws):
    res = await copy_starter_component.handler({"kind": "ios_frame.jsx", "directory": "frames/"})
    assert not res.get("is_error")
    assert (ws / "frames" / "ios_frame.jsx").is_file()
    for v in VENDOR:
        assert (ws / "frames" / "vendor" / v).is_file(), v
    assert (ws / "frames" / "vendor" / "LICENSE-react.txt").read_text().startswith("MIT License")
    out = _text(res)
    assert '<script src="frames/vendor/babel.min.js"></script>' in out
    assert '<script type="text/babel" src="frames/ios_frame.jsx"></script>' in out
    assert "Object.assign(window, { IosFrame" in out


@pytest.mark.asyncio
async def test_the_kind_must_include_the_extension_and_stay_in_the_workspace(ws):
    res = await copy_starter_component.handler({"kind": "ios_frame"})
    assert res.get("is_error") and "Did you mean 'ios_frame.jsx'" in _text(res)
    res = await copy_starter_component.handler({"kind": "nope.jsx"})
    assert res.get("is_error") and "Unknown starter" in _text(res)
    res = await copy_starter_component.handler({"kind": "deck_stage.js", "directory": "../out"})
    assert res.get("is_error") and "inside the workspace" in _text(res)
    res = await copy_starter_component.handler({"kind": "deck_stage.js", "directory": "/tmp/elsewhere"})
    assert res.get("is_error") and "relative to the workspace" in _text(res)
    assert not (ws / "tmp").exists()


@pytest.mark.asyncio
async def test_three_starter_brings_a_working_threejs_with_addons(tmp_path):
    """Dream fix #3: the frames have no network, so three.js must ship with Dream.
    The copied module graph (core, bloom composer, sky) loads clean in the preview."""
    from dream.tools.context import ToolContext, set_context
    from dream.tools.starters import copy_starter_component
    from dream.gui.preview import Preview
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=lambda e: None))
    out = await copy_starter_component.handler({"kind": "three"})
    text = out["content"][0]["text"]
    assert not out.get("is_error") and '"three": "./vendor/three/three.module.min.js"' in text
    assert (tmp_path / "vendor/three/addons/postprocessing/UnrealBloomPass.js").is_file()
    tags = text[text.index("<script type=\"importmap\">"):]
    scene = tags.replace("// your scene", """
const r = new THREE.WebGLRenderer(); r.setSize(320, 200); document.body.appendChild(r.domElement);
const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(50, 1.6, 0.1, 100);
scene.add(new Sky()); const c = new EffectComposer(r); c.addPass(new RenderPass(scene, cam));
c.addPass(new UnrealBloomPass(new THREE.Vector2(320, 200), 1, 0.4, 0.8)); c.addPass(new OutputPass());
new OrbitControls(cam, r.domElement); c.render(); console.log('scene ok');""")
    (tmp_path / "scene.html").write_text("<!doctype html><body>" + scene + "</body>")
    pv = Preview()
    try:
        logs = await pv.load(tmp_path / "scene.html")
    finally:
        await pv.aclose()
    assert any("scene ok" in line for line in logs), logs
    assert not [line for line in logs if line.startswith(("error:", "console.error:"))], logs
