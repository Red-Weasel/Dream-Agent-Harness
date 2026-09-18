"""Real Controls UI with intercepted metadata and no inference requests."""
import copy

from playwright.async_api import expect

from test_studio_controls import READ_ONLY_STARTUP_CALLS, controls, open_controls, no_overflow  # noqa: F401


PERFORMANCE = {
    "supported": True, "current": "custom", "reason": "", "applies": "next_user_turn",
    "effective": {"output_tokens": 12000, "reasoning_effort": "medium"},
    "modes": [
        {"name": "custom", "label": "Custom", "description": "Restore original session settings.", "output_tokens": 12000, "reasoning_effort": "medium"},
        {"name": "quick", "label": "Quick", "description": "Shorter output and lower supported reasoning effort.", "output_tokens": 2048, "reasoning_effort": "low"},
        {"name": "balanced", "label": "Balanced", "description": "Moderate output and reasoning allowance.", "output_tokens": 8192, "reasoning_effort": "medium"},
        {"name": "thorough", "label": "Thorough", "description": "Full session output allowance and highest supported reasoning effort.", "output_tokens": 12000, "reasoning_effort": "high"},
    ],
}


async def test_modes_preview_then_apply_and_restore_without_saving_profile(controls, tmp_path):
    _, page, api, url, errors = controls
    api.runtime["performance"] = copy.deepcopy(PERFORMANCE)
    initial_settings = copy.deepcopy(api.runtime["settings"])

    async def apply(route):
        payload = route.request.post_data_json
        if payload["action"] == "permission_mode_status":
            await route.fallback()
            return
        assert payload["action"] == "performance"
        api.calls.append(payload)
        mode = next(row for row in PERFORMANCE["modes"] if row["name"] == payload["mode"])
        api.runtime["performance"].update(current=mode["name"], effective={key: mode[key] for key in ("output_tokens", "reasoning_effort")})
        await route.fulfill(json={"ok": True, "result": api.runtime["performance"]})

    await page.route("**/api/control", apply)
    await open_controls(page, url)
    await expect(page.locator("#dc-performance-mode")).to_have_value("custom")
    assert api.calls == READ_ONLY_STARTUP_CALLS
    await page.locator("#dc-performance-mode").select_option("quick")
    await expect(page.locator("#dc-performance-preview")).to_contain_text("2,048")
    await expect(page.locator("#dc-performance-preview")).to_contain_text("low")
    await page.screenshot(path=str(tmp_path / "performance-preview-800.png"))
    assert api.calls == READ_ONLY_STARTUP_CALLS
    await page.get_by_role("button", name="Apply to next turn").click()
    await expect(page.locator("#dc-performance-current")).to_contain_text("Quick")
    await expect(page.locator("#dc-feedback")).to_contain_text("next user turn")
    await page.locator("#dc-performance-mode").select_option("custom")
    await page.get_by_role("button", name="Apply to next turn").click()
    await expect(page.locator("#dc-performance-current")).to_contain_text("Custom")
    assert api.calls == READ_ONLY_STARTUP_CALLS + [{"action": "performance", "mode": "quick"}, {"action": "performance", "mode": "custom"}]
    assert api.runtime["settings"] == initial_settings
    await page.set_viewport_size({"width": 480, "height": 800})
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "performance-custom-480.png"))
    assert not errors


async def test_unsupported_or_failed_mode_remains_honest(controls):
    _, page, api, url, errors = controls
    api.runtime["performance"] = {"supported": False, "reason": "Native CLI owns generation settings"}
    await open_controls(page, url)
    await expect(page.locator("#dc-performance-state")).to_contain_text("Native CLI")
    await expect(page.locator("#dc-performance-mode")).to_be_disabled()
    api.runtime["performance"] = copy.deepcopy(PERFORMANCE)
    await page.get_by_role("button", name="Refresh controls").click()
    await page.locator("#dc-performance-mode").select_option("quick")
    api.fail["performance"] = (400, "Mode unavailable for this adapter")
    await page.get_by_role("button", name="Apply to next turn").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("Mode unavailable")
    await expect(page.locator("#dc-performance-current")).to_contain_text("Custom")
    assert not errors
