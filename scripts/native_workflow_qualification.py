"""Opt-in actual GTK/WebKit qualification with a real model-free Studio server.

Run with system Python on a PRIVATE software-rendered display, for example:
DISPLAY=:117 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
WEBKIT_DISABLE_DMABUF_RENDERER=1 WEBKIT_DISABLE_COMPOSITING_MODE=1 \
/usr/bin/python3 scripts/native_workflow_qualification.py --output /tmp/native-check

Requires GTK/VTE/WebKit GI, FFmpeg with libx264, and the checkout's .venv.
Never runs Engine or loads models. Stop/resume checks cover UI and HTTP dispatch
against explicit callbacks, not durable Engine recovery or owner acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixture_environment(inherited: dict[str, str], workspace: Path) -> dict[str, str]:
    """Keep only display/locale inputs; state and Python imports belong to the fixture."""
    env = {key: inherited[key] for key in ('DISPLAY', 'XAUTHORITY', 'LANG', 'LC_ALL', 'TZ')
           if key in inherited}
    env.update(PATH='/usr/bin:/bin', PYTHONPATH=str(ROOT), PYTHONUNBUFFERED='1',
        DREAM_ROOT=str(workspace / 'state'), DREAM_GUI='1', DREAM_GUI_OPEN='0',
        DREAM_DESKTOP_SESSION_FILE=str(workspace / 'session.json'),
        DREAM_SEMANTIC_MEMORY='0', DREAM_RERANK='0', DREAM_CONSOLIDATE='0',
        GDK_BACKEND='x11', LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe',
        WEBKIT_DISABLE_DMABUF_RENDERER='1', WEBKIT_DISABLE_COMPOSITING_MODE='1',
        GST_PLUGIN_FEATURE_RANK='vaapidecodebin:NONE,vaapih264dec:NONE,vah264dec:NONE,nvh264dec:NONE',
        GIO_USE_VFS='local', GTK_USE_PORTAL='0', NO_AT_BRIDGE='1')
    for key, name in (('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                      ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'),
                      ('XDG_RUNTIME_DIR', 'runtime')):
        directory = workspace / ('xdg-' + name)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[key] = str(directory)
    return env


def isolate_environment(workspace: Path):
    env = fixture_environment(dict(os.environ), workspace)
    os.environ.clear()
    os.environ.update(env)


def require_clean_exit(wait_status: int | None):
    if wait_status is None:
        raise RuntimeError('No VTE child exit status was observed')
    exit_code = os.waitstatus_to_exitcode(wait_status)
    if exit_code != 0:
        raise RuntimeError(f'VTE Studio child exited unsuccessfully: {exit_code}')


async def serve(workspace: Path):
    isolate_environment(workspace)
    from dream.core.backends.base import Event
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from dream.tools import studio as studio_tools
    from dream.tools.context import ToolContext, set_context, set_studio

    queue = asyncio.Queue()
    calls = []
    report = {}

    def record(kind, value):
        calls.append({'kind': kind, 'value': value})
        (workspace / 'calls.json').write_text(json.dumps(calls))

    def prompt(value):
        record('prompt', value)
        queue.put_nowait(value)

    def control(data):
        if data.get('action') == 'interrupt':
            record('control', 'interrupt')
            server.bus.publish(Event('turn_end', {'interrupted': True}))
            return {'ok': True}
        return {'mode': 'ask', 'available': True}

    server = StudioServer(EventBus(), session={'provider': 'Native qualification fixture',
        'model': 'No inference', 'workspace': str(workspace), 'session_id': 'native-qualification'},
        on_prompt=prompt, on_control=control)

    def emit(event):
        server.retain_show(event)
        server.bus.publish(event)

    set_context(ToolContext(store=None, working=None, browser=None, session_id='native-qualification',
                            workspace=workspace, emit=emit))
    set_studio(server)
    try:
        await server.start()
        assert studio_tools._show_in_studio(workspace / 'video.html', 'video.html')
        while True:
            command = await queue.get()
            if command == '/quit':
                break
            if command == 'probe-media':
                code = '''return (async () => {
                    const v=document.querySelector('video'); v.muted=true;
                    await v.play(); await new Promise(r=>setTimeout(r,350));
                    const first=v.currentTime; v.pause();
                    await new Promise(r=>setTimeout(r,200)); const paused=v.currentTime;
                    await v.play(); await new Promise(r=>setTimeout(r,300));
                    const resumed=v.currentTime; v.pause();
                    let isolated=false;try{void parent.document.body;}catch(e){isolated=e.name==='SecurityError';}
                    return {width:v.videoWidth,height:v.videoHeight,first,paused,resumed,isolated,error:v.error?.code||null};
                })();'''
                try:
                    report['media'] = await server.ask_frame('eval', {'code': code}, timeout=12)
                except Exception as exc:
                    report['media_error'] = str(exc)
            elif command == 'probe-invalid':
                code = '''return new Promise(resolve=>{const v=document.querySelector('video');
                    v.onerror=()=>resolve({code:v.error.code});v.src='data:video/mp4;base64,bm90IGEgdmFsaWQgbW92aWU=';v.load();
                    setTimeout(()=>resolve({timeout:true}),3000);});'''
                try:
                    report['invalid'] = await server.ask_frame('eval', {'code': code}, timeout=6)
                except Exception as exc:
                    report['invalid_error'] = str(exc)
            elif command == 'start-fixture-turn':
                server.bus.publish(Event('turn_start', {}))
            elif command == 'resume-fixture-turn':
                server.bus.publish(Event('turn_start', {}))
                server.bus.publish(Event('text_delta', 'Fixture resumed once.'))
                server.bus.publish(Event('turn_end', {}))
            elif command == 'probe-connections':
                report['clients'] = len(server._clients)
            (workspace / 'server-report.json').write_text(json.dumps(report))
    finally:
        await server.stop()
        set_studio(None)


def qualify(output: Path):
    from urllib.request import Request, build_opener, ProxyHandler

    if os.environ.get('LIBGL_ALWAYS_SOFTWARE') != '1':
        raise SystemExit('Set LIBGL_ALWAYS_SOFTWARE=1 and use a private software display.')
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    checks = []
    error = None
    with tempfile.TemporaryDirectory(prefix='dream-native-workflow-') as temp:
        workspace = Path(temp)
        isolate_environment(workspace)
        from dream.desktop.window import DreamWindow, Gdk, GLib, Gtk, Vte
        if not Gtk.init_check([])[0]:
            raise SystemExit('A private graphical display is required.')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'color=c=blue:s=32x32:r=12', '-t', '3', '-c:v', 'libx264', '-threads', '1',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(workspace / 'clip.mp4')],
            check=True, timeout=20)
        (workspace / 'video.html').write_text('<video controls muted playsinline src="clip.mp4"></video>')
        window = DreamWindow(str(ROOT / '.venv/bin/python'), str(workspace), [], autostart=False)
        window.running = True
        window.discovery = workspace / 'session.json'
        state = {'stage': 0, 'busy': False}
        window.terminal.connect('child-exited', lambda _terminal, status: state.update(child_wait_status=status))
        env = fixture_environment(window.child_env(), workspace)
        window.terminal.spawn_async(Vte.PtyFlags.DEFAULT, str(ROOT),
            [str(ROOT / '.venv/bin/python'), str(Path(__file__).resolve()), '--server', str(workspace)],
            [f'{key}={value}' for key, value in env.items()], GLib.SpawnFlags.DEFAULT,
            None, None, -1, None, window._spawned, None)

        def send(value):
            base, token = window.address_info
            request = Request(base + '/api/prompt', data=json.dumps({'prompt': value}).encode(),
                headers={'Content-Type': 'application/json', 'x-dream-token': token})
            with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                response.read()

        def evaluate(code, callback):
            state['busy'] = True
            def done(view, result, _data):
                state['busy'] = False
                try:
                    raw = view.evaluate_javascript_finish(result).to_json(0)
                    callback(json.loads(raw) if raw else None)
                except Exception as exc:
                    state['exception'] = str(exc)
            window.studio.evaluate_javascript(code, -1, None, None, None, done, None)

        def advance(message):
            checks.append(message)
            state['stage'] += 1

        def tick():
            nonlocal error
            try:
                if time.monotonic() - started > 65:
                    raise AssertionError(f"Timed out at stage {state['stage']}")
                if state.get('exception'):
                    raise AssertionError(state['exception'])
                if state['busy']:
                    return True
                report_file = workspace / 'server-report.json'
                report = json.loads(report_file.read_text()) if report_file.exists() else {}
                stage = state['stage']
                if stage == 0 and window.connected:
                    def loaded(value):
                        if value:
                            send('probe-media')
                            advance('Actual native Studio loaded the queued sandboxed video artifact.')
                    evaluate("!!document.querySelector('#artbody iframe')", loaded)
                elif stage == 1 and ('media' in report or 'media_error' in report):
                    assert 'media_error' not in report, report
                    media = report['media']
                    assert media['width'] == media['height'] == 32 and not media['error'], media
                    assert media['first'] > 0 and abs(media['paused'] - media['first']) < .05, media
                    assert media['resumed'] > media['paused'] and media['isolated'], media
                    advance('WebKit decoded H.264; its clock advanced, paused, resumed, and origin isolation held.')
                    send('probe-invalid')
                elif stage == 2 and ('invalid' in report or 'invalid_error' in report):
                    assert report.get('invalid', {}).get('code') in (3, 4), report
                    def media_error(value):
                        if value and 'Preview reported media error' in value:
                            advance('Invalid MP4 produced a native media error and visible actionable Studio status.')
                            window._connect_studio(force=True)
                            state['reconnected_at'] = time.monotonic()
                    evaluate("document.getElementById('dream-output-evidence')?.textContent", media_error)
                elif stage == 3 and time.monotonic() - state['reconnected_at'] > 2:
                    def reloaded(value):
                        if value:
                            send('probe-connections')
                            advance('Native reconnect restored the delivered artifact.')
                    evaluate("!!document.querySelector('#artbody iframe')", reloaded)
                elif stage == 4 and 'clients' in report:
                    assert report['clients'] == 1, report
                    checks.append('Exactly one Studio websocket remained after reconnect.')
                    window.studio.terminate_web_process()
                    state['stage'] = 4.1
                elif stage == 4.1 and window.studio_failed:
                    assert window.studio_stack.get_visible_child_name() == 'welcome'
                    checks.append('Terminating the owned WebKit process displayed the native recovery page.')
                    state['stage'] = 4.2
                elif stage == 4.2 and not window.studio_failed:
                    def recovered(value):
                        if value:
                            send('start-fixture-turn')
                            checks.append('Native polling restored Studio automatically after the WebKit process stopped.')
                            state['stage'] = 5
                    evaluate("!!document.querySelector('#artbody iframe')", recovered)
                elif stage == 5:
                    def stop(value):
                        if value:
                            advance('Visible native Stop dispatched the fixture interrupt.')
                    evaluate("(()=>{const b=document.getElementById('stop');if(!b||b.hidden||b.disabled)return false;b.click();return true;})()", stop)
                elif stage == 6:
                    calls = json.loads((workspace / 'calls.json').read_text())
                    if {'kind': 'control', 'value': 'interrupt'} in calls:
                        def resume(value):
                            if value:
                                advance('Stop completed; native composer accepted a subsequent fixture turn.')
                        evaluate("(()=>{const b=document.getElementById('stop');if(!b.hidden)return false;const input=document.getElementById('input');input.value='resume-fixture-turn';document.getElementById('send').click();return true;})()", resume)
                elif stage == 7:
                    calls = json.loads((workspace / 'calls.json').read_text())
                    if {'kind': 'prompt', 'value': 'resume-fixture-turn'} in calls:
                        for value in ('probe-media', 'probe-invalid', 'start-fixture-turn', 'resume-fixture-turn'):
                            assert calls.count({'kind': 'prompt', 'value': value}) == 1, calls
                        assert calls.count({'kind': 'control', 'value': 'interrupt'}) == 1, calls
                        (output / 'calls.json').write_text(json.dumps(calls, indent=2))
                        (output / 'server-report.json').write_text(json.dumps(report, indent=2))
                        pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0,
                            window.get_allocated_width(), window.get_allocated_height())
                        pixbuf.savev(str(output / 'native-workflow.png'), 'png', [], [])
                        checks.append('Each tested prompt and Stop reached its callback exactly once.')
                        assert 'optimizer' in window.nav_buttons, 'Native sidebar has no Prompt Optimizer entry'
                        window.nav_buttons['optimizer'].clicked()
                        state['stage'] = 7.1
                elif stage == 7.1:
                    def prepare(value):
                        if value:
                            state['stage'] = 7.2
                    evaluate("(()=>{const p=document.getElementById('dream-prompt-optimizer');if(!p||p.hidden)return false;document.getElementById('input').value='Existing native draft';const d=document.getElementById('po-draft');d.value='Review the project notes';d.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('po-quick').click();return true;})()", prepare)
                elif stage == 7.2:
                    def prepared(value):
                        if value:
                            assert 'Review the project notes' in value, value
                            checks.append('Native Prompt Optimizer opened and real Quick structure returned a prepared prompt without inference.')
                            state['stage'] = 7.3
                    evaluate("document.getElementById('po-result')?.value", prepared)
                elif stage == 7.3:
                    def use(value):
                        assert value is True, 'Use in chat did not preserve the existing draft for explicit append'
                        state['stage'] = 7.4
                    evaluate("(()=>{document.getElementById('po-use').click();const choice=document.getElementById('po-append-choice');if(choice.hidden||document.getElementById('input').value!=='Existing native draft')return false;document.getElementById('po-append').click();return true;})()", use)
                elif stage == 7.4:
                    def appended(value):
                        if value:
                            calls = json.loads((workspace / 'calls.json').read_text())
                            assert not any('Review the project notes' in call['value'] for call in calls), calls
                            checks.append('Native Use in chat preserved the draft, appended only after the explicit choice, and sent no prompt.')
                            send('/quit')
                            state['stage'] = 8
                    evaluate("(()=>{const p=document.getElementById('dream-prompt-optimizer'),v=document.getElementById('input').value;return v.includes('Existing native draft')&&v.includes('Review the project notes');})()", appended)
                elif stage == 8 and not window.running:
                    require_clean_exit(state.get('child_wait_status'))
                    advance('The real VTE Studio child exited cleanly after /quit.')
                    window.destroy()
                    return False
            except Exception as exc:
                error = str(exc)
                window.terminal.disconnect_by_func(window._child_exited)
                window.destroy()
                return False
            return True

        GLib.timeout_add(200, tick)
        Gtk.main()
        for name in ('calls.json', 'server-report.json'):
            source = workspace / name
            if source.exists():
                (output / name).write_bytes(source.read_bytes())
        # Only this fixture child belongs to us; never leave it running on failure.
        if window.child_pid:
            try:
                os.kill(window.child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if state['stage'] != 9 and error is None:
            error = f"Window ended before qualification completed: stage {state['stage']}"
    result = {'checks': checks, 'error': error, 'child_wait_status': state.get('child_wait_status'), 'seconds': round(time.monotonic() - started, 2),
              'limits': 'Model-free callbacks. No Engine, durable run recovery, model quality or owner acceptance.'}
    (output / 'native-report.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', type=Path)
    parser.add_argument('--output', type=Path, default=Path('/tmp/dream-native-workflow-report'))
    args = parser.parse_args()
    if args.server:
        asyncio.run(serve(args.server))
    else:
        qualify(args.output)
