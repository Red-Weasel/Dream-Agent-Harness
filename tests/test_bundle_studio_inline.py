"""A0 (build it right there): the page the Studio frame gets has its local stylesheet, classic scripts, images
and fonts inlined, from its own folder tree, under the media rules -- the frame loads nothing by URL, so every
such page was a blank pane before (6 of the owner's 9 shows). A page with no local references stays
byte-identical; an .svg is never changed. DREAM-238: the page's local ES-module graph is inlined too (one
importmap of data: URLs, each local import rewritten to its key). What the frame cannot run whatever is inlined
(a local fetch()) or what could not be inlined is named in a notice at the top of the page."""
from __future__ import annotations

import base64

import pytest
from bs4 import BeautifulSoup

from dream.gui import bundle as assets

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP4z8DwHwyBNAMDAHbjD/0ie3IoAAAAAElFTkSuQmCC")
SITE = {"index.html": ('<!doctype html><html><head><title>Coffee Shop</title><link rel="stylesheet" href="style.css">'
                       '</head><body><h1 id="h">booting...</h1><img id="logo" src="logo.png" alt="logo">'
                       '<script src="app.js"></script></body></html>'),
        "style.css": "body{background:rgb(1, 2, 3)}h1{background:url(mark.png) no-repeat}@font-face{font-family:F;src:url('f.woff2')}",
        "app.js": "document.getElementById('h').textContent = 'running'; // </script> in a comment",
        "f.woff2": b"wOF2fixture"}


def site(tmp_path, **override):
    folder = tmp_path / "site"
    folder.mkdir(exist_ok=True)
    for name, body in {**SITE, **override}.items():
        if isinstance(body, bytes):
            (folder / name).write_bytes(body)
        else:
            (folder / name).write_text(body)
    (folder / "logo.png").write_bytes(PNG)
    (folder / "mark.png").write_bytes(PNG)
    return folder / "index.html"


def prepare(page):
    return assets.prepare_studio_media(page, page.read_text())


def data(payload, mime):
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def test_stylesheet_script_image_and_the_css_urls_are_inlined(tmp_path):
    page = site(tmp_path)
    out = prepare(page)
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("link") is None and soup.find("script").get("src") is None
    style = soup.find("style").string
    assert "background:rgb(1, 2, 3)" in style
    assert f"url({data(PNG, 'image/png')})" in style, "a background image in the stylesheet"
    assert f"url({data(b'wOF2fixture', 'font/woff2')})" in style, "a font the stylesheet names"
    assert soup.find("img")["src"] == data(PNG, "image/png")
    assert "textContent = 'running'" in soup.find("script").string
    assert "<\\/script>" in soup.find("script").string, "a closing tag inside the code cannot end the inline script"
    assert assets.NOTICE_ID not in out, "nothing the frame cannot run: no notice"
    assert page.read_text() == SITE["index.html"], "the file on disk is untouched"


@pytest.mark.parametrize("html", ["<!doctype html><title>t</title><style>body{background:#123}</style><h1>Hi</h1><script>let a = 1;</script>",
                                  '<script src="https://cdn.example/x.js"></script><img src="data:image/png;base64,AA==">',
                                  '<link rel="stylesheet" href="//cdn.example/x.css"><link rel="icon" href="fav.ico">',
                                  '<script>let x = `<img src="logo.png"><script src="app.js">`;</script>',
                                  '<img src="/abs.png"><img src="#frag">'])
def test_a_page_with_no_local_references_is_byte_identical(tmp_path, html):
    page = tmp_path / "page.html"
    page.write_text(html)
    (tmp_path / "logo.png").write_bytes(PNG)
    assert assets.prepare_studio_media(page, html) == html


def test_an_svg_is_never_changed(tmp_path):
    svg = tmp_path / "pic.svg"
    body = '<svg xmlns="http://www.w3.org/2000/svg"><image href="logo.png"/><script href="app.js"/></svg>'
    svg.write_text(body)
    (tmp_path / "logo.png").write_bytes(PNG)
    assert assets.prepare_studio_media(svg, body) == body


