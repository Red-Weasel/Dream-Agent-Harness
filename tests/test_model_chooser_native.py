"""Actual GTK journey with metadata fixtures; no providers or model loads."""
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_load_screen_card import _gtk, _box, _labels, settings_for


def drain(Gtk):
    for _ in range(32):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(.01)


def screen(tmp_path, width):
    Gtk = _gtk()
    from gi.repository import Gdk
    from dream.desktop.onboarding import Onboarding
    css = Gtk.CssProvider()
    css.load_from_path('dream/desktop/style.css')
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    calls = []
    owner = SimpleNamespace(cwd=str(tmp_path), running=False, stopped=False, python='python3', child_env=dict,
                            open_terminal=lambda: None, launch_selection=calls.append)
    panel = Onboarding(owner)
    win = Gtk.Window()
    win.set_default_size(width, 1000)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.add(panel)
    win.add(scroll)
    win.show_all()
    settings = settings_for(tmp_path, 'qwen27')
    settings['facts'] = dict(context_limit=262144, context_source='GGUF metadata', configured_context=32768,
                             vision=True, speculative=dict(supported=True, enabled=True, draft='Native MTP', count=3))
    choices = [dict(id='service', kind='provider', provider='codex', ready=True, label='Codex',
                    note='Installed client detected. Account access is checked on your first message.')]
    choices += [dict(id=str(i), kind='local', provider='machx', ready=True, path=f'/library/model-{i}/q4_k_m.gguf',
                     label=f'Qwen 3.8 {i}B', display_name=f'Qwen 3.8 {i}B', quantization='Q4_K_M',
                     format='GGUF', size_gb=16.9, shard_count=1, vision=True, location='Expansion',
                     metadata_source='GGUF header', note='Local files stay on this computer.') for i in range(1, 65)]
    panel.query = lambda action, callback, *args: callback(settings)
    panel.catalog_loaded(dict(choices=choices, council_choices=[], saved_council=None,
                              discovery=dict(scope='Configured model folders and mounted drives.', roots=['/library'],
                                             refresh='Refresh reads metadata only.', privacy='No uploads.')))
    drain(Gtk)
    return Gtk, win, panel, settings, calls


def shot(win, name):
    from gi.repository import Gtk
    drain(Gtk)
    folder = os.environ.get('DREAM_LOAD_SCREEN_SHOTS')
    if folder:
        from gi.repository import Gdk
        Path(folder).mkdir(parents=True, exist_ok=True)
        pix = Gdk.pixbuf_get_from_window(win.get_window(), 0, 0, win.get_allocated_width(), win.get_allocated_height())
        pix.savev(str(Path(folder) / f'{name}.png'), 'png', [], [])


