"""Dream native chat workspace with Terminal and independent Browser tabs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Vte', '2.91')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte, WebKit2

from ..config import LOG_DIR
from . import engine_controls
from .browser import Browser, button, label, webview
from .crash_log import ENV as CRASH_LOG_ENV, arm_window as crash_log_arm_window
from .protocol import (LoginTracker, login_ack, session_address, session_status,
                       TOOLBAR_SELECTS, TOOLBAR_INPUTS, TOOLBAR_BUTTONS, toolbar_state)
from .login_terminal import LoginTerminal
from .onboarding import Onboarding

HERE = Path(__file__).resolve().parent
WEB_VIEWS = frozenset(('home', 'chat', 'nested', 'studio', 'projects', 'optimizer', 'skills', 'memory', 'sleepwalk', 'understand', 'settings', 'deepsleep'))
# The fatal signals faulthandler records (DREAM-086); the window itself stops a session only with /quit, SIGINT or Ctrl+D.
CRASH_SIGNALS = frozenset((signal.SIGSEGV, signal.SIGBUS, signal.SIGILL, signal.SIGFPE, signal.SIGABRT))
# "Open in browser" (DREAM-111): the session's page server, a loopback origin with a token path.
PAGE_BASE = re.compile(r'http://127\.0\.0\.1:([0-9]{1,5})/[A-Za-z0-9_-]{40,}/')


def page_base(state) -> str | None:
    """The page origin a session reports in /api/desktop, or None unless it is exactly a
    loopback http base with a token path: only those URLs go to the default browser."""
    base = state.get('page_base') if isinstance(state, dict) else None
    match = PAGE_BASE.fullmatch(base) if isinstance(base, str) else None
    return base if match and 0 < int(match.group(1)) < 65536 else None


def color(value: str) -> Gdk.RGBA:
    result = Gdk.RGBA()
    result.parse(value)
    return result


def _note(text: str) -> None:
    """One dated line on stderr (the journal, from the desktop menu): the order of events around a crash."""
    try:
        print(f"[dream desktop {time.strftime('%Y-%m-%d %H:%M:%S')}] {text}", file=sys.stderr, flush=True)
    except OSError:
        pass   # the terminal that started a checkout launch has gone


class DreamWindow(Gtk.Window):
    def __init__(self, python: str, cwd: str, cli_args: list[str], *, autostart: bool = True):
        super().__init__(title='Dream — Workspace')
        self.python, self.cwd, self.cli_args = python, cwd, cli_args
        self.child_pid: int | None = None
        self.crash_log: Path | None = None
        self.running = False
        self.closing = False
        self.polling = False
        self.stopped = False
        self.address_info = None
        self.page_base = None
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
        # Provider sign-in (DREAM-246): a separate window with its own terminal and process. It shares no state
        # with the session terminal below; the only thing it is given from here is the status bar.
        self.login = LoginTerminal(self, Gtk, Vte, GLib, notify=self.status, repo_root=str(HERE.parents[1]),
                                   strip_env=(CRASH_LOG_ENV,))
        self.login_tracker = LoginTracker()
        self.login_sending = False
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
        # Provider selection lives below Understand; do not duplicate it as a title-bar pill.
        self._add_deepsleep_toolbar(header)
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
        self.terminal.connect('key-press-event', self._terminal_keys)
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
        # No row of its own above the work area (the owner, 2026-10-04: [feedback], DREAM-239): the folder's name and New
        # conversation sit in the title bar; the sidebar already says which view this is, so its label is not shown.
        self.workspace_name = Gtk.Button(label=Path(cwd).name or cwd)
        self.workspace_name.get_style_context().add_class('flat')
        self.workspace_name.get_child().set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.workspace_name.get_child().set_max_width_chars(28)
        self.workspace_name.set_tooltip_text('Show the full workspace path')
        self.workspace_name.connect('clicked', self._show_workspace_path)
        self.workspace_path = cwd
        header.pack_start(self.workspace_name)
        self.view_label = label('Chat', 'muted')
        header.pack_end(button('document-new-symbolic', 'New conversation', lambda _: self.new_session()))

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
                ('nested', 'Nested Dream', 'view-grid-symbolic'),
                ('deepsleep', 'DeepSleep', 'network-workgroup-symbolic'),
                ('optimizer', 'Prompt Optimizer', 'document-edit-symbolic'),
                ('studio', 'Artifacts', 'applications-graphics-symbolic'),   # the page's view id stays 'studio' (DREAM-235)
                ('projects', 'Projects', 'folder-symbolic'),
                ('skills', 'Skills', 'applications-system-symbolic'),
                ('memory', 'Lucid Control', 'document-open-recent-symbolic'),
                ('sleepwalk', 'Sleepwalk', 'weather-clear-night-symbolic'),
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
        # Below a divider, panels that open beside the chat: switches, not places (owner, 2026-09-28).
        divider = Gtk.Box()
        divider.get_style_context().add_class('nav-divider')
        self.sidebar.pack_start(divider, False, False, 0)
        self.understand_toggle = Gtk.ToggleButton()
        for name in ('nav-item', 'nav-toggle'):
            self.understand_toggle.get_style_context().add_class(name)
        self.understand_toggle.set_tooltip_text('Understand: show or hide the code map beside the chat')
        self.understand_toggle.get_accessible().set_name('Understand panel')
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Image.new_from_icon_name('view-grid-symbolic', Gtk.IconSize.BUTTON), False, False, 0)
        caption = label('Understand')
        caption.set_xalign(0)
        row.pack_start(caption, True, True, 0)
        self.nav_labels.append(caption)
        self.understand_switch = Gtk.Box(valign=Gtk.Align.CENTER)
        self.understand_switch.get_style_context().add_class('nav-switch')
        self.understand_knob = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.understand_knob.get_style_context().add_class('nav-knob')
        self.understand_switch.pack_start(self.understand_knob, True, True, 0)
        row.pack_end(self.understand_switch, False, False, 0)
        self.nav_labels.append(self.understand_switch)
        self.understand_toggle.add(row)
        self.syncing_understand = False
        self.understand_toggle.connect('toggled', self._understand_toggled)
        self.sidebar.pack_start(self.understand_toggle, False, False, 0)
        self._add_frontier_controls()
        self._add_engine_controls()
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.add(self.sidebar)
        body.pack_start(sidebar_scroll, False, False, 0)
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
        self.onboarding.mode_box.hide()
        self.onboarding.card.hide()
        self.studio_stack.set_visible_child_name('welcome')
        self.workspace.set_visible_child_name('studio')
        self._highlight_navigation('chat')
        self.timer = GLib.timeout_add(700, self._poll)
        self.engine_timer = GLib.timeout_add(3000, self._poll_engine)
        GLib.idle_add(lambda: self._poll_engine() and False)     # once now; the timer repeats it
        if autostart:
            GLib.idle_add(self.start_terminal if self.cli_args else self.onboarding.load_catalog)

    def _resize_navigation(self, _widget, allocation):
        compact = allocation.width < 1000
        for caption in self.nav_labels:
            caption.set_visible(not compact)
        self._layout_deepsleep_toolbar(allocation.width)

    def _highlight_navigation(self, view):
        if not hasattr(self, 'nav_buttons'):
            return
        for name, item in self.nav_buttons.items():
            context = item.get_style_context()
            context.add_class('selected') if name == view else context.remove_class('selected')
        self.view_label.set_text({'optimizer': 'Prompt Optimizer', 'studio': 'Artifacts'}.get(view) or view.title())
        if hasattr(self, 'ds_header'):
            self.ds_header.set_visible(view == 'deepsleep')
            if view != 'deepsleep':
                self.ds_binding = None
                self.ds_header.set_sensitive(False)

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

    def _understand_toggled(self, button):
        on = button.get_active()
        self.understand_knob.set_halign(Gtk.Align.END if on else Gtk.Align.START)
        if not self.syncing_understand:
            self.navigate('understand')

    def _show_understand_state(self, is_open):
        """The page says whether the panel is open (it can also be closed from inside); the switch follows it."""
        if self.understand_toggle.get_active() != is_open:
            self.syncing_understand = True
            self.understand_toggle.set_active(is_open)
            self.syncing_understand = False

    def _add_deepsleep_toolbar(self, header):
        self.ds_binding = None
        self.ds_snapshot = {}
        self.ds_reading = False
        self.ds_syncing = False
        self.ds_controls = {}
        self.ds_header = Gtk.Box(spacing=4)
        self.ds_header.set_no_show_all(True)
        self.ds_inline = Gtk.Box(spacing=4)
        self.ds_more = Gtk.MenuButton(label='Controls')
        self.ds_more.set_tooltip_text('DeepSleep workspace controls')
        self.ds_more.set_no_show_all(True)
        self.ds_popup = Gtk.Popover.new(self.ds_more)
        self.ds_overflow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.ds_overflow.set_border_width(10)
        self.ds_popup.add(self.ds_overflow)
        self.ds_more.set_popover(self.ds_popup)
        self.ds_header.pack_start(self.ds_inline, False, False, 0)
        self.ds_header.pack_start(self.ds_more, False, False, 0)
        for key, title in (('provider', 'Provider'), ('profile', 'Experience'), ('model', 'Model'),
                           ('layout', 'Text view'), ('name', 'Terminal name'),
                           ('add', '+ Terminal'), ('tile', 'Tile'), ('group', 'Group task'),
                           ('connections', 'Message Board'), ('preview', 'Preview'), ('refresh', 'Refresh'),
                           ('characters', 'Characters')):
            if key in TOOLBAR_SELECTS:
                widget = Gtk.ComboBoxText()
                for cell in widget.get_cells():
                    cell.set_property('ellipsize', Pango.EllipsizeMode.END)
                    cell.set_property('max-width-chars', 14)
                widget.connect('changed', lambda item, control=key: self._deepsleep_action(control, item.get_active_id()))
            elif key in TOOLBAR_INPUTS:
                widget = Gtk.Entry(placeholder_text='Terminal name (optional)')
                widget.set_max_length(100)
                widget.set_width_chars(10)
                widget.set_max_width_chars(14)
                widget.connect('changed', lambda item, control=key: self._deepsleep_action(control, item.get_text()))
            else:
                widget = Gtk.Button(label=title)
                widget.connect('clicked', lambda _, control=key: self._deepsleep_action(control))
            widget.set_tooltip_text(title)
            widget.get_accessible().set_name('DeepSleep ' + title)
            widget.set_sensitive(False)
            self.ds_controls[key] = widget
        self._layout_deepsleep_toolbar(1600)
        header.set_custom_title(self.ds_header)

    def _layout_deepsleep_toolbar(self, width):
        if not hasattr(self, 'ds_header'):
            return
        compact = width < 1300
        wide = width >= 1850
        more_label = self.ds_more.get_child()
        more_label.show()
        for key, widget in self.ds_controls.items():
            destination = self.ds_inline if wide or (not compact and key in ('provider', 'profile', 'name', 'add')) else self.ds_overflow
            if widget.get_parent() is not destination:
                if widget.get_parent():
                    widget.get_parent().remove(widget)
                destination.pack_start(widget, False, False, 0)
            widget.show()
        self.ds_inline.show()
        self.ds_more.set_visible(not wide)
        self.ds_overflow.show()

    def _deepsleep_binding(self):
        return (self.address_info, self.session_id, self.workspace_path)

    def _deepsleep_action(self, control, value=None):
        if (self.ds_syncing or control not in TOOLBAR_SELECTS | TOOLBAR_INPUTS | TOOLBAR_BUTTONS
                or self.ds_binding != self._deepsleep_binding()
                or self.native_view != 'deepsleep' or self.workspace.get_visible_child_name() != 'studio'
                or not self._trusted_studio_document()):
            return False
        item = self.ds_snapshot.get(control)
        if not item or item['disabled']:
            return False
        payload = {'session_id': self.session_id, 'workspace': self.workspace_path, 'control': control}
        if control in TOOLBAR_SELECTS:
            if not isinstance(value, str) or not any(option['value'] == value and not option['disabled'] for option in item['options']):
                return False
            payload['value'] = value
        elif control in TOOLBAR_INPUTS:
            if not isinstance(value, str) or len(value) > 100:
                return False
            payload['value'] = value
        elif value is not None:
            return False
        self.studio.run_javascript('window.DreamDeepSleepToolbar?.dispatch(' + json.dumps(payload) + ')', None, None, None)
        return True

    def _sync_deepsleep_toolbar(self):
        if not hasattr(self, 'ds_header'):
            return
        ready = (self.native_view == 'deepsleep' and self.workspace.get_visible_child_name() == 'studio'
                 and self._trusted_studio_document())
        if not ready:
            self.ds_binding = None
            self.ds_header.set_sensitive(False)
            return
        if self.ds_reading:
            return
        self.ds_reading = True
        self.studio.run_javascript('JSON.stringify(window.DreamDeepSleepToolbar?.state() || null)',
                                   None, self._deepsleep_state_read, self._deepsleep_binding())

    def _deepsleep_state_read(self, view, result, binding):
        self.ds_reading = False
        try:
            raw = view.run_javascript_finish(result).get_js_value().to_string()
        except GLib.Error:
            return
        if (binding != self._deepsleep_binding() or self.native_view != 'deepsleep'
                or self.workspace.get_visible_child_name() != 'studio' or not self._trusted_studio_document()):
            return
        state = toolbar_state(raw, self.session_id, self.workspace_path)
        if state is None:
            self.ds_binding = None
            self.ds_header.set_sensitive(False)
            return
        self.ds_syncing = True
        try:
            for key, widget in self.ds_controls.items():
                item = state['controls'].get(key)
                widget.set_sensitive(bool(item) and not item['disabled'])
                if not item:
                    continue
                if key in TOOLBAR_SELECTS:
                    if self.ds_snapshot.get(key, {}).get('options') != item['options']:
                        widget.remove_all()
                        for option in item['options']:
                            widget.append(option['value'], option['label'])
                    if widget.get_active_id() != item['value']:
                        widget.set_active_id(item['value'])
                elif key in TOOLBAR_INPUTS:
                    if not widget.is_focus() and widget.get_text() != item['value']:
                        widget.set_text(item['value'])
                elif item.get('pressed'):
                    widget.get_style_context().add_class('suggested-action')
                else:
                    widget.get_style_context().remove_class('suggested-action')
            self.ds_snapshot = state['controls']
            self.ds_binding = binding
            self.ds_header.set_sensitive(True)
        finally:
            self.ds_syncing = False

    def _add_frontier_controls(self):
        self.frontier_buttons, self.frontier_knobs, self.frontier_logins = {}, {}, {}
        self.frontier_users = {}
        self.syncing_frontiers = False
        self.frontier_message = ''
        for key, title in (('anthropic', 'Claude'), ('grok', 'Grok'), ('antigravity', 'Gemini'), ('codex', 'Codex')):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            group.set_margin_top(5)
            group.set_margin_bottom(5)
            pill = Gtk.Button()
            pill.get_style_context().add_class('frontier-pill')
            pill.get_accessible().set_name('Sign in to ' + title)
            pill.set_tooltip_text('Sign in to ' + title + ' · account not checked')
            pill.set_sensitive(False)
            content = Gtk.Box(spacing=9)
            logo_name = {'anthropic': 'claude', 'antigravity': 'gemini'}.get(key, key)
            logo = Gtk.Image()
            from gi.repository import GdkPixbuf
            logo_path = HERE.parent / 'gui' / 'static' / 'provider-logos' / (logo_name + '.svg')
            if logo_path.is_file():
                logo.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(logo_path), 20, 20, True))
            logo.set_size_request(20, 20)
            content.pack_start(logo, False, False, 0)
            caption = label(title)
            caption.set_xalign(0)
            content.pack_start(caption, True, True, 0)
            dot = label('●')
            dot.get_style_context().add_class('account-unknown')
            content.pack_end(dot, False, False, 0)
            pill.add(content)
            pill.connect('clicked', lambda _, provider=key: self._frontier_action('login', provider))
            username = label('', 'frontier-user')
            username.set_xalign(0)
            username.set_ellipsize(Pango.EllipsizeMode.END)
            username.set_max_width_chars(20)
            username.set_no_show_all(True)
            group.pack_start(pill, False, False, 0)
            group.pack_start(username, False, False, 0)
            self.sidebar.pack_start(group, False, False, 0)
            # Provider names remain visible; account identity never changes geometry.
            self.frontier_buttons[key] = self.frontier_logins[key] = pill
            self.frontier_knobs[key] = dot
            self.frontier_users[key] = username

    def _add_engine_controls(self):
        """Unload model and Load model under the provider pills (the owner, 2026-10-10): switch the local model in
        the middle of a build without closing Dream and every terminal with it."""
        self.engine_model = None
        self.engine_busy = None             # what the sidebar says while an unload or a load runs
        self.engine_polling = False
        self.engine_loader = None           # the running `startup load` process
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class('engine-controls')
        box.set_margin_top(10)
        self.engine_label = label('Local model: checking…', 'engine-state')
        self.engine_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.engine_label.set_max_width_chars(24)
        box.pack_start(self.engine_label, False, False, 0)
        self.unload_button = Gtk.Button(label='Unload model')
        self.load_button = Gtk.Button(label='Load model')
        for widget, action in ((self.unload_button, self._unload_clicked), (self.load_button, self._load_clicked)):
            widget.get_style_context().add_class('engine-button')
            widget.get_accessible().set_name(widget.get_label())
            widget.set_sensitive(False)
            widget.connect('clicked', action)
            box.pack_start(widget, False, False, 0)
        self.sidebar.pack_start(box, False, False, 0)

    def _poll_engine(self):
        if self.stopped:
            return False
        if not self.engine_polling:
            self.engine_polling = True
            def work():
                model = engine_controls.served_model()
                GLib.idle_add(self._engine_polled, model)
            threading.Thread(target=work, daemon=True).start()
        return True

    def _engine_polled(self, model):
        self.engine_polling = False
        if not self.stopped:
            self.engine_model = model
            self._show_engine_state()
        return False

    def _show_engine_state(self):
        shown = engine_controls.buttons(self.engine_model, busy=self.engine_busy, session_running=self.running)
        self.engine_label.set_text(shown['text'])
        self.engine_label.set_tooltip_text(shown['text'])
        self.unload_button.set_sensitive(shown['unload'])
        self.unload_button.set_tooltip_text(shown['unload_tip'])
        self.load_button.set_sensitive(shown['load'])
        self.load_button.set_tooltip_text(shown['load_tip'])

    def _unload_clicked(self, _button):
        model = self.engine_model
        if not model or self.engine_busy:
            return
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=f'Unload {model}?')
        dialog.format_secondary_text('Its GPU memory is freed. A local terminal in the middle of a reply stops; every '
                                     'terminal stays open. Use Load model to choose the next model.')
        dialog.add_buttons('Keep it loaded', Gtk.ResponseType.CANCEL, 'Unload model', Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT:
            return
        self.engine_busy = f'Unloading {model}…'
        self._show_engine_state()
        self.status(f'Unloading {model}… Dream asks the engine to stop and waits for it.')
        def work():
            try:
                done = subprocess.run(engine_controls.unload_command(self.python), cwd=self.cwd, env=self.child_env(),
                                      capture_output=True, text=True, timeout=180)
                message = engine_controls.unload_result(done.returncode, done.stdout, done.stderr)
            except Exception as exc:
                message = f'Unload did not finish: {exc}'
            GLib.idle_add(self._engine_step_done, message)
        threading.Thread(target=work, daemon=True).start()

    def _engine_step_done(self, message):
        self.engine_busy = None
        if not self.stopped:
            self.status(message)
            self._show_engine_state()
            self._poll_engine()
        return False

    def _load_clicked(self, _button):
        if self.engine_model or self.engine_busy or not self.running:
            return
        # Home and its model selector, in load-only mode: local models, and Load model in place of Start.
        self.workspace.set_visible_child_name('studio')
        self._highlight_navigation('home')
        self.studio_stack.set_visible_child_name('welcome')
        self.welcome_title.set_text('')
        self.welcome_description.set_text('')
        self.onboarding.set_load_only(True)
        self.onboarding.set_running(False)
        self.onboarding.load_catalog()

    def _close_model_loader(self):
        if not self.onboarding.load_only or self.engine_loader is not None:
            return
        self.onboarding.set_load_only(False)
        self.onboarding.set_running(self.running)
        if self.connected:
            self.studio_stack.set_visible_child_name('web')

    def load_model(self, request):
        """Load a local model for the open session and its terminals (Onboarding.launch in load-only mode)."""
        if self.engine_loader is not None or self.engine_model:
            return
        request_path = Path(self.private.name) / engine_controls.LOAD_REQUEST
        status_path = Path(self.private.name) / engine_controls.LOAD_STATUS
        request_path.write_text(json.dumps(request))
        request_path.chmod(0o600)
        status_path.unlink(missing_ok=True)
        try:
            self.engine_loader = subprocess.Popen(
                engine_controls.load_command(self.python, str(request_path), str(status_path)), cwd=self.cwd,
                env=self.child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True)
        except OSError as exc:
            self.onboarding.message.set_text(f'Could not start loading: {exc}')
            return
        self.engine_busy = 'Loading a model…'
        self._show_engine_state()
        self.onboarding.set_running(True)
        self.onboarding.message.set_text('Loading the model. Large models can take several minutes.')
        loader = self.engine_loader
        def wait():
            error = loader.stderr.read()
            GLib.idle_add(self._load_finished, loader, loader.wait(), error)
        threading.Thread(target=wait, daemon=True).start()
        GLib.timeout_add(1000, self._load_progress, loader, status_path)

    def _load_progress(self, loader, status_path):
        if self.stopped or self.engine_loader is not loader:
            return False
        shown = engine_controls.load_status(status_path)
        if shown:
            self.onboarding.message.set_text(shown[1])
            self.status(shown[1])
        return True

    def _load_finished(self, loader, returncode, error):
        if self.stopped or self.engine_loader is not loader:
            return False
        self.engine_loader = None
        self.engine_busy = None
        shown = engine_controls.load_status(Path(self.private.name) / engine_controls.LOAD_STATUS)
        message = shown[1] if shown else (error.strip()[-400:] or f'Loading ended with exit status {returncode}.')
        self.status(message)
        if returncode == 0:
            self._close_model_loader()
        else:
            self.onboarding.set_running(False)
            self.onboarding.message.set_text(message)
        self._show_engine_state()
        self._poll_engine()
        return False

    def _frontier_action(self, action, key, enabled=None):
        if key not in self.frontier_buttons or action != 'login' or not self._trusted_studio_document():
            self.status('Connect a Dream session before selecting or signing in to a frontier.')
            return False
        self.workspace.set_visible_child_name('studio')
        payload = json.dumps({'provider': key, 'enabled': enabled})
        self.studio.run_javascript("window.dispatchEvent(new CustomEvent('dream:frontier-" + action +
                                   "', {detail:" + payload + "}));", None, None, None)
        return True

    def _show_frontier_state(self, state):
        if not isinstance(state, dict) or not isinstance(state.get('controls'), dict):
            return
        for key, pill in self.frontier_buttons.items():
            value = state['controls'].get(key, {})
            signed_in = value.get('signed_in') is True
            pill.set_sensitive(state.get('loginBusy') is not True)
            dot = self.frontier_knobs[key].get_style_context()
            for name in ('account-red', 'account-green', 'account-unknown'):
                dot.remove_class(name)
            status = value.get('status')
            dot.add_class('account-green' if signed_in else
                          'account-red' if status in ('signed_out', 'unavailable') else 'account-unknown')
            title = value.get('title')
            if isinstance(title, str):
                pill.set_tooltip_text(title[:500])
            username = value.get('username')
            self.frontier_users[key].set_text('')
            self.frontier_users[key].hide()
            if signed_in and isinstance(username, str) and username:
                pill.set_tooltip_text((title[:500] if isinstance(title, str) else key) + ' · ' + username[:200])
        message = state.get('message')
        if isinstance(message, str) and message != self.frontier_message:
            self.frontier_message = message
            if message:
                self.status(message[:1500])

    def navigate(self, view):
        """Select a known surface without launching a task or changing its workspace."""
        if view not in WEB_VIEWS and view not in ('terminal', 'browser'):
            return False
        self._close_model_loader()          # leaving the model selector the sidebar opened, without loading
        if view in ('terminal', 'browser'):
            self.workspace.set_visible_child_name(view)
            (self.terminal if view == 'terminal' else self.browser.view).grab_focus()
        elif view == 'understand':
            # A switch, not a page: the page opens or closes the panel beside the chat and keeps its own view.
            self.workspace.set_visible_child_name('studio')
            self._dispatch_view('understand')
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
        return (uri.scheme == trusted.scheme and uri.netloc == trusted.netloc and uri.path == '/'
                and parse_qs(uri.fragment).get('token') == [token])

    def _dispatch_view(self, view=None):
        view = view or self.native_view
        if not self._trusted_studio_document() or view not in WEB_VIEWS:
            return False
        payload = json.dumps({'view': view, 'session_id': self.session_id})
        self.studio.run_javascript(
            "document.documentElement.classList.add('dream-native');"
            "document.documentElement.dataset.nativeSession = " + json.dumps(self.session_id) + ";"
            "window.dispatchEvent(new CustomEvent('dream:navigate', {detail:" + payload + "}));",
            None, None, None)
        return True

    def _sync_web_view(self):
        self._sync_deepsleep_toolbar()
        if (self.reading_web_view or self.workspace.get_visible_child_name() != 'studio'
                or not self._trusted_studio_document()):
            return
        self.reading_web_view = True
        self.studio.run_javascript("(document.documentElement.dataset.dreamView || '') + '|' + "
                                   "(window.DreamUnderstand && window.DreamUnderstand.isOpen() ? '1' : '0') + '|' + "
                                   "JSON.stringify(window.DreamFrontiers ? window.DreamFrontiers.state() : null)",
                                   None, self._web_view_read, (self.address_info, self.native_view))

    def _web_view_read(self, view, result, _data):
        self.reading_web_view = False
        try:
            name, _, tail = view.run_javascript_finish(result).get_js_value().to_string().partition('|')
            understand, _, frontiers = tail.partition('|')
        except GLib.Error:
            # A page replaced during a reconnect has no selection to synchronize.
            return
        if _data[0] == self.address_info and self._trusted_studio_document():
            self._show_understand_state(understand == '1')
            if frontiers:
                try:
                    self._show_frontier_state(json.loads(frontiers))
                except (ValueError, TypeError):
                    pass
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
        box.set_vexpand(True)
        self.onboarding = Onboarding(self)
        box.pack_start(self.onboarding, True, True, 0)
        self.welcome_title = label('', 'headline')
        self.welcome_description = label('', 'description')
        self.welcome_description.set_line_wrap(True)
        for text in (self.welcome_title, self.welcome_description):
            text.set_no_show_all(True)
            text.connect('notify::label', lambda widget, *_: widget.set_visible(bool(widget.get_text())))
        box.pack_start(self.welcome_title, False, False, 0)
        box.pack_start(self.welcome_description, False, False, 0)
        self.retry_studio = Gtk.Button(label='Reconnect conversation')
        self.retry_studio.connect('clicked', lambda _: self._connect_studio(force=True))
        self.retry_studio.set_sensitive(False)
        self.retry_studio.set_no_show_all(True)
        self.retry_studio.set_halign(Gtk.Align.CENTER)
        box.pack_start(self.retry_studio, False, False, 0)
        # Decorative paint does not take layout space or intercept clicks.
        backdrop = Gtk.Overlay()
        backdrop.add(box)
        self.welcome_art = Gtk.DrawingArea()
        # Overlay pass-through does not disable a DrawingArea's own GdkWindow.
        # Paint into the parent surface so decoration can never receive input.
        self.welcome_art.set_has_window(False)
        self.welcome_art.set_can_focus(False)
        backdrop.add_overlay(self.welcome_art)
        backdrop.set_overlay_pass_through(self.welcome_art, True)
        self._welcome_art_available = False
        try:
            import cairo
            gi.require_foreign('cairo')
        except (ImportError, ValueError):
            return backdrop
        self._welcome_cairo = cairo
        self._welcome_art_available = True
        from gi.repository import GdkPixbuf
        self._welcome_artwork = GdkPixbuf.Pixbuf.new_from_file(str(HERE.parent / 'gui/static/dream-eclipse-v2.png'))
        self.welcome_art.connect('draw', self._draw_welcome_art)
        self.onboarding.connect('size-allocate', lambda *_: self.welcome_art.queue_draw())
        return backdrop

    def _draw_welcome_art(self, area, cr):
        """Fade the existing eclipse into spare canvas, keeping its full circle clear of controls."""
        point = self.onboarding.translate_coordinates(area, 0, 0)
        if point is None or not self.onboarding.get_visible():
            return False
        start = point[0] + self.onboarding.get_allocated_width() + 24
        available = area.get_allocated_width() - start - 24
        if available < 240:
            return False
        viewport = area.get_parent().get_parent()
        height = min(area.get_allocated_height(), viewport.get_allocated_height())
        diameter = min(900, available, height * .85)
        # The source circle is centered near (1170, 440), radius 340. A larger
        # feathered radius keeps the rim and figure intact, with no image rectangle.
        scale = diameter / 940
        x = start + available / 2
        y = height / 2
        cr.save()
        cr.rectangle(start, 0, available, height)
        cr.clip()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, self._welcome_artwork, -1170, -440)
        mask = self._welcome_cairo.RadialGradient(0, 0, 340, 0, 0, 470)
        mask.add_color_stop_rgba(0, 0, 0, 0, 1)
        mask.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.mask(mask)
        cr.restore()
        return False

    def child_env(self):
        env = dict(os.environ)
        # browser.sandbox_environment() set these for this process's own web views (DREAM-187); a session
        # and whatever it starts are not WebKit and do not inherit them.
        env.pop('WEBKIT_FORCE_SANDBOX', None)
        env.pop('GST_REGISTRY', None)
        env.update(DREAM_GUI='1', DREAM_DESKTOP_SESSION_FILE=str(self.discovery),
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
        self.page_base = None
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
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.crash_log = LOG_DIR / f"session-crash-{time.strftime('%Y%m%d-%H%M%S')}.log"
        env[CRASH_LOG_ENV] = str(self.crash_log)
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
        if self.onboarding.load_only and self.engine_loader is None:
            self.onboarding.set_load_only(False)      # no session left to load a model into
        self._show_engine_state()
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
        if os.WIFSIGNALED(wait_status):   # DREAM-086: say what stopped it, how to continue, and where its traceback is
            sig = os.WTERMSIG(wait_status)
            try:
                name = signal.Signals(sig).name
            except ValueError:
                name = f'signal {sig}'
            log = self.crash_log
            traced = log is not None and log.exists() and log.stat().st_size > 0
            if log is not None and log.exists() and not traced:
                log.unlink()
            text = ((f'The session crashed ({name}).' if sig in CRASH_SIGNALS else f'The session was stopped by {name}.')
                    + ' Start a new session and type /resume to continue from its saved turns.'
                    + (f' Crash traceback: {log}' if traced else ''))
            self.session_label.set_text('Session crashed' if sig in CRASH_SIGNALS else 'Session stopped')
            self.onboarding.message.set_text(text)
            self.welcome_description.set_text(text)
            self.status(text)
        _note(f'session process exited, wait status {wait_status}')
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
            self.page_base = page_base(state)
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
                if self.native_view == 'chat':
                    # The Chat view shows the artifact beside the conversation itself (DREAM-233); when the page
                    # chose the Artifacts tab instead (a narrow window), the 700 ms view poll follows it.
                    self.status('Dream opened an artifact beside the chat.')
                else:
                    self.navigate('studio')
                    self.status('Dream opened an artifact in the Artifacts tab.')
            self._login_poll(pid, address, state)
        elif (error or not address) and self.connected:
            self.connected = False
            self.session_label.set_text('Studio reconnecting')
            self.session_label.get_style_context().remove_class('connected')
            self.status('Studio disconnected. Retrying automatically; your terminal remains available.')
        return False

    # --- provider sign-in windows (DREAM-246). Nothing here touches the session terminal or its state. ---

    def _login_poll(self, pid, address, state):
        """Act once on a sign-in window the session asked for, and deliver the answers still owed to it."""
        binding = (pid, address)
        request = self.login_tracker.take(binding, state)
        if request:
            request_id, provider = request
            self.login.launch(provider, self.cwd, lambda status: self.login_tracker.report(
                binding, request_id, provider, status))
        due = self.login_tracker.due()
        if self.login_sending or not due or not address:
            return
        self.login_sending = True
        base, token = address

        def work():
            delivered = []
            for request_id, provider, status in due:
                try:
                    if login_ack(base, token, request_id, provider, status):
                        delivered.append((request_id, status))
                except Exception:
                    break   # the session did not answer; the next poll tries again
            GLib.idle_add(self._login_sent, binding, delivered)
        threading.Thread(target=work, daemon=True).start()

    def _login_sent(self, binding, delivered):
        self.login_sending = False
        for request_id, status in delivered:
            self.login_tracker.sent(binding, request_id, status)
        return False

    def _connect_studio(self, force=False):
        if not self.address_info:
            self.status('Studio connects after you select an engine and workspace.')
            return
        base, token = self.address_info
        self.last_connect = time.monotonic()
        self.studio_failed = False
        self.studio_stack.set_visible_child_name('web')
        # The token rides in the fragment (DREAM-187); this view has no address bar, and the
        # page keeps it there so _trusted_studio_document can recognise its own document.
        self.studio.load_uri(f'{base}/?companion=1#token={quote(token)}')

    def _studio_policy(self, _view, decision, kind):
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri()
            parsed = urlsplit(uri)
            # about:blank/srcdoc are used by the existing sandboxed artifact iframe.
            if parsed.scheme == 'about':
                return False
            # "Open in browser" (DREAM-111): a page of this session's page server goes to the
            # owner's default browser on a click, and never loads in this view.
            page = getattr(self, 'page_base', None)
            if isinstance(page, str) and uri.startswith(page):
                decision.ignore()
                if action.is_user_gesture():
                    self._open_in_default_browser(uri)
                return True
            trusted = self.address_info and uri.startswith(self.address_info[0] + '/')
            if not trusted:
                decision.ignore()
                if action.is_user_gesture() and parsed.scheme in ('http', 'https'):
                    self.workspace.set_visible_child_name('browser')
                    self.browser.navigate(uri)
                return True
        return False

    def _open_in_default_browser(self, uri):
        name = unquote(urlsplit(uri).path.rsplit('/', 1)[-1]) or 'the page'
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
            self.status(f'Opened {name} in your default browser.')
        except GLib.Error as exc:
            self.workspace.set_visible_child_name('browser')
            self.browser.navigate(uri)
            self.status(f'The default browser did not open ({exc.message}); {name} opened in the Browser tab instead.')

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

    def _terminal_keys(self, _widget, event):
        # A modified-key sequence contains no Enter byte: permission prompts
        # must never mistake a request for a new line for approval/submission.
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        other = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
                               | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
        if shift and not other and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.running:
                self.terminal.feed_child(b'\x1b[13;2u')
            return True
        return False

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
                                     'Ctrl+Plus / Minus / 0  Terminal text size\nF11  Fullscreen\n'
                                     'Shift+Enter / Alt+Enter  New line in a Dream prompt\nEnter  Send prompt\n\n'
                                     'Ctrl+C still interrupts Dream. Ctrl+D or /quit finishes the session.')
        dialog.run()
        dialog.destroy()

    def _close(self, *_):
        if not self.running:
            return False
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text='Finish this Dream session?')
        dialog.format_secondary_text('Dream will finish its current turn and save the conversation before this window '
                                     'closes. Saving to long-term memory also writes a summary and the memories worth '
                                     'keeping; that model pass can take several minutes.')
        # DREAM-185: the owner can label the memory this session saves ("09.28.26 - rocket animation - day 2").
        box = dialog.get_content_area()
        prompt = Gtk.Label(label='Label this memory (optional)', xalign=0)
        prompt.get_style_context().add_class('muted')
        label_entry = Gtk.Entry(placeholder_text='e.g. 09.28.26 - rocket animation - day 2', max_length=120,
                                activates_default=True)
        box.pack_start(prompt, False, False, 0)
        box.pack_start(label_entry, False, False, 0)
        prompt.show()
        label_entry.show()
        dialog.add_buttons('Keep working', Gtk.ResponseType.CANCEL,
                           'Close without saving to memory', Gtk.ResponseType.REJECT,
                           'Save to memory and close', Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        response = dialog.run()
        memory_label = ' '.join(label_entry.get_text().split())
        dialog.destroy()
        if response in (Gtk.ResponseType.ACCEPT, Gtk.ResponseType.REJECT):
            quit_line = (('/quit save ' + memory_label).strip() if response == Gtk.ResponseType.ACCEPT
                         else '/quit nosave')
            self.closing = True
            if self.address_info:
                base, token = self.address_info
                def finish():
                    try:
                        request = Request(base + '/api/prompt', data=json.dumps({'prompt': quit_line}).encode(),
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
        if getattr(self, 'login', None) is not None:
            self.login.shutdown()   # stops only the sign-in window's own process, and does not wait
        if getattr(self, 'timer', None):
            GLib.source_remove(self.timer)
        if getattr(self, 'engine_timer', None):
            GLib.source_remove(self.engine_timer)
        loader = getattr(self, 'engine_loader', None)
        if loader is not None and loader.poll() is None:
            # A load still running when the window closes: interrupt it as Ctrl-C would. Its cleanup stops the
            # engine it started, so no model is left loaded with nobody to unload it.
            loader.send_signal(signal.SIGINT)
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
    Gdk.set_program_class('Dream')   # dialogs too carry the WM_CLASS that dream/computer.py refuses to drive
    cli_args = args.cli_args[1:] if args.cli_args[:1] == ['--'] else args.cli_args
    crash_log_arm_window()   # the window's own exit leaves its stacks on stderr (the journal, from the desktop menu)
    DreamWindow(args.python, args.cwd, cli_args)
    Gtk.main()
    _note('window main loop ended')


if __name__ == '__main__':
    main()