def test_the_css_urls_in_style_blocks_and_style_attributes_are_inlined(tmp_path):
    page = tmp_path / "page.html"
    (tmp_path / "bg.png").write_bytes(PNG)
    html = '<style>body{background:url("bg.png")}</style><div style="background-image:url(bg.png)">x</div>'
    page.write_text(html)
    out = assets.prepare_studio_media(page, html)
    assert out.count(data(PNG, "image/png")) == 2 and "bg.png" not in out


def test_a_deferred_script_keeps_its_time_the_end_of_the_body(tmp_path):
    page = tmp_path / "page.html"
    (tmp_path / "app.js").write_text("document.getElementById('h').textContent = 'ran';")
    html = '<html><head><script src="app.js" defer></script></head><body><h1 id="h">x</h1></body></html>'
    page.write_text(html)
    soup = BeautifulSoup(assets.prepare_studio_media(page, html), "html.parser")
    assert soup.head.find("script") is None
    last = soup.body.find_all(True)[-1]
    assert last.name == "script" and "ran" in last.string and not last.has_attr("defer") and not last.has_attr("src")


def test_only_the_pages_own_folder_tree_and_only_regular_files_under_the_caps(tmp_path, monkeypatch):
    page = site(tmp_path, **{"index.html": ('<link rel="stylesheet" href="../outside.css"><img src="missing.png">'
                                            '<img src="big.png"><img src="logo.png"><script src="notes.txt"></script>')})
    (tmp_path / "outside.css").write_text("body{}")
    (page.parent / "big.png").write_bytes(PNG * 4)
    (page.parent / "notes.txt").write_text("not a script, still inlined: a script's type is its tag's")
    monkeypatch.setattr(assets, "MAX_ASSET_BYTES", len(PNG))
    out = assets.prepare_studio_media(page, page.read_text())
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("link")["href"] == "../outside.css", "left as it was"
    srcs = [i["src"] for i in soup.find_all("img")]
    assert srcs[0] == "missing.png" and srcs[1] == "big.png" and srcs[2] == data(PNG, "image/png")
    notice = soup.find(id=assets.NOTICE_ID).string
    assert "outside.css (outside the page's folder)" in notice and "missing.png (missing)" in notice
    assert "big.png (over the preview's" in notice and "Open in browser runs it" not in notice
    assert "notes.txt" not in notice and "still inlined" in soup.find("script").string


def test_a_symlink_out_of_the_folder_is_left_alone(tmp_path):
    folder = tmp_path / "page"
    folder.mkdir()
    (tmp_path / "secret.css").write_text("body{}")
    (folder / "style.css").symlink_to(tmp_path / "secret.css")
    page = folder / "index.html"
    html = '<link rel="stylesheet" href="style.css">'
    page.write_text(html)
    out = assets.prepare_studio_media(page, html)
    assert "body{}" not in out and 'href="style.css"' in out


NEEDS = ("This page needs files the preview cannot load by URL (a file a script fetches, or a module that could "
         "not be inlined). Open in browser runs it. Found: ")
MISSING = "Part of this page is missing here: the preview loads nothing by URL, and these files could not be inlined: "


@pytest.mark.parametrize("html, said", [
    ('<script type="module" src="gone.js"></script>', NEEDS + "ES module gone.js. Not inlined: gone.js (missing)."),
    ('<script type="importmap">{"imports":{"three":"./vendor/three.js"}}</script><script type="module">import("./main.js")</script>',
     MISSING + "main.js (missing)."),
    ("<script type='module'>\nimport {x} from './x.js';\n</script>", MISSING + "x.js (missing)."),
    ("<script type='module'>import {x} from '../above.js';</script>", MISSING + "above.js (outside the page's folder)."),
    ("<script>fetch('./data.json').then(r => r.json())</script>", NEEDS + "fetch() of a local file."),
    ('<script>fetch("data/rows.csv")</script>', NEEDS + "fetch() of a local file."),
    ('<script type="module" src="loads.js"></script>', NEEDS + "fetch() of a local file in a module."),
])
def test_what_the_frame_cannot_run_gets_the_owner_visible_notice(tmp_path, html, said):
    folder = tmp_path / "page"
    folder.mkdir()
    page = folder / "page.html"
    (tmp_path / "above.js").write_text("export const x = 1;")
    (folder / "loads.js").write_text("fetch('./rows.json').then(r => r.json());")
    body = "<!doctype html><html><body><h1>booting...</h1>" + html + "</body></html>"
    page.write_text(body)
    soup = BeautifulSoup(assets.prepare_studio_media(page, body), "html.parser")
    notice = soup.body.contents[0]
    assert notice.name == "div" and notice["id"] == assets.NOTICE_ID and notice["role"] == "status"
    assert "position:fixed" in notice["style"] and "z-index:2147483647" in notice["style"]
    assert notice.string == said