@pytest.mark.parametrize('width', [1920, 900, 620])
def test_native_library_selection_responsive_keyboard_and_live_facts(tmp_path, width):
    Gtk, win, panel, settings, calls = screen(tmp_path, width)
    from gi.repository import Gdk
    panel.engines.clicked()
    drain(Gtk)
    assert panel.engines.popover.get_visible()
    assert panel.engines.scroll.get_allocated_height() <= 722
    unselected_name = list(_labels(panel.engines.list.get_children()[2]))[0]
    color = unselected_name.get_style_context().get_color(Gtk.StateFlags.NORMAL)
    assert min(color.red, color.green, color.blue) > .5
    shot(win, f'library-{width}')
    panel.engines.search.set_text('qwen 3.8 27b')
    panel.engines.filter_rows()
    drain(Gtk)
    visible = [row for row in panel.engines.list.get_children() if row.get_visible()]
    assert len(visible) == 1
    assert panel.engines.scroll.get_allocated_height() <= 722
    shot(win, f'chooser-{width}')
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = Gdk.KEY_Down
    panel.engines.search.emit('key-press-event', event)
    assert win.get_focus() == visible[0]
    event.keyval = Gdk.KEY_Return
    panel.engines.search.emit('key-press-event', event)
    drain(Gtk)
    assert panel.choices[panel.engines.get_active()]['path'] == '/library/model-27/q4_k_m.gguf'
    assert panel.engines.title.get_text() == 'Qwen 3.8 27B'
    assert panel.main_model.get_visible() is False
    assert panel.file_details.get_visible()
    panel.file_details.set_expanded(True)
    drain(Gtk)
    assert panel.file_text.get_mapped() and '/library/model-27/q4_k_m.gguf' in panel.file_text.get_text()
    if width == 900:
        shot(win, 'selected-file-details')
    panel.file_details.set_expanded(False)
    assert not calls
    texts = [l.get_text() for l in _labels(panel.facts_box)]
    assert '256K tokens' in texts and 'Supported · On' in texts
    panel.fields['ctx'][0].set_text('65536')
    assert '64K tokens' in [l.get_text() for l in _labels(panel.facts_box)]
    assert '65,536' in panel.summary.get_text()
    assert panel.get_allocation().width <= width
    assert _box(panel.start, panel)[1] < 850
    assert panel.model_area.get_orientation() == (Gtk.Orientation.VERTICAL if width < 1000 else Gtk.Orientation.HORIZONTAL)
    shot(win, f'local-{width}')
    panel.fields['gpus'][0].set_text('default')
    panel.launch()
    assert calls[0]['selection']['gpus'] is None
    assert calls[0]['choice']['path'] == '/library/model-27/q4_k_m.gguf'
    assert calls[0]['selection']['ctx'] == 65536
    win.destroy()


def test_no_matches_empty_library_and_browse_preserve_explicit_path(tmp_path):
    Gtk, win, panel, settings, calls = screen(tmp_path, 900)
    panel.engines.popover.popup()
    drain(Gtk)
    panel.engines.search.set_text('no such model')
    panel.engines.filter_rows()
    assert panel.engines.empty.get_visible()
    before = panel.engines.get_active()
    from gi.repository import Gdk
    panel.engines.search_key(panel.engines.search, SimpleNamespace(keyval=Gdk.KEY_Return))
    assert panel.engines.get_active() == before
    panel.engines.popover.popdown()
    panel.catalog_loaded(dict(choices=[], council_choices=[], saved_council=None))
    assert not panel.start.get_sensitive()
    assert panel.message.get_text().startswith('No ready agent')
    shot(win, 'empty-library')
    choice = dict(id='browse', label='Chosen model', display_name='Chosen model', path='/chosen/location/model.gguf',
                  kind='local', provider='machx', ready=True, note='Explicit local file')
    panel.query = lambda *_: pytest.fail('Browse must reuse inspected metadata')
    panel.browsed_model(dict(choice=choice, settings=settings))
    assert panel.start.get_sensitive() and not calls
    panel.fields['ctx'][0].set_text('bad input')
    panel.launch()
    assert not calls and panel.message.get_text().startswith('Check the advanced settings')
    assert 'Enter a whole number for Context' in panel.message.get_text()
    assert 'invalid literal' not in panel.message.get_text()
    assert panel.advanced.get_expanded()
    assert win.get_focus() == panel.fields['ctx'][0]
    drain(Gtk)
    assert panel.message.get_mapped()
    shot(win, 'invalid-settings')
    win.destroy()


def test_async_catalog_failure_and_retry_are_visible_without_launch(tmp_path, monkeypatch):
    Gtk, win, panel, settings, calls = screen(tmp_path, 900)
    from dream.desktop.onboarding import Onboarding
    from dream.desktop import onboarding
    import json
    panel.query = Onboarding.query.__get__(panel)
    monkeypatch.setattr(onboarding.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=json.dumps({'error': 'Could not read model locations'}), stderr=''))
    panel.load_catalog()
    assert not panel.start.get_sensitive()
    for _ in range(5):
        drain(Gtk)
        if 'Could not read' in panel.message.get_text():
            break
    assert 'Refresh to retry' in panel.message.get_text()
    assert panel.message.get_mapped()
    assert panel.engines.get_sensitive() and not panel.start.get_sensitive() and not calls
    shot(win, 'catalog-error')
    monkeypatch.setattr(onboarding.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'choices': [], 'council_choices': []}), stderr=''))
    panel.load_catalog()
    drain(Gtk)
    assert panel.message.get_text().startswith('No ready agent') and not calls
    win.destroy()


