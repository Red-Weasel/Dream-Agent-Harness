"""Dream native chat workspace with Terminal and independent Browser tabs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Vte', '2.91')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte, WebKit2

from .browser import Browser, button, label, webview
from .protocol import session_address, session_status
from .onboarding import Onboarding

HERE = Path(__file__).resolve().parent
WEB_VIEWS = frozenset(('home', 'chat', 'studio', 'projects', 'optimizer', 'skills', 'memory', 'settings'))


def color(value: str) -> Gdk.RGBA:
    result = Gdk.RGBA()
    result.parse(value)
    return result


class DreamWindow(Gtk.Window):
    def __init__(self, python: str, cwd: str, cli_args: list[str], *, autostart: bool = True):
        super().__init__(title='Dream — Workspace')
        self.python, self.cwd, self.cli_args = python, cwd, cli_args
        self.child_pid: int | None = None
        self.running = False
        self.closing = False
        self.polling = False
        self.stopped = False
        self.address_info = None
        self.connected = False
        self.session_id = ''
        self.native_view = 'chat'
        self.reading_web_view = False
        self.studio_failed = False
        self.last_connect = 0.0
        self.show_sequence = 0
        self.zoom = 1.0
        self.fullscreen_on = False
        self.private = tempfile.TemporaryDirectory(prefix='dream-desktop-')
        self.discovery = Path(self.private.name) / 'session.json'
        self.launch_status = Path(self.private.name) / 'startup-status.json'
        self.launch_request = None
        self.set_default_size(1600, 1000)
        self.set_size_request(720, 520)
        branding = HERE.parent / 'gui' / 'static'
        self.set_icon_from_file(str(branding / 'dream-app-icon.png'))
        self.set_wmclass('dream-desktop', 'Dream')
        provider = Gtk.CssProvider()
        provider.load_from_path(str(HERE / 'style.css'))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Gtk.HeaderBar(show_close_button=True)
        brand = Gtk.Box(spacing=11)
        icon = Gtk.Image.new_from_file(str(branding / 'dream-mark.svg'))
        icon.set_pixel_size(46)
        # SVG raster size is controlled by the pixbuf so the header stays compact.
        from gi.repository import GdkPixbuf
        icon.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(branding / 'dream-mark.svg'), 46, 46, True))
        brand.pack_start(icon, False, False, 0)
        wordmark = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        wordmark.pack_start(label('DREAM', 'brand'), False, False, 0)
        wordmark.pack_start(label('BUILD · EXPLORE · ALIGN', 'brand-tagline'), False, False, 0)
        brand.pack_start(wordmark, False, False, 0)
        header.pack_start(brand)
        self.session_label = label('Choose an engine', 'pill')
        self.session_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.session_label.set_max_width_chars(28)
        header.set_custom_title(self.session_label)
        header.pack_end(button('view-fullscreen-symbolic', 'Fullscreen (F11)', lambda _: self.toggle_fullscreen()))
        header.pack_end(button('help-about-symbolic', 'Keyboard shortcuts', self.shortcuts))
        self.set_titlebar(header)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        terminalbar = Gtk.Box(spacing=8)
        terminalbar.get_style_context().add_class('toolbar')
        terminalbar.get_style_context().add_class('terminalbar')
        terminalbar.pack_start(label('Terminal'), False, False, 0)
        terminalbar.pack_start(label('Your familiar Dream CLI', 'muted'), True, True, 4)
        terminalbar.pack_end(button('edit-find-symbolic', 'Find in terminal (Ctrl+Shift+F)', lambda _: self.find_terminal()), False, False, 0)
        terminalbar.pack_end(button('edit-copy-symbolic', 'Copy selection (Ctrl+Shift+C)', lambda _: self.terminal.copy_clipboard_format(Vte.Format.TEXT)), False, False, 0)
        self.restart = button('view-refresh-symbolic', 'Start another Dream session', lambda _: self.new_session())
        self.restart.set_sensitive(False)
        terminalbar.pack_end(self.restart, False, False, 0)
        left.pack_start(terminalbar, False, False, 0)
        self.searchbar = Gtk.SearchBar()
        searchbox = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text='Find in terminal history')
        self.search.connect('search-changed', self._terminal_search)
        self.search.connect('activate', lambda _: self.terminal.search_find_next())
        searchbox.pack_start(self.search, True, True, 0)
        searchbox.pack_start(button('go-up-symbolic', 'Previous terminal match', lambda _: self.terminal.search_find_previous()), False, False, 0)
        searchbox.pack_start(button('go-down-symbolic', 'Next terminal match', lambda _: self.terminal.search_find_next()), False, False, 0)
        self.searchbar.add(searchbox)
        self.searchbar.connect_entry(self.search)
        self.searchbar.set_show_close_button(True)
        left.pack_start(self.searchbar, False, False, 0)
        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription('DejaVu Sans Mono 11'))
        self.terminal.set_colors(color('#EBF0FA'), color('#070B16'), [color(x) for x in (
            '#14262e', '#ff877e', '#87d9a4', '#e9c983', '#84b9f2', '#b99aff', '#5ee7f2', '#d4e4eb',
            '#6a8291', '#ffaaa3', '#b0efbd', '#ffe0a1', '#a8d0ff', '#d2b9ff', '#8ef3fa', '#ffffff')])
        self.terminal.set_color_cursor(color('#AA94FF'))
        self.terminal.set_scrollback_lines(20000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        self.terminal.connect('child-exited', self._child_exited)
        terminalbox = Gtk.Box()
        terminalbox.pack_start(self.terminal, True, True, 8)
        terminalbox.pack_end(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.terminal.get_vadjustment()), False, False, 0)
        left.pack_start(terminalbox, True, True, 8)
        self.terminal_panel = left

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.workspace = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.workspace.connect('notify::visible-child-name', self._workspace_changed)
        top = Gtk.Box(spacing=8)
        top.get_style_context().add_class('toolbar')
        self.workspace_name = Gtk.Button(label=Path(cwd).name or cwd)
        self.workspace_name.get_style_context().add_class('flat')
        self.workspace_name.get_child().set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.workspace_name.get_child().set_max_width_chars(28)
        self.workspace_name.set_tooltip_text('Show the full workspace path')
        self.workspace_name.connect('clicked', self._show_workspace_path)
        self.workspace_path = cwd
        top.pack_start(self.workspace_name, False, False, 0)
        self.view_label = label('Chat', 'muted')
        top.pack_start(self.view_label, True, True, 0)
        top.pack_end(button('document-new-symbolic', 'New conversation', lambda _: self.new_session()), False, False, 0)
        right.pack_start(top, False, False, 0)

        self.studio_stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.studio = webview(local_only=True)
        self.studio.connect('decide-policy', self._studio_policy)
        self.studio.connect('create', self._studio_popup)
        self.studio.connect('load-failed', self._studio_failed)
        self.studio.connect('load-changed', self._studio_loaded)
        self.studio.connect('web-process-terminated', self._studio_terminated)
        self.studio_stack.add_named(self.studio, 'web')
        self.welcome = self._welcome()
        welcome_scroll = Gtk.ScrolledWindow()
        welcome_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        welcome_scroll.add(self.welcome)
        self.studio_stack.add_named(welcome_scroll, 'welcome')
        self.workspace.add_titled(self.studio_stack, 'studio', 'Chat')
        self.workspace.add_titled(self.terminal_panel, 'terminal', 'Terminal')
        self.browser = Browser(self.status)
        self.workspace.add_titled(self.browser, 'browser', 'Browser')
        # Studio's authenticated downloads use the same native Save dialog.
        self.studio.get_context().connect('download-started', self.browser._download)
        right.pack_start(self.workspace, True, True, 0)
        body = Gtk.Box()
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.sidebar.get_style_context().add_class('sidebar')
        self.nav_buttons = {}
        self.nav_labels = []
        for view, title, icon_name in (
                ('home', 'Home', 'go-home-symbolic'),
                ('chat', 'Chat', 'user-available-symbolic'),
                ('optimizer', 'Prompt Optimizer', 'document-edit-symbolic'),
                ('studio', 'Studio', 'applications-graphics-symbolic'),
                ('projects', 'Projects', 'folder-symbolic'),
                ('skills', 'Skills', 'applications-system-symbolic'),
                ('memory', 'Memory', 'document-open-recent-symbolic'),
                ('terminal', 'Terminal', 'utilities-terminal-symbolic'),
                ('browser', 'Browser', 'web-browser-symbolic'),
                ('settings', 'Settings', 'emblem-system-symbolic')):
            item = Gtk.Button()
            item.get_style_context().add_class('nav-item')
            item.set_tooltip_text(title)
            item.get_accessible().set_name(title)
            row = Gtk.Box(spacing=12)
            row.pack_start(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON), False, False, 0)
            caption = label(title)
            caption.set_xalign(0)
            row.pack_start(caption, True, True, 0)
            self.nav_labels.append(caption)
            item.add(row)
            item.connect('clicked', lambda _, target=view: self.navigate(target))
            self.sidebar.pack_start(item, False, False, 0)
            self.nav_buttons[view] = item
        body.pack_start(self.sidebar, False, False, 0)
        body.pack_start(right, True, True, 0)
        root.pack_start(body, True, True, 0)
        self.connect('size-allocate', self._resize_navigation)

        footer = Gtk.Box(spacing=14)
        footer.get_style_context().add_class('statusbar')
        self.folder = label(Path(cwd).name, 'muted')
        self.folder.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.folder.set_max_width_chars(35)
        self.folder.set_tooltip_text(cwd)
        footer.pack_start(self.folder, False, False, 0)
        self.status_label = label('Choose an agent and workspace to begin.', 'muted')
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        footer.pack_start(self.status_label, True, True, 0)
        footer.pack_end(label('F11  Focus', 'muted'), False, False, 0)
        root.pack_end(footer, False, False, 0)
        self.connect('key-press-event', self._keys)
        self.connect('delete-event', self._close)
        self.connect('destroy', self._destroy)
        self.show_all()
        self.onboarding.advanced.hide()
        self.onboarding.reset.hide()
        self.studio_stack.set_visible_child_name('welcome')
        self.workspace.set_visible_child_name('studio')
        self._highlight_navigation('chat')
        self.timer = GLib.timeout_add(700, self._poll)
        if autostart:
            GLib.idle_add(self.start_terminal if self.cli_args else self.onboarding.load_catalog)

    def _resize_navigation(self, _widget, allocation):
        compact = allocation.width < 1000
        for caption in self.nav_labels:
            caption.set_visible(not compact)

    def _highlight_navigation(self, view):
        if not hasattr(self, 'nav_buttons'):
            return
        for name, item in self.nav_buttons.items():
            context = item.get_style_context()
            context.add_class('selected') if name == view else context.remove_class('selected')
        self.view_label.set_text('Prompt Optimizer' if view == 'optimizer' else view.title())

    def _workspace_changed(self, *_):
        name = self.workspace.get_visible_child_name()
        self._highlight_navigation(self.native_view if name == 'studio' else name or 'chat')

    def _show_workspace_path(self, _button):
        popover = Gtk.Popover.new(self.workspace_name)
        path = label(self.workspace_path, 'description')
        path.set_selectable(True)
        path.set_line_wrap(True)
        path.set_max_width_chars(50)
        path.set_margin_start(16)
        path.set_margin_end(16)
        path.set_margin_top(12)
        path.set_margin_bottom(12)
        popover.add(path)
        popover.show_all()
        popover.popup()

    def navigate(self, view):
        """Select a known surface without launching a task or changing its workspace."""
        if view not in WEB_VIEWS and view not in ('terminal', 'browser'):
            return False
        if view in ('terminal', 'browser'):
            self.workspace.set_visible_child_name(view)
            (self.terminal if view == 'terminal' else self.browser.view).grab_focus()
        else:
            self.native_view = view
            self.workspace.set_visible_child_name('studio')
            self._highlight_navigation(view)
            self._dispatch_view()
        return True

    def _trusted_studio_document(self):
        if not self.address_info or not self.connected:
            return False
        base, token = self.address_info
        uri = urlsplit(self.studio.get_uri() or '')
        trusted = urlsplit(base)
        return (uri.scheme == trusted.scheme and uri.netloc == trusted.netloc
                and uri.path == '/' and parse_qs(uri.query).get('token') == [token])

    def _dispatch_view(self):
        if not self._trusted_studio_document() or self.native_view not in WEB_VIEWS:
            return False
        payload = json.dumps({'view': self.native_view, 'session_id': self.session_id})
        self.studio.run_javascript(
            "document.documentElement.classList.add('dream-native');"
            "document.documentElement.dataset.nativeSession = " + json.dumps(self.session_id) + ";"
            "window.dispatchEvent(new CustomEvent('dream:navigate', {detail:" + payload + "}));",
            None, None, None)
        return True

    def _sync_web_view(self):
        if (self.reading_web_view or self.workspace.get_visible_child_name() != 'studio'
                or not self._trusted_studio_document()):
            return
        self.reading_web_view = True
        self.studio.run_javascript("document.documentElement.dataset.dreamView || ''",
                                   None, self._web_view_read, (self.address_info, self.native_view))

    def _web_view_read(self, view, result, _data):
        self.reading_web_view = False
        try:
            name = view.run_javascript_finish(result).get_js_value().to_string()
        except GLib.Error:
            # A page replaced during a reconnect has no selection to synchronize.
            return
        if (_data == (self.address_info, self.native_view)
                and self._trusted_studio_document() and name in WEB_VIEWS):
            self.native_view = name
            if self.workspace.get_visible_child_name() == 'studio':
                self._highlight_navigation(name)

    def _studio_loaded(self, _view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self._dispatch_view()

    def _welcome(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.onboarding = Onboarding(self)
        box.pack_start(self.onboarding, True, True, 0)
        self.welcome_title = label('', 'headline')
        self.welcome_description = label('', 'description')
        self.welcome_description.set_line_wrap(True)
        box.pack_start(self.welcome_title, False, False, 0)
        box.pack_start(self.welcome_description, False, False, 0)
        self.retry_studio = Gtk.Button(label='Reconnect conversation')
        self.retry_studio.connect('clicked', lambda _: self._connect_studio(force=True))
        self.retry_studio.set_sensitive(False)
        self.retry_studio.set_no_show_all(True)
        self.retry_studio.set_halign(Gtk.Align.CENTER)
        box.pack_start(self.retry_studio, False, False, 0)
        return box

    def child_env(self):
        env = dict(os.environ)
        env.update(DREAM_GUI='1', DREAM_GUI_OPEN='0', DREAM_DESKTOP_SESSION_FILE=str(self.discovery),
                   TERM='xterm-256color', COLORTERM='truecolor', PYTHONUNBUFFERED='1')
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(HERE.parents[1]), env.get('PYTHONPATH')]))
        return env

    def open_terminal(self):
        self.workspace.set_visible_child_name('terminal')
        if not self.running:
            self.launch_request = None
            self.cli_args = []
            self.start_terminal()
        self.terminal.grab_focus()

    def new_session(self):
        if self.running:
            self.status('Finish this session with /quit in Chat or Terminal, then start another.')
            return
        self.launch_request = None
        self.cli_args = []
        self.native_view = 'chat'
        self.workspace.set_visible_child_name('studio')
        self._highlight_navigation('chat')
        self.studio_stack.set_visible_child_name('welcome')
        self.welcome_title.set_text('')
        self.welcome_description.set_text('')
        self.onboarding.set_running(False)
        self.onboarding.load_catalog()

    def launch_selection(self, request):
        self.launch_request = Path(self.private.name) / 'startup-request.json'
        self.launch_request.write_text(json.dumps(request))
        self.launch_request.chmod(0o600)
        self.launch_status.unlink(missing_ok=True)
        self.onboarding.message.set_text('Starting your session…')
        self.onboarding.set_running(True)
        self.start_terminal()

    def status(self, text: str):
        if hasattr(self, 'status_label') and not self.stopped:
            self.status_label.set_text(text)
            self.status_label.set_tooltip_text(text)

    def start_terminal(self):
        if self.running or self.stopped:
            return False
        self.discovery.unlink(missing_ok=True)
        self.address_info = None
        self.connected = False
        self.studio_failed = False
        self.show_sequence = 0
        self.closing = False
        self.running = True
        self.restart.set_sensitive(False)
        self.session_label.set_text('Choose an engine')
        self.session_label.get_style_context().remove_class('connected')
        self.studio.load_uri('about:blank')
        self.studio_stack.set_visible_child_name('welcome')
        self.welcome_title.set_text('')
        self.welcome_description.set_text('Starting your conversation…' if self.launch_request else 'Choose your engine in the Terminal tab.')
        env = self.child_env()
        command = ([self.python, '-m', 'dream.desktop.startup', 'run', str(self.launch_request), str(self.launch_status)]
                   if self.launch_request else [self.python, '-m', 'dream', *self.cli_args])
        if not self.launch_request:
            self.workspace.set_visible_child_name('terminal')
        try:
            self.terminal.spawn_async(Vte.PtyFlags.DEFAULT, self.cwd, command,
                                     [f'{k}={v}' for k, v in env.items()], GLib.SpawnFlags.DEFAULT,
                                     None, None, -1, None, self._spawned, None)
        except (GLib.Error, TypeError, OSError) as exc:
            self.running = False
            self.restart.set_sensitive(True)
            self.session_label.set_text('Launch failed')
            self.onboarding.set_running(False)
            self.onboarding.message.set_text(f'Dream could not start: {exc}. Try again.')
            self.status(f'Dream could not start: {exc}. Use the restart button to retry.')
        if not self.launch_request:
            self.terminal.grab_focus()
        return False

    def _spawned(self, _term, pid, error, _data):
        if error:
            self.running = False
            self.restart.set_sensitive(True)
            self.session_label.set_text('Launch failed')
            self.onboarding.set_running(False)
            self.onboarding.message.set_text(f'Dream could not start: {error.message}')
            self.status(f'Dream could not start: {error.message}')
        else:
            self.child_pid = pid
            self.status('Starting your conversation…' if self.launch_request else 'Choose your engine in the Terminal tab.')

    def _child_exited(self, _term, wait_status):
        self.running = False
        self.child_pid = None
        self.connected = False
        self.restart.set_sensitive(True)
        self.session_label.set_text('Session ended' if wait_status == 0 else 'Session stopped')
        self.session_label.get_style_context().remove_class('connected')
        self.onboarding.set_running(False)
        self._read_launch_status()
        self.studio_stack.set_visible_child_name('welcome')
        self.workspace.set_visible_child_name('studio')
        self.welcome_description.set_text('Your terminal history is preserved. Start another conversation when ready.')
        self.status('Session ended. Choose an agent to start again; Terminal history is preserved.')
        if self.closing:
            self.destroy()

    def _read_launch_status(self):
        if not self.launch_request or not self.launch_status.exists():
            return
        try:
            data = json.loads(self.launch_status.read_text())
            message = data['message']
            if data['state'] == 'loading' and data.get('updated_at'):
                elapsed = max(0, int(time.time() - data['updated_at']))
                message += f'\nElapsed: {elapsed // 60}m {elapsed % 60}s. You can open Terminal while loading.'
            self.onboarding.message.set_text(message)
            if data['state'] == 'error':
                self.session_label.set_text('Could not start')
        except (OSError, ValueError, KeyError):
            pass  # Atomic status may not exist before the child starts.

    def _poll(self):
        if self.stopped:
            return False
        if self.connected:
            self._sync_web_view()
        if self.running and not self.connected:
            self._read_launch_status()
        if self.polling or not self.running:
            return True
        self.polling = True
        pid = self.child_pid
        def work():
            address = session_address(self.discovery, pid)
            try:
                state = session_status(*address) if address else None
                error = None
            except Exception as exc:
                state, error = None, type(exc).__name__
            GLib.idle_add(self._polled, pid, address, state, error)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _polled(self, pid, address, state, error):
        self.polling = False
        if self.stopped or pid != self.child_pid or not self.running:
            return False
        if state is not None:
            changed = self.address_info != address
            self.address_info = address
            self.connected = True
            self.retry_studio.set_sensitive(True)
            self.retry_studio.show()
            meta = state.get('session', state)
            self.session_id = str(meta.get('session_id') or '')
            provider = str(meta.get('provider') or 'Dream')
            model = str(meta.get('model') or '')
            self.session_label.set_text(provider + ('  /  ' + model[:36] if model else ''))
            self.session_label.set_tooltip_text(provider + ' ' + model)
            self.session_label.get_style_context().add_class('connected')
            if meta.get('workspace'):
                self.workspace_path = str(meta['workspace'])
                self.workspace_name.set_label(Path(meta['workspace']).name or str(meta['workspace']))
                self.folder.set_text(Path(meta['workspace']).name)
                self.folder.set_tooltip_text(meta['workspace'])
            if changed or (self.studio_failed and time.monotonic() - self.last_connect > 4):
                self._connect_studio()
                self.workspace.set_visible_child_name('studio')
                self.onboarding.spinner.stop()
                self.status('Conversation ready. Terminal and Browser are available in the sidebar.')
            sequence = state.get('show_sequence', 0)
            if isinstance(sequence, int) and sequence > self.show_sequence:
                self.show_sequence = sequence
                self.navigate('studio')
                self.status('Dream opened an artifact in Studio.')
        elif (error or not address) and self.connected:
            self.connected = False
            self.session_label.set_text('Studio reconnecting')
            self.session_label.get_style_context().remove_class('connected')
            self.status('Studio disconnected. Retrying automatically; your terminal remains available.')
        return False

    def _connect_studio(self, force=False):
        if not self.address_info:
            self.status('Studio connects after you select an engine and workspace.')
            return
        base, token = self.address_info
        self.last_connect = time.monotonic()
        self.studio_failed = False
        self.studio_stack.set_visible_child_name('web')
        self.studio.load_uri(f'{base}/?token={quote(token)}&companion=1')

    def _studio_policy(self, _view, decision, kind):
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri()
            parsed = urlsplit(uri)
            # about:blank/srcdoc are used by the existing sandboxed artifact iframe.
            if parsed.scheme == 'about':
                return False
            trusted = self.address_info and uri.startswith(self.address_info[0] + '/')
            if not trusted:
                decision.ignore()
                if action.is_user_gesture() and parsed.scheme in ('http', 'https'):
                    self.workspace.set_visible_child_name('browser')
                    self.browser.navigate(uri)
                return True
        return False

    def _studio_popup(self, _view, action):
        uri = action.get_request().get_uri()
        if self.address_info and uri.startswith(self.address_info[0] + '/'):
            if urlsplit(uri).path == '/api/download' or urlsplit(uri).path.startswith('/api/media/assets/'):
                self.studio.download_uri(uri)
            # Never transfer an authenticated local URL into general browsing.
            else:
                self.status('This link belongs to Studio. Use its workspace controls to open it.')
        elif action.is_user_gesture():
            self.workspace.set_visible_child_name('browser')
            self.browser.navigate(uri)
        return None

    def _studio_failed(self, _view, _event, _uri, error):
        if error.matches(WebKit2.NetworkError.quark(), WebKit2.NetworkError.CANCELLED):
            return True
        self.studio_failed = True
        self.welcome_title.set_text('Let’s reconnect your workspace.')
        self.welcome_description.set_text('Studio could not load. The terminal is still available.\nUse Reconnect Studio to try again.')
        self.studio_stack.set_visible_child_name('welcome')
        self.status('Studio could not load. Reconnect to try again.')
        return True

    def _studio_terminated(self, _view, _reason):
        self.studio_failed = True
        self.welcome_title.set_text('Your terminal is still here.')
        self.welcome_description.set_text('The Studio browser stopped. Reconnect to restore the preview.')
        self.studio_stack.set_visible_child_name('welcome')
        self.status('Studio browser stopped — use Reconnect Studio.')

    def focus_browser(self):
        self.workspace.set_visible_child_name('browser')
        self.browser.address.grab_focus()
        self.browser.address.select_region(0, -1)

    def find_terminal(self):
        self.workspace.set_visible_child_name('terminal')
        self.searchbar.set_search_mode(True)
        self.search.grab_focus()

    def _terminal_search(self, entry):
        text = entry.get_text()
        try:
            # PCRE2_UTF | PCRE2_CASELESS | PCRE2_MULTILINE.
            regex = Vte.Regex.new_for_search(re.escape(text), -1, 0x40000408) if text else None
            self.terminal.search_set_regex(regex, 0)
            self.terminal.search_set_wrap_around(True)
            if text:
                self.terminal.search_find_previous()
        except GLib.Error as exc:
            self.status(f'Search could not run: {exc.message}')

    def toggle_fullscreen(self):
        self.fullscreen_on = not self.fullscreen_on
        self.fullscreen() if self.fullscreen_on else self.unfullscreen()

    def _keys(self, _widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)
        key = Gdk.keyval_name(event.keyval).lower()
        if key == 'f11':
            self.toggle_fullscreen()
        elif ctrl and shift and key == 'c' and self.terminal.has_focus():
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)
        elif ctrl and shift and key == 'v' and self.terminal.has_focus():
            self.terminal.paste_clipboard()
        elif ctrl and shift and key == 'f':
            self.find_terminal()
        elif ctrl and key == 'l':
            self.focus_browser()
        elif ctrl and key in ('1', '2', '3'):
            if key == '1':
                self.workspace.set_visible_child_name('terminal')
                self.terminal.grab_focus()
            else:
                self.workspace.set_visible_child_name('studio' if key == '2' else 'browser')
                (self.studio if key == '2' else self.browser.view).grab_focus()
        elif ctrl and key in ('plus', 'equal', 'minus', '0'):
            self.zoom = 1.0 if key == '0' else min(1.8, max(0.65, self.zoom + (-0.1 if key == 'minus' else 0.1)))
            self.terminal.set_font_scale(self.zoom)
        elif ctrl and key == 'f' and self.workspace.get_visible_child_name() == 'browser' and not self.terminal.has_focus():
            self.browser.find_page()
        elif ctrl and key == 'r' and not self.terminal.has_focus():
            self.browser.reload() if self.workspace.get_visible_child_name() == 'browser' else self._connect_studio(force=True)
        elif alt and key in ('left', 'right') and self.workspace.get_visible_child_name() == 'browser':
            self.browser.view.go_back() if key == 'left' else self.browser.view.go_forward()
        else:
            return False
        return True

    def shortcuts(self, _button):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.CLOSE, text='Make yourself at home.')
        dialog.format_secondary_text('Ctrl+1  Focus terminal\nCtrl+2  Focus Studio\nCtrl+3  Focus browser\n'
                                     'Ctrl+L  Enter a browser address\nCtrl+Shift+C / V  Copy / paste terminal\n'
                                     'Ctrl+Shift+F  Find in terminal\nCtrl+F  Find in browser\n'
                                     'Ctrl+Plus / Minus / 0  Terminal text size\nF11  Fullscreen\n\n'
                                     'Ctrl+C still interrupts Dream. Ctrl+D or /quit finishes the session.')
        dialog.run()
        dialog.destroy()

    def _close(self, *_):
        if not self.running:
            return False
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text='Finish this Dream session?')
        dialog.format_secondary_text('Dream will finish its current turn and save the session before this window closes. Your browser stays open until it is done.')
        dialog.add_buttons('Keep working', Gtk.ResponseType.CANCEL, 'Finish and close', Gtk.ResponseType.ACCEPT)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            self.closing = True
            if self.address_info:
                base, token = self.address_info
                def finish():
                    try:
                        request = Request(base + '/api/prompt', data=json.dumps({'prompt': '/quit'}).encode(),
                                          headers={'x-dream-token': token, 'Content-Type': 'application/json'})
                        with build_opener(ProxyHandler({})).open(request, timeout=3) as result:
                            result.read()
                    except Exception:
                        GLib.idle_add(self._close_failed)
                threading.Thread(target=finish, daemon=True).start()
            elif self.launch_request and self.child_pid:
                # Cancel only this window's bootstrap. Its finally block frees
                # only its own model child; attached servers are never stopped.
                try:
                    os.kill(self.child_pid, signal.SIGINT)
                except ProcessLookupError:
                    self.destroy()
            else:
                self.terminal.feed_child(b'\x04')
            self.status('Finishing the session… You can still use the terminal.')
        return True

    def _close_failed(self):
        self.closing = False
        self.status('Could not request shutdown. Use Ctrl+D or /quit in the terminal, then close the window.')
        self.workspace.set_visible_child_name('terminal')
        self.terminal.grab_focus()

    def _destroy(self, *_):
        self.stopped = True
        if getattr(self, 'timer', None):
            GLib.source_remove(self.timer)
        self.private.cleanup()
        Gtk.main_quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('cli_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not Gtk.init_check([])[0]:
        parser.exit(1, 'Dream Desktop cannot connect to the graphical display. Run it from your desktop terminal.\n')
    cli_args = args.cli_args[1:] if args.cli_args[:1] == ['--'] else args.cli_args
    DreamWindow(args.python, args.cwd, cli_args)
    Gtk.main()


if __name__ == '__main__':
    main()
