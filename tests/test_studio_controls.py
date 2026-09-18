"""Shipped Studio controls against a real StudioServer and explicit API fixtures.

No Engine, desktop capture, provider requests or GPU/model loads are started.
The fixtures exercise HTTP contracts without modifying a user's runtime settings.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
from urllib.parse import unquote

import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


# Loading the companion reads the mode once; it must not change execution policy.
READ_ONLY_STARTUP_CALLS = [{"action": "permission_mode_status"}]


RUNTIME = {
    "provider": "openai", "model": "fixture-model", "workspace": "/workspace/field-notes",
    "profile": {"name": "balanced", "output_tokens": 8192, "max_parallel": 3},
    "settings": {"profile": "auto", "overrides": {}},
    "context_owner": "Dream", "context": {
        "window": 32768, "input_tokens": 6200, "output": 8192,
        "remaining": 17864, "method": "estimated UTF-8 bytes / 3.5",
    },
    "execution": {"available": True, "red_team": False, "targets": []},
    "run": {"tools": 8, "failures": 0, "prompt_tokens": 6200, "output_tokens": 900, "cached_tokens": 512},
}


def extension(name, kind="skill", enabled=True, **kwargs):
    return {"id": f"{kind}:{name}", "name": name, "kind": kind, "enabled": enabled,
            "default_enabled": enabled, "source": "Dream", "path": f"/workspace/skills/{name}",
            "provenance": "dream-skill", "portable": kind == "skill",
            "description": "Close a capability gap with bounded experiments and reviewed evidence.",
            "usage": {"state": "not_observed", "counts": {}}, **kwargs}


class API:
    def __init__(self):
        self.runtime = copy.deepcopy(RUNTIME)
        self.rows = [extension("illuminati-handshake"), extension("workflow-notes"),
                     extension("design-kit", "plugin", False),
                     extension("palette", parent="plugin:design-kit", enabled=False),
                     extension("local-index", "mcp"), extension("audit", "hook", False)]
        self.learning = {"active": None, "demonstrations": []}
        self.calls = []
        self.headers = []
        self.fail = {}
        self.pause = {}
        self.module_sources = {}
        self.draft = "---\nname: learned-export\ndescription: Export a design\n---\n\n# Export\n1. Check the current app.\n<script>window.pwned = true</script>\n"

    async def route(self, route):
        request = route.request
        path = unquote(request.url.split("/api/", 1)[1].split("?", 1)[0])
        self.headers.append(request.headers)
        if path in self.fail:
            status, error = self.fail[path]
            await route.fulfill(status=status, json={"error": error})
            return
        if path == "runtime":
            result = self.runtime
        elif path == "extensions":
            counts = {kind: {"total": sum(r["kind"] == kind for r in self.rows),
                             "enabled": sum(r["kind"] == kind and r["enabled"] for r in self.rows)}
                      for kind in ("skill", "plugin", "mcp", "hook", "tool")}
            result = {"extensions": self.rows, "counts": counts, "warnings": [],
                      "usage_note": "Observed runtime events only; absent history means not observed."}
        elif path == "learning":
            result = self.learning
        elif path.startswith("extensions/") and path.endswith("/source"):
            result = self.module_sources[path[len("extensions/"):-len("/source")]]
        elif path.startswith("learning/") and path.endswith("/draft"):
            result = {"id": path.split("/")[1], "body": self.draft, "uncertainty": "Confirm inferred clicks."}
        elif path == "control":
            payload = request.post_data_json
            self.calls.append(payload)
            action = payload["action"]
            if action in self.fail:
                status, error = self.fail[action]
                await route.fulfill(status=status, json={"error": error})
                return
            result = {}
            if action == "profile":
                self.runtime["settings"] = {k: payload[k] for k in ("profile", "overrides")}
                result = {"applies": "new sessions", "profile": payload["profile"]}
            elif action == "extension":
                row = next(row for row in self.rows if row["id"] == payload["id"])
                row.update(enabled=payload["enabled"], override=payload["enabled"])
                result = row
            elif action == "module_trust":
                source = self.module_sources[payload["id"]]
                if payload["sha256"] != source["sha256"]:
                    await route.fulfill(status=400, json={"error": "Module changed since review; inspect the new source before trusting it"})
                    return
                row = next(row for row in self.rows if row["id"] == payload["id"])
                row.update(trust_state="trusted", sha256=source["sha256"], enabled=row["configured_enabled"])
                result = {k: source[k] for k in ("id", "path", "sha256", "size")}
                result["trusted"] = True
            elif action == "learn_start":
                result = {"id": "demo-fixture", "name": payload["name"], "status": "recording",
                          "region": payload["region"], "maximum_seconds": payload["seconds"], "frames": []}
                self.learning["active"] = result
            elif action == "learn_stop":
                info = self.learning["active"]
                self.learning["active"] = None
                result = {**info, "status": "recorded"} if info else {"status": "idle"}
                if info:
                    self.learning["demonstrations"].append(result)
            elif action == "learn_import":
                result = {"id": "demo-imported", "name": payload["name"] or "Imported video", "status": "recorded", "frames": []}
                self.learning["demonstrations"].append(result)
            elif action == "learn_extract":
                row = next(r for r in self.learning["demonstrations"] if r["id"] == payload["id"])
                row.update(status="ready", frames=[{"file": "frame-01.jpg"}])
                result = row
            elif action == "learn_analyze":
                result = {"queued": True, "provider": "openai"}
            elif action == "learn_install":
                result = {"path": "/workspace/skills/learned-export/SKILL.md", "enabled": False}
            elif action == "red_team":
                self.runtime["execution"] = {"available": True, "red_team": payload["enabled"],
                                              "targets": [payload["target"]] if payload["enabled"] else [],
                                              "remaining_seconds": payload.get("minutes", 0) * 60}
                result = self.runtime["execution"]
            result = {"ok": True, "result": result}
            if action in self.pause:
                await self.pause[action].wait()
        else:
            await route.continue_()
            return
        await route.fulfill(json=result)


@pytest.fixture
async def controls(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    server = StudioServer(EventBus(), session={"workspace": str(tmp_path), "provider": "test", "model": "fixture"})
    url = await server.start()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 800, "height": 900}, reduced_motion="reduce")
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            api = API()
            for pattern in ("**/api/runtime", "**/api/extensions", "**/api/extensions/*/source", "**/api/learning", "**/api/learning/*/draft", "**/api/control"):
                await page.route(pattern, api.route)
            yield server, page, api, url + "&companion=1", errors
            await browser.close()
    finally:
        await server.stop()


async def open_controls(page, url, tab="Runtime"):
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.locator("#dream-controls-open").click()
    await page.get_by_role("tab", name=tab, exact=True).click()


async def no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert await page.locator(".dc-scroll").evaluate("e => e.scrollWidth <= e.clientWidth")
    box = await page.locator("#dream-controls").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]


async def test_profile_numbers_and_next_session_do_not_change_canvas(controls, tmp_path):
    server, page, api, url, errors = controls
    server.retain_show(Event("studio", {"op": "show", "path": "work.html", "content": '<h1>Working canvas</h1><input id="draft" value="original">'}))
    await page.goto(url)
    await expect(page.frame_locator("#artbody iframe").locator("h1")).to_have_text("Working canvas")
    await page.frame_locator("#artbody iframe").locator("#draft").fill("Keep this edit")
    await page.locator("#dream-controls-open").click()
    await expect(page.locator("#dc-session")).to_contain_text("fixture-model")
    await expect(page.locator("#dc-profile")).to_have_value("auto")
    await page.screenshot(path=str(tmp_path / "studio-runtime-800.png"))
    await page.get_by_text("Override limits", exact=True).click()
    await page.locator("#dc-profile").select_option("lean")
    await page.locator("#dc-context_limit").fill("16384")
    await page.locator("#dc-max_parallel").fill("1")
    await page.locator("#dc-idle_timeout_s").fill("90.5")
    await page.get_by_role("button", name="Save for next session").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("This session remains balanced")
    assert api.calls == READ_ONLY_STARTUP_CALLS + [{"action": "profile", "profile": "lean", "overrides": {"context_limit": 16384, "max_parallel": 1, "idle_timeout_s": 90.5}}]
    assert all(h.get("x-dream-token") == server.token for h in api.headers)
    await no_overflow(page)
    await page.set_viewport_size({"width": 480, "height": 800})
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-profile-480.png"))
    await page.keyboard.press("Escape")
    await expect(page.locator("#dream-controls")).not_to_be_visible()
    await expect(page.locator("#dream-controls-open")).to_be_focused()
    await expect(page.frame_locator("#artbody iframe").locator("#draft")).to_have_value("Keep this edit")
    assert errors == []


async def test_extension_toggle_failure_retry_catalog_provenance_and_hooks(controls, tmp_path):
    _, page, api, url, errors = controls
    await open_controls(page, url, "Extensions")
    await expect(page.get_by_role("switch", name="illuminati-handshake enabled")).to_have_attribute("aria-checked", "true")
    await expect(page.get_by_role("switch", name="audit enabled")).to_be_disabled()
    await expect(page.get_by_role("switch", name="palette enabled")).to_be_disabled()
    api.fail["extension"] = (400, "Cannot update malformed Dream settings")
    toggle = page.get_by_role("switch", name="illuminati-handshake enabled")
    await toggle.click()
    await expect(page.locator("#dc-feedback")).to_contain_text("malformed Dream settings")
    await expect(toggle).to_have_attribute("aria-checked", "true")
    del api.fail["extension"]
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-checked", "false")
    await page.locator("#dc-ext-search").fill("illuminati")
    await expect(page.locator(".dc-extension")).to_have_count(1)
    await page.locator(".dc-extension summary").click()
    await expect(page.locator(".dc-extension")).to_contain_text("No observed events")
    await expect(page.locator(".dc-extension .dc-meta")).to_contain_text("/workspace/skills/illuminati-handshake")
    await page.set_viewport_size({"width": 480, "height": 800})
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-extensions-480.png"))
    await page.locator("#dc-ext-search").fill("nothing-matches")
    await expect(page.get_by_text("No matching capabilities")).to_be_visible()
    assert api.calls[-1] == {"action": "extension", "id": "skill:illuminati-handshake", "enabled": False}
    assert errors == []


async def test_explicit_capture_stop_extract_queue_review_install(controls, tmp_path):
    _, page, api, url, errors = controls
    await open_controls(page, url, "Learn")
    await expect(page.get_by_text("No demonstrations yet")).to_be_visible()
    assert api.calls == READ_ONLY_STARTUP_CALLS
    for key, value in {"record-name": "Export a design", "x": "10", "y": "20", "width": "800", "height": "600", "seconds": "90"}.items():
        await page.locator("#dc-" + key).fill(value)
    await page.get_by_role("button", name="Start recording", exact=True).click()
    await expect(page.locator("#dream-recording")).to_be_visible()
    assert api.calls[-1] == {"action": "learn_start", "name": "Export a design", "region": [10, 20, 800, 600], "seconds": 90}
    await page.keyboard.press("Escape")
    await expect(page.locator("#dream-recording")).to_have_text("Recording")
    await page.locator("#dream-recording").click()
    await page.get_by_role("button", name="Stop recording", exact=True).click()
    await expect(page.locator("#dream-recording")).to_be_hidden()
    await page.get_by_role("button", name="Extract frames", exact=True).click()
    await expect(page.locator(".dc-demo")).to_contain_text("1 sampled frames")
    assert not any(c["action"] == "learn_analyze" for c in api.calls)
    await page.get_by_role("button", name="Analyze with model").click()
    await expect(page.locator(".dc-demo")).to_contain_text("Analysis queued")
    api.learning["demonstrations"][0]["status"] = "draft"
    await page.get_by_role("button", name="Refresh controls").click()
    await page.get_by_role("button", name="Review skill draft").click()
    await expect(page.get_by_label("Skill draft text")).to_contain_text("window.pwned")
    assert await page.evaluate("window.pwned") is None
    await expect(page.get_by_role("button", name="Install disabled skill")).to_be_disabled()
    await page.get_by_label("I reviewed these instructions").check()
    await page.set_viewport_size({"width": 480, "height": 800})
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-learn-review-480.png"))
    await page.get_by_role("button", name="Install disabled skill").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("Skill installed disabled")
    assert api.calls[-1] == {"action": "learn_install", "id": "demo-fixture"}
    assert errors == []


async def test_unavailable_api_retry_and_unreviewable_draft_cannot_install(controls):
    _, page, api, url, errors = controls
    api.fail["runtime"] = (503, "Engine is starting")
    await open_controls(page, url)
    await expect(page.locator("#dc-runtime-state")).to_contain_text("Engine is starting")
    del api.fail["runtime"]
    api.runtime["context"] = None
    await page.locator("#dc-runtime-state").get_by_role("button", name="Retry").click()
    await expect(page.locator("#dc-session")).to_contain_text("Not reported")
    api.learning["demonstrations"] = [{"id": "demo-missing", "name": "Draft", "status": "draft", "frames": []}]
    api.fail["learning/demo-missing/draft"] = (404, "Not implemented")
    await page.get_by_role("tab", name="Learn", exact=True).click()
    await page.get_by_role("button", name="Review skill draft").click()
    await expect(page.locator("#dc-draft")).to_contain_text("Installation requires a visible draft")
    await expect(page.get_by_role("button", name="Install disabled skill")).to_have_count(0)
    assert api.calls == READ_ONLY_STARTUP_CALLS
    assert errors == []


async def test_wayland_failure_import_and_recording_connection_loss(controls, tmp_path):
    _, page, api, url, errors = controls
    await open_controls(page, url, "Learn")
    api.fail["learn_start"] = (400, "Direct recording requires an X11 session. On Wayland, import its video.")
    for key, value in {"record-name": "Workflow", "x": "0", "y": "0", "width": "640", "height": "480"}.items():
        await page.locator("#dc-" + key).fill(value)
    await page.get_by_role("button", name="Start recording", exact=True).click()
    await expect(page.locator("#dc-feedback")).to_contain_text("X11")
    await expect(page.locator("#dream-recording")).to_be_hidden()
    await page.get_by_text("Import a recording instead", exact=True).click()
    await page.locator("#dc-import-path").fill("/tmp/workflow.webm")
    await page.locator("#dc-import-name").fill("My workflow")
    await page.get_by_role("button", name="Import video", exact=True).click()
    await expect(page.locator(".dc-demo h4")).to_have_text("My workflow")
    assert api.calls[-1] == {"action": "learn_import", "path": "/tmp/workflow.webm", "name": "My workflow"}
    api.learning["active"] = {"id": "recording-elsewhere", "name": "Terminal capture", "status": "recording", "maximum_seconds": 60}
    await page.get_by_role("button", name="Refresh controls").click()
    await expect(page.locator("#dream-recording")).to_have_text("Recording")
    api.fail["learning"] = (503, "Recorder status temporarily unavailable")
    await page.get_by_role("button", name="Refresh controls").click()
    await expect(page.locator("#dream-recording")).to_have_text("Check recording")
    await expect(page.get_by_role("button", name="Stop recording", exact=True)).to_be_enabled()
    await expect(page.get_by_role("button", name="Start recording", exact=True)).to_be_disabled()
    await page.set_viewport_size({"width": 480, "height": 800})
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-recording-error-480.png"))
    assert errors == []


async def test_scoped_red_team_expiry_and_keyboard_external_mode(controls, tmp_path):
    _, page, api, url, errors = controls
    await open_controls(page, url)
    await page.get_by_text("Local red-team exercise", exact=True).click()
    await page.locator("#dc-target").fill("labs/owned-fixture")
    await page.locator("#dc-minutes").fill("5")
    await page.get_by_role("button", name="Enable scoped exercise").click()
    await expect(page.locator("#dc-scope-status")).to_contain_text("Expires in 5 min")
    assert api.calls[-1] == {"action": "red_team", "enabled": True, "target": "labs/owned-fixture", "minutes": 5}
    await page.get_by_role("button", name="End exercise now").click()
    await expect(page.locator("#dc-scope-status")).to_contain_text("No red-team exercise")
    await page.get_by_role("tab", name="Runtime", exact=True).focus()
    await page.keyboard.press("ArrowRight")
    await expect(page.get_by_role("tab", name="Extensions", exact=True)).to_be_focused()
    await page.keyboard.press("End")
    await expect(page.get_by_role("tab", name="Learn", exact=True)).to_be_focused()
    await page.keyboard.press("Escape")
    await page.set_viewport_size({"width": 1280, "height": 900})
    await page.goto(url.replace("&companion=1", ""))
    await expect(page.locator("#input")).to_be_visible()
    await page.locator("#dream-controls-open").click()
    await expect(page.get_by_role("dialog", name="Studio controls")).to_be_visible()
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-controls-external-1280.png"))
    await page.keyboard.press("Escape")
    await expect(page.locator("#input")).to_be_visible()
    assert errors == []


async def test_stale_learning_poll_cannot_undo_confirmed_recording(controls):
    _, page, api, url, errors = controls
    await open_controls(page, url, "Learn")
    await expect(page.get_by_text("No demonstrations yet")).to_be_visible()
    started, release = asyncio.Event(), asyncio.Event()
    reads = 0

    async def stale_once(route):
        nonlocal reads
        reads += 1
        if reads == 1:
            old = copy.deepcopy(api.learning)
            started.set()
            await release.wait()
            await route.fulfill(json=old)
        else:
            await api.route(route)

    await page.route("**/api/learning", stale_once)
    for key, value in {"record-name": "Poll race", "x": "0", "y": "0", "width": "640", "height": "480"}.items():
        await page.locator("#dc-" + key).fill(value)
    await page.get_by_role("button", name="Refresh controls").click()
    await asyncio.wait_for(started.wait(), 3)
    await page.get_by_role("button", name="Start recording", exact=True).click()
    await expect(page.locator("#dc-pending")).to_be_visible()
    await expect(page.locator("#dc-active")).to_be_visible()
    await expect(page.get_by_role("button", name="Stop recording", exact=True)).to_be_enabled()
    release.set()
    await expect(page.locator("#dc-feedback")).to_contain_text("Recording started")
    await expect(page.locator("#dc-active")).to_be_visible()
    await expect(page.locator("#dream-recording")).to_have_text("Recording")
    assert reads >= 2, "A fresh status read follows the mutation, even with an earlier poll in flight"
    assert errors == []


async def test_stop_remains_visible_and_usable_during_slow_extraction(controls):
    _, page, api, url, errors = controls
    api.learning = {"active": {"id": "demo-live", "name": "Live recording", "status": "recording", "maximum_seconds": 120},
                    "demonstrations": [{"id": "demo-older", "name": "Earlier video", "status": "recorded", "frames": []}]}
    release = asyncio.Event()
    api.pause["learn_extract"] = release
    try:
        await open_controls(page, url, "Learn")
        await page.get_by_role("button", name="Extract frames", exact=True).click()
        await expect(page.locator("#dc-pending")).to_contain_text("Extracting frames")
        await page.get_by_role("tab", name="Extensions", exact=True).click()
        stop = page.get_by_role("button", name="Stop recording", exact=True)
        await expect(stop).to_be_visible()
        await expect(stop).to_be_enabled()
        await stop.click()
        await expect(page.locator("#dream-recording")).to_be_hidden()
        assert api.calls[-1] == {"action": "learn_stop"}
    finally:
        release.set()
    assert errors == []


@pytest.mark.parametrize("vision", ["auto", "true", "false"])
async def test_vision_override_has_json_types_and_preserves_other_limits(controls, vision):
    _, page, api, url, errors = controls
    api.runtime["settings"]["overrides"] = {"max_run_tools": 75, "vision": True}
    api.runtime["settings"]["models"] = {"openai:fixture-model": {"vision": False}}
    await open_controls(page, url)
    await expect(page.locator("#dc-vision")).to_have_value("true")
    await page.locator("#dc-vision").select_option(vision)
    await page.get_by_role("button", name="Save for next session").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("Saved auto for new sessions")
    expected = {"max_run_tools": 75}
    if vision != "auto":
        expected["vision"] = vision == "true"
    assert api.calls[-1] == {"action": "profile", "profile": "auto", "overrides": expected}
    assert errors == []


async def test_hook_enable_requires_metadata_review_and_explicit_trust(controls, tmp_path):
    _, page, api, url, errors = controls
    row = next(row for row in api.rows if row["id"] == "hook:audit")
    row.update(command=["/usr/bin/python3", "/workspace/hooks/audit.py", "<literal>"], cwd="/workspace",
               events=["before_tool"], mode="gate", timeout_s=2)
    await open_controls(page, url, "Extensions")
    await page.get_by_label("Extension type").select_option("hook")
    toggle = page.get_by_role("switch", name="audit enabled")
    await expect(toggle).to_be_disabled()
    await expect(page.locator(".dc-hook-review")).to_contain_text('/usr/bin/python3')
    await expect(page.locator(".dc-hook-review")).to_contain_text('before-tool failures deny execution')
    await expect(page.locator(".dc-hook-review")).to_contain_text('<literal>')
    assert api.calls == READ_ONLY_STARTUP_CALLS
    await page.get_by_label("I trust audit to run this configured command").check()
    await expect(toggle).to_be_enabled()
    await page.set_viewport_size({"width": 480, "height": 800})
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-hook-review-480.png"))
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-checked", "true")
    assert api.calls[-1] == {"action": "extension", "id": "hook:audit", "enabled": True, "trusted": True}
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-checked", "false")
    assert api.calls[-1] == {"action": "extension", "id": "hook:audit", "enabled": False}
    await expect(toggle).to_be_disabled()
    await expect(page.get_by_label("I trust audit to run this configured command")).not_to_be_checked()
    assert errors == []


async def test_module_enable_cannot_substitute_for_source_review(controls):
    _, page, api, url, errors = controls
    api.rows.append(extension("custom/generated", "tool", False, configured_enabled=True,
                              provenance="python-tool-module", trust_state="changed", sha256="a" * 64))
    await open_controls(page, url, "Extensions")
    await page.get_by_label("Extension type").select_option("tool")
    await expect(page.get_by_role("switch", name="custom/generated enabled")).to_be_disabled()
    await expect(page.locator(".dc-extension")).to_contain_text("enabling alone does not trust code")
    await page.locator(".dc-extension summary").click()
    await expect(page.locator(".dc-extension")).to_contain_text("a" * 64)
    assert api.calls == READ_ONLY_STARTUP_CALLS and errors == []


def module_fixture(api, configured=False):
    row = extension("custom/generated", "tool", False, configured_enabled=configured,
                    provenance="python-tool-module", trust_state="required")
    api.rows = [row]
    raw = b'# Reviewed entry file\n# <script>window.pwned = true</script>\nTOOLS = []\n'
    api.module_sources[row["id"]] = {
        "id": row["id"], "path": "/workspace/custom/generated.py", "source": raw.decode(),
        "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
    }
    return row, api.module_sources[row["id"]]


@pytest.mark.parametrize("configured", [False, True])
async def test_source_review_explicit_hash_preserves_enablement(controls, tmp_path, configured):
    server, page, api, url, errors = controls
    row, source = module_fixture(api, configured)
    await open_controls(page, url, "Extensions")
    await expect(page.get_by_role("switch")).to_be_disabled()
    await expect(page.locator("[data-source-agree]")).to_have_count(0)
    await page.get_by_role("button", name="Review Python source").click()
    await expect(page.get_by_label("Python module source", exact=True)).to_have_text(source["source"])
    await expect(page.locator(".dc-source-hash")).to_have_text(source["sha256"])
    assert await page.evaluate("window.pwned") is None
    approve = page.get_by_role("button", name="Trust this SHA-256")
    await expect(approve).to_be_disabled()
    assert api.calls == READ_ONLY_STARTUP_CALLS
    await page.get_by_label("I reviewed this source and trust the displayed SHA-256 snapshot").check()
    await expect(approve).to_be_enabled()
    await page.set_viewport_size({"width": 480, "height": 800})
    await page.locator(".dc-module-review").scroll_into_view_if_needed()
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "studio-module-review-480.png"))
    await approve.click()
    await expect(page.locator("#dc-feedback")).to_contain_text("source snapshot trusted")
    await expect(page.get_by_role("switch")).to_have_attribute("aria-checked", str(configured).lower())
    assert api.calls == READ_ONLY_STARTUP_CALLS + [{"action": "module_trust", "id": row["id"], "sha256": source["sha256"]}]
    assert all(h.get("x-dream-token") == server.token for h in api.headers)
    assert errors == []


async def test_changed_source_requires_reload_and_second_explicit_review(controls):
    _, page, api, url, errors = controls
    row, source = module_fixture(api)
    original_hash = source["sha256"]
    await open_controls(page, url, "Extensions")
    await page.get_by_role("button", name="Review Python source").click()
    agree = page.get_by_label("I reviewed this source and trust the displayed SHA-256 snapshot")
    await agree.check()
    source.update(source="# Changed entry file\nTOOLS = []\n", sha256="b" * 64, size=32)
    await page.get_by_role("button", name="Trust this SHA-256").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("Module changed since review")
    await expect(page.get_by_role("button", name="Trust this SHA-256")).to_be_disabled()
    await expect(agree).to_be_disabled()
    await expect(agree).not_to_be_checked()
    assert api.calls == READ_ONLY_STARTUP_CALLS + [{"action": "module_trust", "id": row["id"], "sha256": original_hash}]
    await page.get_by_role("button", name="Reload source").click()
    await expect(page.locator(".dc-source-hash")).to_have_text("b" * 64)
    await expect(agree).not_to_be_checked()
    await expect(page.get_by_role("button", name="Trust this SHA-256")).to_be_disabled()
    await agree.check()
    await page.get_by_role("button", name="Trust this SHA-256").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("source snapshot trusted")
    assert api.calls[-1] == {"action": "module_trust", "id": row["id"], "sha256": "b" * 64}
    assert errors == []


@pytest.mark.parametrize("broken", ["unavailable", "id", "sha256", "source"])
async def test_unreadable_source_never_offers_approval(controls, broken):
    _, page, api, url, errors = controls
    row, source = module_fixture(api)
    if broken == "unavailable":
        api.fail[f"extensions/{row['id']}/source"] = (404, "Missing")
    else:
        source[broken] = ""
    await open_controls(page, url, "Extensions")
    await page.get_by_role("button", name="Review Python source").click()
    await expect(page.locator(".dc-module-review [role=alert]")).to_be_visible()
    await expect(page.locator("[data-source-agree]")).to_have_count(0)
    await expect(page.get_by_role("button", name="Trust this SHA-256")).to_have_count(0)
    await expect(page.get_by_role("switch")).to_be_disabled()
    assert api.calls == READ_ONLY_STARTUP_CALLS and errors == []