@pytest.mark.parametrize('width,height', [(2559, 1343), (1920, 1080), (900, 900)])
@pytest.mark.parametrize('state', ['provider', 'loading', 'empty', 'local'])
def test_actual_dream_shell_load_screen(tmp_path, width, height, state):
    Gtk = _gtk()
    import sys
    from dream.desktop.window import DreamWindow
    win = DreamWindow(sys.executable, str(tmp_path), [], autostart=False)
    try:
        settings = settings_for(tmp_path, 'qwen27')
        settings['facts'] = dict(context_limit=262144, context_source='GGUF metadata', configured_context=32768,
                                 vision=True, speculative=dict(supported=True, enabled=True, draft='Native MTP', count=3))
        win.onboarding.query = lambda action, callback, *args: callback(settings)
        if state == 'local':
            win.onboarding.catalog_loaded(dict(choices=[dict(id='local', kind='local', provider='machx', ready=True,
                path='/library/Qwen3.8-27B/Q4_K_M.gguf', label='Qwen 3.8 27B', display_name='Qwen 3.8 27B',
                quantization='Q4_K_M', format='GGUF', size_gb=16.9, shard_count=1, vision=True,
                location='Expansion', note='Local files stay on this computer.')], council_choices=[]))
        if state == 'provider':
            win.onboarding.catalog_loaded(dict(choices=[dict(id='provider', kind='provider', provider='codex', ready=True,
                label='Codex', note='Installed client detected. Access checked on your first message.')] + [dict(id=f'local-{i}', kind='local', provider='machx', ready=True, label=f'Qwen 3.8 · Variant {i}', path=f'/library/qwen-{i}.gguf', format='GGUF', quantization='Q4_K_M', location='Expansion', note='Local model') for i in range(64)], council_choices=[]))
        elif state == 'empty':
            win.onboarding.catalog_loaded(dict(choices=[], council_choices=[]))
        elif state == 'loading':
            win.onboarding.catalog_loaded(dict(choices=[], council_choices=[]))
            win.onboarding.message.set_text('Finding installed agents and local models…')
            win.onboarding.spinner.start()
        for _ in range(3):
            # Broadway includes client-side decorations in the captured frame.
            # Match the owner's full screenshot dimensions, not the inner client area.
            extra_w = win.get_allocated_width() - win.get_size().width
            extra_h = win.get_allocated_height() - win.get_size().height
            win.resize(width - extra_w, height - extra_h)
            drain(Gtk)
        assert (win.get_allocated_width(), win.get_allocated_height()) == (width, height)
        assert win.onboarding.start.get_mapped()
        assert win.child_pid is None and not win.running
        panel = _box(win.onboarding, win)
        viewport = _box(win.welcome.get_parent(), win)
        assert panel[1] + panel[3] >= viewport[1] + viewport[3] - 30, (panel, viewport)
        assert not win.welcome_title.get_visible() and not win.welcome_description.get_visible()
        assert win.welcome.get_overlay_pass_through(win.welcome_art)
        assert not hasattr(win.onboarding, 'artwork')
        shot(win, f'dream-shell-{state}-{width}x{height}')
        if width >= 1920:
            from gi.repository import Gdk
            assert win._welcome_art_available
            x = panel[0] + panel[2] + 24
            pix = Gdk.pixbuf_get_from_window(win.get_window(), x, viewport[1], width - x - 25, viewport[3])
            pixels, stride, channels = pix.get_pixels(), pix.get_rowstride(), pix.get_n_channels()
            glowing = sum(pixels[y * stride + x * channels] > 180 and pixels[y * stride + x * channels + 1] > 90
                          and pixels[y * stride + x * channels + 2] > 35
                          for y in range(0, pix.get_height(), 3) for x in range(0, pix.get_width(), 3))
            assert glowing > 1000, 'Eclipse glow must be painted on the spare canvas'
        if state == 'provider' and width == 2559:
            win.onboarding.engines.clicked()
            drain(Gtk)
            assert 710 <= win.onboarding.engines.scroll.get_allocated_height() <= 722
            shot(win, 'dream-shell-library-2559x1343')
            win.onboarding.engines.popover.popdown()
        if width == 900:
            scroller = win.welcome.get_parent().get_parent()
            adjustment = scroller.get_vadjustment()
            button = _box(win.onboarding.start, win)
            adjustment.set_value(adjustment.get_value() + max(0, button[1] + button[3] - viewport[1] - viewport[3] + 20))
            drain(Gtk)
            button = _box(win.onboarding.start, win)
            assert viewport[1] <= button[1] and button[1] + button[3] <= viewport[1] + viewport[3], (button, viewport)
            shot(win, f'dream-shell-{state}-900-actions')
        if state == 'provider' and width == 2559:
            drain(Gtk)
            for target_width, target_height in ((900, 900), (2559, 1343)):
                for _ in range(2):
                    extra_w = win.get_allocated_width() - win.get_size().width
                    extra_h = win.get_allocated_height() - win.get_size().height
                    win.resize(target_width - extra_w, target_height - extra_h)
                    drain(Gtk)
                assert (win.get_allocated_width(), win.get_allocated_height()) == (target_width, target_height)
        win.welcome_title.set_text('Could not reconnect')
        win.welcome_description.set_text('Your session is still available. Retry when ready.')
        assert win.welcome_title.get_visible() and win.welcome_description.get_visible()
    finally:
        win.destroy()


