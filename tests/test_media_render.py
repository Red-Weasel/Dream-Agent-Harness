"""Media rendering contracts: valid timing, safe text, real encoded output."""
import json
import shutil

import pytest


def test_composition_rejects_invalid_timing_and_dimensions():
    from dream.media.composition import validate_composition
    for patch in ({"fps": 0}, {"width": -2}, {"width": 101}, {"fps": float('nan')},
                  {"scenes": [{"duration": float('inf')}]}, {"scenes": []}):
        with pytest.raises(ValueError):
            validate_composition({"scenes": [{"duration": 2, "title": "Hello"}], **patch})


def test_composition_does_not_mutate_input_and_preserves_scene_identity():
    from dream.media.composition import validate_composition
    source = {"scenes": [{"id": "intro", "duration": 2, "title": "Hello"}]}
    original = json.dumps(source)
    result = validate_composition(source)
    assert result['scenes'][0]['id'] == 'intro'
    assert result['width'] == 1920
    assert json.dumps(source) == original


def test_player_escapes_script_text_and_refuses_remote_assets():
    from dream.media.composition import build_html
    dangerous = '</script><script>alert(1)</script>'
    doc = build_html({"scenes": [{"duration": 2, "title": dangerous}]}, {})
    assert dangerous not in doc
    with pytest.raises(ValueError):
        build_html({"scenes": [{"duration": 2, "asset_id": "a"}]}, {"a": "https://example.com/a.png"})


@pytest.mark.asyncio
async def test_real_render_has_expected_duration_dimensions_and_frames(tmp_path):
    from dream.media.render import render, probe
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/ffprobe unavailable')
    output = tmp_path / 'demo.mp4'
    progress = []
    result = await render({"width": 320, "height": 180, "fps": 5,
        "scenes": [{"duration": 1, "title": "Hello", "animation": "none"},
                   {"duration": 1, "title": "Goodbye", "animation": "none"}]},
        {}, output, progress=progress.append)
    info = probe(output)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == (320, 180)
    assert int(video['nb_frames']) == 10
    assert abs(float(info['format']['duration']) - 2) < .05
    assert result['path'] == str(output)
    assert progress[-1] == 1


@pytest.mark.asyncio
async def test_cancelled_render_never_publishes_output(tmp_path):
    from dream.media.render import render
    import asyncio
    path = tmp_path / 'cancelled.mp4'
    with pytest.raises(asyncio.CancelledError):
        await render({"scenes": [{"duration": 1}]}, {}, path, cancelled=lambda: True)
    assert not path.exists()


@pytest.mark.asyncio
async def test_seek_is_repeatable_and_switches_scenes_without_executing_text(tmp_path):
    from dream.media.composition import build_html
    from playwright.async_api import async_playwright
    document = build_html({"width": 320, "height": 180, "scenes": [
        {"duration": 1, "title": '</script><script>window.infected=true</script>', 'animation': 'none'},
        {"duration": 1, "title": 'Second scene', 'animation': 'none'}]}, {})
    path = tmp_path / 'player.html'
    path.write_text(document)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 320, 'height': 180})
            await page.goto(path.as_uri())
            await page.evaluate('window.dreamReady')
            await page.evaluate("document.body.classList.add('export')")
            await page.evaluate('window.dreamSeek(.5)')
            first = await page.screenshot()
            await page.evaluate('window.dreamSeek(1.5)')
            assert await page.locator('h1').text_content() == 'Second scene'
            second = await page.screenshot()
            await page.evaluate('window.dreamSeek(.5)')
            # Chromium can change antialiasing by a few levels when rebuilding
            # a layer. The observed repeat seek varied 21/57,600 pixels, max 4.
            # Compare decoded pixels, with a strict bound on magnitude and area.
            from io import BytesIO
            from PIL import Image, ImageChops
            repeated = await page.screenshot()
            delta = ImageChops.difference(Image.open(BytesIO(first)).convert('RGB'),
                                         Image.open(BytesIO(repeated)).convert('RGB'))
            assert max(hi for lo, hi in delta.getextrema()) <= 4
            assert sum(pixel != (0, 0, 0) for pixel in delta.get_flattened_data()) <= 58
            assert first != second
            assert await page.evaluate('window.infected === undefined')
            box = await page.locator('h1').bounding_box()
            assert box['height'] < 150
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_html_export_embeds_imported_asset_and_rejects_overwrite(tmp_path):
    from dream.media.render import render
    source = tmp_path / 'reference.png'
    source.write_bytes(b'png data')
    output = tmp_path / 'site.html'
    composition = {'scenes': [{'duration': 1, 'asset_id': 'a'}]}
    await render(composition, {'a': source.as_uri()}, output, format='html')
    assert source.as_uri() not in output.read_text()
    assert 'data:image/png;base64,' in output.read_text()
    with pytest.raises(ValueError, match='exists'):
        await render(composition, {'a': source.as_uri()}, output, format='html')


@pytest.mark.parametrize('extension', ['m3u8', 'mp4'])
def test_probe_rejects_playlists_even_when_renamed_as_video(tmp_path, extension):
    import subprocess
    from dream.media.render import probe
    outside = tmp_path/'outside.ts'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-c:a','aac','-f','mpegts',str(outside)],check=True)
    playlist = tmp_path/('renamed.'+extension)
    playlist.write_text('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.0,\n'+str(outside)+'\n#EXT-X-ENDLIST\n')
    with pytest.raises(ValueError, match='Invalid media'):
        probe(playlist)


@pytest.mark.asyncio
async def test_imported_soundtrack_is_encoded_and_manifest_audio_is_rejected(tmp_path):
    import wave
    from dream.media.render import render, probe
    audio = tmp_path/'tone.wav'
    with wave.open(str(audio),'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000); out.writeframes(b'\0\0'*8000)
    c = {'width':160,'height':90,'fps':2,'audio_asset_id':'audio','scenes':[{'duration':1,'title':'Soundtrack'}]}
    output = tmp_path/'audio.mp4'
    await render(c,{'audio':audio.as_uri()},output)
    assert any(s['codec_type']=='audio' for s in probe(output)['streams'])
    manifest = tmp_path/'audio.m3u8'
    manifest.write_text('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.0,\n'+str(audio)+'\n#EXT-X-ENDLIST\n')
    with pytest.raises(ValueError, match='Invalid media'):
        await render(c,{'audio':manifest.as_uri()},tmp_path/'blocked.mp4')


@pytest.mark.asyncio
async def test_missing_ffprobe_fails_before_browser_render_and_html_still_works(tmp_path, monkeypatch):
    from dream.media.render import render
    real_which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name: None if name == 'ffprobe' else real_which(name))
    composition = {'scenes': [{'duration': 1, 'title': 'Dependency check'}]}
    with pytest.raises(ValueError, match='Video export needs ffprobe.*HTML export'):
        await render(composition, {}, tmp_path / 'missing.mp4')
    assert not (tmp_path / 'missing.mp4').exists()
    result = await render(composition, {}, tmp_path / 'available.html', format='html')
    assert result['format'] == 'html'
    assert 'Dependency check' in (tmp_path / 'available.html').read_text()
