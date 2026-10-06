"""Actual media playback in the shipped isolated Studio frame; no model or GPU."""
import base64
from types import SimpleNamespace

from playwright.async_api import expect
from dream.core.backends.base import Event
from dream.tools import studio as studio_tools
from test_studio_controls import controls

# Synthetic one-second 32px blue H.264/yuv420p clip, generated locally with FFmpeg.
MP4 = "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAANibW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAox0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAACAAAAAgAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAgAAABAAAAAAIEbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAQABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABr21pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAW9zdGJsAAAAv3N0c2QAAAAAAAAAAQAAAK9hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAACAAIABIAAAASAAAAAAAAAABFUxhdmM2MC4zMS4xMDIgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANWF2Y0MBZAAK/+EAGGdkAAqs2UlsBEAAAAMAQAAAAwIDxIllgAEABmjr48siwP34+AAAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAAAXqAAAF6gAAAAYc3R0cwAAAAAAAAABAAAABAAAEAAAAAAUc3RzcwAAAAAAAAABAAAAAQAAAChjdHRzAAAAAAAAAAMAAAABAAAgAAAAAAEAAEAAAAAAAgAAEAAAAAAcc3RzYwAAAAAAAAABAAAAAQAAAAQAAAABAAAAJHN0c3oAAAAAAAAAAAAAAAQAAALQAAAADQAAAAwAAAAMAAAAFHN0Y28AAAAAAAAAAQAAA5IAAABidWR0YQAAAFptZXRhAAAAAAAAACFoZGxyAAAAAAAAAABtZGlyYXBwbAAAAAAAAAAAAAAAAC1pbHN0AAAAJal0b28AAAAdZGF0YQAAAAEAAAAATGF2ZjYwLjE2LjEwMAAAAAhmcmVlAAAC/W1kYXQAAAKtBgX//6ncRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY0IHIzMTA4IDMxZTE5ZjkgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDIzIC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49NCBzY2VuZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMDAAgAAAABtliIQAEv/+6Mn8yy155nUaiZZaD5WnG7BwL+EAAAAJQZojbEEP/quAAAAACEGeQXiCPy+hAAAACAGeYmpBDzUg"


async def test_visible_studio_decodes_and_plays_embedded_mp4_without_origin_access(controls, tmp_path, monkeypatch):
    server, page, api, url, errors = controls
    (tmp_path / 'clip.mp4').write_bytes(base64.b64decode(MP4))
    html = tmp_path / 'clip.html'
    html.write_text('<video controls muted playsinline src="clip.mp4"></video>')
    emitted = []
    monkeypatch.setattr(studio_tools, 'ctx', lambda: SimpleNamespace(emit=emitted.append))
    monkeypatch.setattr(studio_tools, 'studio', lambda: server)
    assert studio_tools._show_in_studio(html, 'clip.html')
    assert len(emitted) == 1
    await page.goto(url)
    video = page.frame_locator('#artbody iframe').locator('video')
    await expect(video).to_be_visible()
    await video.evaluate('v => v.play()')
    await expect(video).to_have_js_property('videoWidth', 32)
    await expect(video).to_have_js_property('videoHeight', 32)
    await _advanced(video)
    assert await video.evaluate('v => v.currentTime > 0')
    assert await page.locator('#artbody iframe').get_attribute('sandbox') == 'allow-scripts'
    assert await video.evaluate("v => {try { void parent.document.body; return false; } catch(e) { return e.name === 'SecurityError'; }}")
    assert not errors


async def _advanced(video):
    await video.evaluate("v => new Promise((resolve, reject) => { if (v.currentTime > 0) return resolve(); const timer = setTimeout(() => reject(new Error('video clock did not advance')), 3000); v.addEventListener('timeupdate', () => { if(v.currentTime > 0) { clearTimeout(timer); resolve(); } }); })")


REMOTE = ('<video id="remote" src="{src}"></video><script>window.violations=[];'
          'document.addEventListener("securitypolicyviolation",e=>window.violations.push(e.effectiveDirective));</script>')


async def _try_remote(controls, src, *, internet):
    """Show a page whose video is at `src` with the header's internet switch on or off (DREAM-234); return
    (the frame saw a media-src violation, the URLs the browser actually asked for)."""
    server, page, api, url, errors = controls
    requested = []
    await page.route('**://media.example.invalid/**', lambda route: (requested.append(route.request.url), route.abort())[-1])
    await page.add_init_script("try{ localStorage.setItem('dream.previewNet', '%s'); }catch(e){}" % ('1' if internet else '0'))
    server.retain_show(Event('studio', {'op':'show','path':'remote.html','content':REMOTE.format(src=src)}))
    await page.goto(url)
    video = page.frame_locator('#artbody iframe').locator('video')
    await video.evaluate("v => { v.load(); return new Promise(resolve => { v.onerror=resolve; setTimeout(resolve, 1000); }); }")
    return await video.evaluate("v => window.violations.includes('media-src')"), requested


async def test_visible_studio_blocks_remote_media_with_the_internet_switch_off(controls):
    blocked, requested = await _try_remote(controls, 'https://media.example.invalid/clip.mp4', internet=False)
    assert blocked and not requested


async def test_visible_studio_asks_for_https_media_with_the_internet_switch_on(controls):
    # The owner's rule (DREAM-234): the preview has the internet. On is the default; the request leaves the frame.
    blocked, requested = await _try_remote(controls, 'https://media.example.invalid/clip.mp4', internet=True)
    assert not blocked and set(requested) == {'https://media.example.invalid/clip.mp4'}   # asked for (the element, then load())


async def test_visible_studio_never_loads_plain_http_media(controls):
    # https only: a plain-http address (a loopback service, a LAN box) stays out of reach with the switch on.
    blocked, requested = await _try_remote(controls, 'http://media.example.invalid/clip.mp4', internet=True)
    assert blocked and not requested