def test_switching_to_provider_clears_pending_local_inspection(tmp_path, monkeypatch):
    Gtk, win, panel, settings, calls = screen(tmp_path, 900)
    from dream.desktop.onboarding import Onboarding
    from dream.desktop import onboarding
    import json
    import threading
    release = threading.Event()
    def delayed(*args, **kwargs):
        release.wait(3)
        return SimpleNamespace(returncode=0, stdout=json.dumps(settings), stderr='')
    monkeypatch.setattr(onboarding.subprocess, 'run', delayed)
    panel.query = Onboarding.query.__get__(panel)
    try:
        panel.engines.set_active(1)
        assert panel.spinner.get_property('active')
        drain(Gtk)
        assert panel.message.get_mapped() and panel.spinner.get_mapped()
        shot(win, 'loading-settings')
        panel.engines.set_active(0)
        assert not panel.spinner.get_property('active')
        assert panel.start.get_sensitive() and panel.settings is None
        release.set()
        drain(Gtk)
        assert panel.engines.get_active() == 0 and panel.settings is None
        assert panel.main_model.get_visible() and not panel.card.get_visible()
        assert panel.message.get_text() == '' and not calls
    finally:
        release.set()
        win.destroy()


@pytest.mark.parametrize('height', [1343, 650])
def test_model_popup_taller_but_clamped_and_last_row_reachable(tmp_path, height):
    Gtk, win, panel, settings, calls = screen(tmp_path, 1200)
    try:
        win.resize(1200, height)
        drain(Gtk)
        panel.engines.clicked()
        drain(Gtk)
        popup = _box(panel.engines.popover, win)
        assert popup[1] >= 0 and popup[1] + popup[3] <= win.get_allocated_height(), (popup, panel.engines.scroll.get_max_content_height(), panel.engines.scroll.get_allocated_height(), tuple(panel.engines.scroll.get_preferred_height()), tuple(panel.engines.popover.get_preferred_height()))
        list_height = panel.engines.scroll.get_allocated_height()
        assert 710 <= list_height <= 722 if height == 1343 else list_height < 500
        shot(win, f'popup-open-{height}')
        from gi.repository import Gdk
        panel.engines.search.set_text('Expansion')
        panel.engines.filter_rows()
        drain(Gtk)
        panel.engines.list.get_selected_row().grab_focus()
        assert Gtk.bindings_activate(panel.engines.list, Gdk.KEY_End, 0)
        last = panel.engines.list.get_children()[-1]
        assert panel.engines.list.get_selected_row() == last
        drain(Gtk)
        adjustment = panel.engines.scroll.get_vadjustment()
        assert adjustment.get_value() > 0
        assert last.get_mapped()
        shot(win, f'popup-last-{height}')
        if height == 1343:
            win.resize(1200, 650)
            drain(Gtk)
            popup = _box(panel.engines.popover, win)
            assert popup[1] >= 0 and popup[1] + popup[3] <= win.get_allocated_height(), (popup, panel.engines.scroll.get_max_content_height(), panel.engines.scroll.get_allocated_height(), tuple(panel.engines.scroll.get_preferred_height()), tuple(panel.engines.popover.get_preferred_height()))
            assert panel.engines.scroll.get_allocated_height() < 500
            assert panel.engines.popover.get_mapped()
            assert panel.engines.search.get_text() == 'Expansion'
            assert panel.engines.list.get_selected_row() == last
        assert Gtk.bindings_activate(panel.engines.list, Gdk.KEY_Home, 0)
        assert panel.engines.list.get_selected_row().choice_index == 1
        assert Gtk.bindings_activate(panel.engines.list, Gdk.KEY_End, 0)
        assert Gtk.bindings_activate(win, Gdk.KEY_Return, 0)
        assert panel.choices[panel.engines.get_active()]['path'] == '/library/model-64/q4_k_m.gguf'
        assert not calls
        drain(Gtk)
        panel.engines.clicked()
        drain(Gtk)
        assert panel.engines.popover.get_mapped()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Escape
        panel.engines.popover.emit('key-press-event', event)
        drain(Gtk)
        assert not panel.engines.popover.get_visible()
    finally:
        win.destroy()