def module_map(soup):
    """The page's importmap after inlining: {key: the module's source} for the local modules, and the rest."""
    import json
    imports = json.loads(soup.find("script", type="importmap").string)["imports"]
    local = {k: base64.b64decode(v.split(",", 1)[1]).decode() for k, v in imports.items() if k.startswith("@dream-local/")}
    assert all(imports[k].startswith("data:text/javascript;base64,") for k in local)
    return local, {k: v for k, v in imports.items() if k not in local}


def test_a_local_module_graph_is_inlined_through_one_importmap(tmp_path):
    """DREAM-238, the shape of Dream's own three.js starter: a bare name and a "prefix/" through the page's
    importmap, relative imports between the library's files, a minified import, a dynamic import, a cycle -- and a
    remote entry left alone. Each file is one key from every importer (a library imported twice is one instance)."""
    page = tmp_path / "scene.html"
    lib = tmp_path / "vendor" / "kit"
    (lib / "addons").mkdir(parents=True)
    (lib / "kit.module.min.js").write_text('import{C}from"./kit.core.min.js";export*from"./kit.core.min.js";export const K=C+1;')
    (lib / "kit.core.min.js").write_text("export const C = 41;")
    (lib / "addons" / "Orbit.js").write_text("import { K } from 'kit';\nimport './Pass.js';\nexport const Orbit = K;")
    (lib / "addons" / "Pass.js").write_text("import { Orbit } from './Orbit.js';   // a cycle\nexport const Pass = 1;")
    (tmp_path / "late.js").write_text("export const late = 'late';")
    html = ('<!doctype html><html><head><script type="importmap">{"imports":{"kit":"./vendor/kit/kit.module.min.js",'
            '"kit/addons/":"./vendor/kit/addons/","cdn":"https://cdn.example/x.js"}}</script></head><body>'
            '<script type="module">import * as KIT from "kit";\nimport { Orbit } from \'kit/addons/Orbit.js\';\n'
            'import x from "cdn";\nconst m = await import("./late.js");\nimport y from "unknown-bare";</script></body></html>')
    page.write_text(html)
    out = assets.prepare_studio_media(page, html)
    soup = BeautifulSoup(out, "html.parser")
    local, rest = module_map(soup)
    assert sorted(local) == ["@dream-local/late.js", "@dream-local/vendor/kit/addons/Orbit.js",
                             "@dream-local/vendor/kit/addons/Pass.js", "@dream-local/vendor/kit/kit.core.min.js",
                             "@dream-local/vendor/kit/kit.module.min.js"]
    assert rest == {"kit": "./vendor/kit/kit.module.min.js", "kit/addons/": "./vendor/kit/addons/", "cdn": "https://cdn.example/x.js"}
    entry = soup.find("script", type="module").string
    assert 'from "@dream-local/vendor/kit/kit.module.min.js"' in entry and "from '@dream-local/vendor/kit/addons/Orbit.js'" in entry
    assert 'import("@dream-local/late.js")' in entry
    assert 'from "cdn"' in entry and 'from "unknown-bare"' in entry, "a remote entry and an unknown name are left as written"
    core = "@dream-local/vendor/kit/kit.core.min.js"
    assert local["@dream-local/vendor/kit/kit.module.min.js"] == f'import{{C}}from"{core}";export*from"{core}";export const K=C+1;'
    orbit = local["@dream-local/vendor/kit/addons/Orbit.js"]
    assert "from '@dream-local/vendor/kit/kit.module.min.js'" in orbit and "import '@dream-local/vendor/kit/addons/Pass.js'" in orbit
    assert "from '@dream-local/vendor/kit/addons/Orbit.js'" in local["@dream-local/vendor/kit/addons/Pass.js"]   # the cycle closes
    assert assets.NOTICE_ID not in out and page.read_text() == html, "nothing to warn about; the file on disk is untouched"
    assert soup.find("script", type="importmap") is soup.find_all("script")[0], "the importmap stays ahead of the modules"


