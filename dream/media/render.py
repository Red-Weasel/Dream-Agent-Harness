"""CPU browser rendering and FFmpeg encoding, with owned subprocess cleanup."""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

from .composition import build_html, validate_composition

# Imported media is a single file, never a playlist that may open other paths.
# Apply to probing AND decoding. MOV external tracks remain explicitly disabled.
_INPUT_OPTIONS = ['-protocol_whitelist', 'file,pipe', '-format_whitelist',
                  'mov,matroska,webm,wav,mp3,aac,ogg,flac']
_MOV_OPTIONS = ['-enable_drefs', '0', '-use_absolute_path', '0']


def probe(path: Path) -> dict:
    binary = shutil.which('ffprobe')
    if not binary:
        raise ValueError('ffprobe is not installed')
    result = subprocess.run([binary, '-v', 'error', *_INPUT_OPTIONS, *_MOV_OPTIONS, '-show_streams', '-show_format',
        '-of', 'json', str(path)], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError(f'Invalid media output: {result.stderr.decode(errors="replace")[:1000]}')
    return json.loads(result.stdout)


async def _terminate(process):
    if process and process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


async def render(composition, assets, output: Path, *, format='mp4', progress=None,
                 cancelled=None) -> dict:
    composition = validate_composition(composition)
    if format not in ('mp4', 'webm', 'png', 'html'):
        raise ValueError('Choose mp4, webm, png or html')
    if cancelled and cancelled():
        raise asyncio.CancelledError()
    output = Path(output)
    if output.exists():
        raise ValueError('Output already exists; choose a new version path')
    output.parent.mkdir(parents=True, exist_ok=True)
    html = build_html(composition, assets)
    audio_id = composition.get('audio_asset_id')
    if audio_id and format in ('mp4', 'webm'):
        audio_url = assets[audio_id]
        if not audio_url.startswith('file:'):
            raise ValueError('Video export audio must be an imported local asset')
        audio_info = await asyncio.to_thread(probe, Path(unquote(urlsplit(audio_url).path)))
        if not any(s.get('codec_type') == 'audio' for s in audio_info.get('streams', [])):
            raise ValueError('Imported soundtrack has no audio stream')
    duration = sum(s['duration'] for s in composition['scenes'])
    frames = math.ceil(duration * composition['fps'])
    binary = shutil.which('ffmpeg')
    if format in ('mp4', 'webm'):
        missing = [name for name in ('ffmpeg', 'ffprobe') if not shutil.which(name)]
        if missing:
            raise ValueError('Video export needs ' + ' and '.join(missing) +
                             '. Install the missing FFmpeg tools, or choose HTML export.')
    with tempfile.TemporaryDirectory(prefix='.dream-render-', dir=output.parent) as scratch:
        work = Path(scratch)
        page_path = work / 'animation.html'
        page_path.write_text(html, encoding='utf-8')
        target = work / ('output.' + format)
        process = None
        try:
            if format == 'html':
                # Portable HTML exports embed only explicitly provided assets.
                import base64
                import mimetypes
                embedded = {}
                for key, url in assets.items():
                    if url.startswith('file:'):
                        path = Path(unquote(urlsplit(url).path))
                        if path.stat().st_size > 100 * 1024 * 1024:
                            raise ValueError('Portable HTML embeds assets up to 100 MiB; export video for larger media')
                        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                        embedded[key] = f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()
                    else:
                        embedded[key] = url
                target.write_text(build_html(composition, embedded), encoding='utf-8')
            else:
                from playwright.async_api import async_playwright, Error as PlaywrightError
                with (work / 'encoder.log').open('wb') as log:
                    async with async_playwright() as pw:
                        try:
                            browser = await pw.chromium.launch(headless=True, args=['--disable-gpu', '--allow-file-access-from-files'])
                        except PlaywrightError as exc:
                            raise ValueError('Export could not start Chromium. Check the local browser setup with '
                                             '`uv run --locked playwright install chromium`, or choose HTML export. '
                                             'Details: ' + str(exc)) from exc
                        try:
                            context = await browser.new_context(viewport={k: composition[k] for k in ('width', 'height')})
                            allowed = {page_path.as_uri(), *assets.values()}
                            async def route(request):
                                url = request.request.url
                                if url in allowed or url.startswith(('data:', 'blob:', 'about:')):
                                    await request.continue_()
                                else:
                                    await request.abort()
                            await context.route('**/*', route)
                            page = await context.new_page()
                            await page.goto(page_path.as_uri(), timeout=30_000)
                            await page.evaluate('window.dreamReady')
                            await page.evaluate("document.body.classList.add('export')")
                            if format == 'png':
                                await page.evaluate('(t) => window.dreamSeek(t)', min(.8, duration / 2))
                                await page.screenshot(path=str(target), type='png')
                            else:
                                args = [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                                    '-f', 'image2pipe', '-vcodec', 'png', '-framerate', str(composition['fps']), '-i', 'pipe:0']
                                audio = composition.get('audio_asset_id')
                                if audio:
                                    audio_url = assets[audio]
                                    if not audio_url.startswith('file:'):
                                        raise ValueError('Video export audio must be an imported local asset')
                                    mov_options = _MOV_OPTIONS if 'mov' in audio_info['format']['format_name'].split(',') else []
                                    args += [*_INPUT_OPTIONS, *mov_options, '-i', unquote(urlsplit(audio_url).path), '-map', '0:v:0', '-map', '1:a:0',
                                             '-af', 'apad', '-t', str(frames / composition['fps'])]
                                args += (['-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                                          '-movflags', '+faststart', '-c:a', 'aac'] if format == 'mp4' else
                                         ['-c:v', 'libvpx-vp9', '-crf', '28', '-b:v', '0', '-c:a', 'libopus'])
                                args += ['-threads', '2', str(target)]
                                process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                    stdout=asyncio.subprocess.DEVNULL, stderr=log, start_new_session=True)
                                async with asyncio.timeout(7200):
                                    for index in range(frames):
                                        if cancelled and cancelled():
                                            raise asyncio.CancelledError()
                                        await page.evaluate('(t) => window.dreamSeek(t)', index / composition['fps'])
                                        process.stdin.write(await page.screenshot(type='png'))
                                        await process.stdin.drain()
                                        if progress:
                                            progress((index + 1) / (frames + 1))
                                    process.stdin.close()
                                    await process.wait()
                                if process.returncode:
                                    raise ValueError('FFmpeg failed: ' + (work / 'encoder.log').read_text(errors='replace')[-1500:])
                        finally:
                            await browser.close()
                if format in ('mp4', 'webm'):
                    metadata = await asyncio.to_thread(probe, target)
                    video = next((s for s in metadata.get('streams', []) if s['codec_type'] == 'video'), None)
                    if not video or (video['width'], video['height']) != (composition['width'], composition['height']):
                        raise ValueError('Encoded output failed dimension verification')
                    if abs(float(metadata['format']['duration']) - frames / composition['fps']) > .2:
                        raise ValueError('Encoded output failed duration verification')
            if cancelled and cancelled():
                raise asyncio.CancelledError()
            # link fails if another task published this output in the meantime.
            os.link(target, output)
            if progress:
                progress(1)
            return {'path': str(output), 'format': format, 'duration': duration,
                    'frames': frames if format in ('mp4', 'webm') else 1, 'size': output.stat().st_size}
        finally:
            await _terminate(process)