def test_welcome_form_remains_usable_without_optional_cairo_bridge(tmp_path, monkeypatch):
    Gtk = _gtk()
    import sys
    import gi
    from dream.desktop.window import DreamWindow
    def unavailable(*args):
        raise ImportError('cairo bridge is not installed')
    monkeypatch.setattr(gi, 'require_foreign', unavailable)
    win = DreamWindow(sys.executable, str(tmp_path), [], autostart=False)
    try:
        drain(Gtk)
        assert not win._welcome_art_available
        assert win.onboarding.engines.get_mapped()
        assert win.child_pid is None and not win.running
    finally:
        win.destroy()


@pytest.mark.parametrize('width,height', [(1920, 1080), (900, 900)])
@pytest.mark.parametrize('cairo_available', [True, False])
def test_welcome_real_pointer_routes_through_artwork(tmp_path, width, height, cairo_available, monkeypatch, request):
    """Browser mouse events enter Broadway, then GTK's native window hit testing.

    Enable with DREAM_POINTER_TESTS=1; each case owns its display and GTK process.
    Unlike clicked()/widget event signals, this detects decorative input windows.
    """
    if not os.environ.get('DREAM_POINTER_TESTS'):
        pytest.skip('Set DREAM_POINTER_TESTS=1 to run isolated browser pointer tests')
    if not os.environ.get('_DREAM_POINTER_CHILD'):
        # A Broadway client changes the server's configure handling even after
        # disconnecting. Keep browser input tests off the rendering gate's display.
        import socket
        import subprocess
        import sys
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        import tempfile
        with tempfile.TemporaryDirectory(prefix='dream-pointer-runtime-') as runtime:
            env = dict(os.environ, XDG_RUNTIME_DIR=runtime, GDK_BACKEND='broadway',
                       BROADWAY_DISPLAY=':1', _DREAM_POINTER_CHILD='1',
                       DREAM_POINTER_BROADWAY_URL=f'http://127.0.0.1:{port}')
            server = subprocess.Popen(['broadwayd', '--address=127.0.0.1', f'--port={port}', ':1'],
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                import select
                # Linux Broadway uses an abstract Unix socket: its address is
                # namespaced by XDG_RUNTIME_DIR but has no filesystem entry.
                ready, _, _ = select.select([server.stdout], [], [], 5)
                startup = server.stdout.readline().decode() if ready else ''
                assert server.poll() is None and f'{runtime}/broadway2.socket' in startup, startup
                result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                                         f'{request.path}::{request.node.name}', '--tb=short'],
                                        env=env, capture_output=True, text=True, timeout=120)
                assert result.returncode == 0, result.stdout + result.stderr
            finally:
                server.terminate()
                server.wait(timeout=5)
        return
    url = os.environ['DREAM_POINTER_BROADWAY_URL']
    Gtk = _gtk()
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from playwright.sync_api import sync_playwright
    from dream.desktop.window import DreamWindow
    worker = ThreadPoolExecutor(max_workers=1)
    browser_state = {}

    def browser_start():
        driver = sync_playwright().start()
        browser = driver.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width + 60, 'height': height + 60})
        page.goto(url)
        browser_state.update(driver=driver, browser=browser, page=page)

    def run_browser(action):
        future = worker.submit(action)
        deadline = time.monotonic() + 20
        while not future.done() and time.monotonic() < deadline:
            drain(Gtk)
        result = future.result(timeout=1)
        drain(Gtk)
        return result

    win = None
    try:
        run_browser(browser_start)
        if not cairo_available:
            import gi
            def unavailable(*args):
                raise ImportError('cairo bridge is not installed')
            monkeypatch.setattr(gi, 'require_foreign', unavailable)
        win = DreamWindow(sys.executable, str(tmp_path), [], autostart=False)
        assert not win.welcome_art.get_has_window()
        assert win._welcome_art_available == cairo_available
        panel = win.onboarding
        catalog = dict(choices=[dict(id='provider', kind='provider', provider='codex', ready=True,
                                     label='Codex', note='Installed client fixture.')], council_choices=[])
        query_calls, browse_calls, launches = [], [], []
        panel.query = lambda action, callback, *args: (query_calls.append(action), callback(catalog))
        monkeypatch.setattr(win, 'launch_selection', launches.append)
        monkeypatch.setattr(panel, 'browse_model', lambda folder=False: browse_calls.append(folder))
        panel.catalog_loaded(catalog)
        win.resize(width, height)
        drain(Gtk)

        def click(widget):
            x, y, w, h = _box(widget, win)
            def pointer():
                page = browser_state['page']
                # Broadway exposes each native toplevel as a canvas. Use its
                # actual browser position, with GTK widget coordinates inside it.
                canvas = page.locator('canvas').first.bounding_box()
                page.mouse.click(canvas['x'] + x + w / 2, canvas['y'] + y + h / 2)
            run_browser(pointer)

        click(win.nav_buttons['home'])
        assert win.native_view == 'home', 'Control pointer must reach the sidebar'
        click(win.nav_buttons['chat'])
        assert win.native_view == 'chat'
        click(panel.engines)
        shot(win, f'pointer-chooser-{width}x{height}-cairo-{cairo_available}')
        assert panel.engines.popover.get_mapped(), 'Physical pointer must open the model chooser'
        run_browser(lambda: browser_state['page'].keyboard.type('Codex'))
        assert panel.engines.search.get_text() == 'Codex'
        click(panel.engines.list.get_children()[0])
        assert not panel.engines.popover.get_mapped()
        click(panel.engines)
        run_browser(lambda: browser_state['page'].keyboard.press('Escape'))
        click(panel.main_model)
        run_browser(lambda: browser_state['page'].keyboard.type('pointer-model'))
        assert panel.main_model.get_text() == 'pointer-model'
        click(panel.browse_file)
        click(panel.browse_folder)
        assert browse_calls == [False, True]
        click(panel.refresh)
        assert query_calls == ['catalog']
        # The sidebar is the owner's working control; the form must work too.
        click(win.nav_buttons['home'])
        assert win.native_view == 'home'
        click(win.nav_buttons['chat'])
        assert win.native_view == 'chat'
        click(panel.engines)
        assert panel.engines.popover.get_mapped()
        run_browser(lambda: browser_state['page'].keyboard.press('Escape'))
        assert not launches and win.child_pid is None and not win.running
        shot(win, f'pointer-working-{width}x{height}-cairo-{cairo_available}')
        if width == 1920:
            for resized_width in (900, 1920):
                win.resize(resized_width, height)
                drain(Gtk)
                assert win.get_size().width == resized_width, 'Actual client width must follow resize'
                click(panel.engines)
                assert panel.engines.popover.get_mapped()
                run_browser(lambda: browser_state['page'].keyboard.press('Escape'))
                click(panel.main_model)
                run_browser(lambda: browser_state['page'].keyboard.press('Control+a'))
                run_browser(lambda: browser_state['page'].keyboard.type(str(resized_width)))
                assert panel.main_model.get_text() == str(resized_width)
        if width == 1920 and cairo_available:
            import copy
            import json
            from dream.desktop import onboarding
            payload = settings_for(tmp_path, 'qwenflash')
            payload['selection'] = copy.deepcopy(payload['selection'])
            payload['selection'].update(ctx=262000, gpus=2)
            payload['selection']['options']['reasoning_effort'] = 'medium'
            payload['sources']['ctx'] = 'Saved for this model'
            payload['facts'] = dict(context_limit=262144, configured_context=262000,
                                    context_source='GGUF header', speculative=dict(supported=True, enabled=True, count=3))
            local = dict(id='qwenflash', kind='local', provider='machx', ready=True,
                         path='/fixture/Qwen3.8-Flash-Next-UD-Q4_K_XL.gguf',
                         label='Qwen 3.8 Flash Next', note='Saved model fixture')
            catalog['choices'].append(local)
            panel.query = onboarding.Onboarding.query.__get__(panel)
            monkeypatch.setattr(onboarding.subprocess, 'run', lambda *a, **k: SimpleNamespace(
                returncode=0, stderr='', stdout=json.dumps(payload)))
            panel.catalog_loaded(catalog)
            click(panel.engines)
            click(panel.engines.list.get_children()[1])
            drain(Gtk)
            assert panel.card.get_mapped() and panel.advanced.get_mapped()
            assert panel.start.get_sensitive() and panel.start.get_label() == 'Load model and start'
            assert panel.message.get_text() == ''
            shot(win, 'local-ready-after-pointer-selection')
            click(panel.start)
            assert len(launches) == 1
            assert launches[0]['choice']['path'] == local['path']
            assert launches[0]['selection']['ctx'] == 262000
            assert launches[0]['selection']['gpus'] == 2
            assert launches[0]['selection']['options']['reasoning_effort'] == 'medium'
            assert win.child_pid is None and not win.running
    finally:
        if win is not None:
            win.destroy()
        def close_browser():
            if browser_state:
                browser_state['browser'].close()
                browser_state['driver'].stop()
        run_browser(close_browser)
        worker.shutdown(wait=True)