def test_a_module_script_with_src_and_no_importmap_gets_one(tmp_path):
    page = tmp_path / "page.html"
    (tmp_path / "post.js").write_text("import { n } from './lib/n.js';\ndocument.title = n;")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "n.js").write_text("export const n = 'ran';")
    html = '<h1>x</h1><script type="module" src="post.js"></script>'
    page.write_text(html)
    soup = BeautifulSoup(assets.prepare_studio_media(page, html), "html.parser")
    local, rest = module_map(soup)
    assert sorted(local) == ["@dream-local/lib/n.js", "@dream-local/post.js"] and rest == {}
    tags = soup.find_all("script")
    assert [t.get("type") for t in tags] == ["importmap", "module"] and not tags[1].has_attr("src")
    assert tags[1].string == 'import "@dream-local/post.js";'
    assert "from '@dream-local/lib/n.js'" in local["@dream-local/post.js"]


def test_modules_obey_the_folder_and_the_caps(tmp_path, monkeypatch):
    folder = tmp_path / "page"
    folder.mkdir()
    (tmp_path / "secret.js").write_text("export const s = 1;")
    (folder / "link.js").symlink_to(tmp_path / "secret.js")
    (folder / "big.js").write_text("export const b = '" + "x" * 64 + "';")
    (folder / "ok.js").write_text("export const o = 1;")
    monkeypatch.setattr(assets, "MAX_ASSET_BYTES", 40)
    html = "<script type='module'>import './ok.js'; import './big.js'; import './link.js';</script>"
    page = folder / "page.html"
    page.write_text(html)
    out = assets.prepare_studio_media(page, html)
    soup = BeautifulSoup(out, "html.parser")
    local, _ = module_map(soup)
    assert list(local) == ["@dream-local/ok.js"] and "export const s" not in out
    entry = soup.find("script", type="module").string
    assert "import './big.js'" in entry and "import './link.js'" in entry, "what could not be inlined keeps its specifier"
    notice = soup.find(id=assets.NOTICE_ID).string
    assert "big.js (over the preview's" in notice and "link.js (outside the page's folder)" in notice


@pytest.mark.parametrize("html", ['<script type="module">const a = 1; document.title = "no import";</script>',
                                  "<script>fetch('https://api.example/x')</script>",
                                  "<script>fetch(url)</script>",
                                  "<script>const s = 'import x from y';</script>"])
def test_a_module_without_imports_or_a_remote_or_variable_fetch_is_not_flagged(tmp_path, html):
    page = tmp_path / "page.html"
    page.write_text(html)
    assert assets.prepare_studio_media(page, html) == html


def test_the_notice_goes_into_a_page_without_a_body_tag_and_the_media_rules_still_hold(tmp_path):
    page = tmp_path / "page.html"
    (tmp_path / "clip.mp4").write_bytes(b"fixture")
    html = '<script type="module" src="m.js"></script><video src="clip.mp4"></video>'
    page.write_text(html)
    out = assets.prepare_studio_media(page, html)
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find(id=assets.NOTICE_ID) is not None and "ES module m.js" in soup.find(id=assets.NOTICE_ID).string
    assert soup.find("video")["src"].startswith("data:video/mp4;base64,")
    with pytest.raises(ValueError, match="Studio media preparation"):
        assets.prepare_studio_media(page, '<video src="missing.mp4"></video>')