@pytest.mark.parametrize('via_browse', [False, True])
def test_async_settings_render_failure_is_visible_and_refresh_recovers(tmp_path, monkeypatch, via_browse):
    Gtk, win, panel, settings, calls = screen(tmp_path, 1920)
    from dream.desktop import onboarding
    import json
    catalog = dict(choices=panel.choices, council_choices=[])
    panel.query = onboarding.Onboarding.query.__get__(panel)
    monkeypatch.setattr(onboarding.subprocess, 'run', lambda command, **kw: SimpleNamespace(
        returncode=0, stderr='', stdout=json.dumps(catalog if command[3] == 'catalog' else settings)))
    fill_mode = panel.fill_mode
    def broken_render():
        raise RuntimeError('fixture renderer failed')
    monkeypatch.setattr(panel, 'fill_mode', broken_render)
    try:
        if via_browse:
            panel.browsed_model(dict(choice=panel.choices[1], settings=settings))
        else:
            panel.engines.set_active(1)
        drain(Gtk)
        assert 'Refresh to retry' in panel.message.get_text()
        assert not panel.spinner.get_property('active')
        assert not panel.start.get_sensitive() and panel.settings is None
        assert not panel.card.get_visible() and not panel.advanced.get_visible()
        assert not calls
        shot(win, 'settings-render-error')
        monkeypatch.setattr(panel, 'fill_mode', fill_mode)
        panel.load_catalog()
        drain(Gtk)
        assert panel.message.get_text() == ''
        assert panel.start.get_sensitive() and panel.start.get_label() == 'Load model and start'
        assert panel.card.get_mapped() and panel.advanced.get_mapped()
        assert not calls
    finally:
        win.destroy()


def test_local_settings_render_in_clean_system_gtk_runtime(tmp_path):
    """Production uses system GTK Python, with app dependencies only in its query child."""
    import copy
    import json
    import subprocess
    payload = settings_for(tmp_path, 'qwenflash')
    payload['selection'] = copy.deepcopy(payload['selection'])
    payload['selection'].update(ctx=262000, gpus=2)
    payload['selection']['options']['reasoning_effort'] = 'medium'
    payload['sources']['ctx'] = 'Saved for this model'
    payload['facts'] = dict(context_limit=262144, context_source='GGUF header', configured_context=262000,
                            vision=True, speculative=dict(supported=True, enabled=True, count=3, draft=None))
    fixture = tmp_path / 'settings.json'
    fixture.write_text(json.dumps(payload))
    script = r'''
import json, sys, time
from pathlib import Path
from types import SimpleNamespace
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from dream.desktop import onboarding
settings = json.loads(Path(sys.argv[1]).read_text())
errors = []
sys.excepthook = lambda *error: (errors.append(str(error[1])), sys.__excepthook__(*error))   # kept, and printed
owner = SimpleNamespace(cwd=str(Path(sys.argv[1]).parent), python=sys.executable, stopped=False,
                        running=False, child_env=dict, open_terminal=lambda: None, launch_selection=lambda r: None)
win = Gtk.Window()
win.set_default_size(1920, 1080)
panel = onboarding.Onboarding(owner)
win.add(panel)
win.show_all()
choices = [dict(id='provider', kind='provider', provider='codex', ready=True, label='Codex', note='Fixture')]
choices += [dict(id=f'local-{i}', kind='local', provider='machx', ready=True,
                 path=f'/fixture/Qwen3.8-Flash-Next-{i}.gguf', label='Qwen 3.8 Flash Next', note='Fixture') for i in range(2)]
onboarding.subprocess.run = lambda *a, **k: SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(settings))
panel.catalog_loaded(dict(choices=choices, council_choices=[]))
try:
    for index in (1, 2, 0, 1):
        panel.engines.set_active(index)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            if index == 0 or panel.start.get_sensitive():
                break
            time.sleep(.01)
        if index:
            assert not errors, errors
            assert panel.start.get_sensitive(), panel.message.get_text()
            assert panel.start.get_label() == 'Load model and start'
            assert panel.card.get_mapped() and panel.advanced.get_mapped()
            assert panel.fields['ctx'][0].get_text() == '262000'
            assert panel.fields['reasoning_effort'][0].get_active_text() == 'medium'
            assert panel.message.get_text() == ''
    assert 'dream.local.machx' not in sys.modules
    assert 'httpx' not in sys.modules
finally:
    win.destroy()
'''
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    # The same prerequisite as the other GTK cases (test_load_screen_card._gtk), checked in the system Python.
    probe = "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk; raise SystemExit(not Gtk.init_check([])[0])"
    if not Path('/usr/bin/python3').exists() or subprocess.run(['/usr/bin/python3', '-c', probe], env=env,
                                                               capture_output=True, timeout=20).returncode:
        pytest.skip('System GTK 3 Python and a display are required (run-sys.sh with GDK_BACKEND=broadway draws one offscreen)')
    result = subprocess.run(['/usr/bin/python3', '-c', script, str(fixture)], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
