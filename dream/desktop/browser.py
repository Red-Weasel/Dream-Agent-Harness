"""Native browser controls. Web content never receives a Python bridge."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib, Gio

from .protocol import browser_address


def button(icon: str, tip: str, action) -> Gtk.Button:
    widget = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    widget.set_tooltip_text(tip)
    widget.get_accessible().set_name(tip)
    widget.connect('clicked', action)
    return widget


def label(text: str, style: str = '', xalign: float = 0) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign)
    if style:
        widget.get_style_context().add_class(style)
    return widget


def webview(*, local_only: bool = False, profile: Path | None = None) -> WebKit2.WebView:
    # Separate ephemeral context per view. In particular, browsing sites cannot
    # inherit the Studio origin's state or script-message capabilities.
    if profile is not None and not local_only:
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        manager = WebKit2.WebsiteDataManager(base_data_directory=str(profile / 'data'),
                                            base_cache_directory=str(profile / 'cache'))
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
        context.get_cookie_manager().set_persistent_storage(
            str(profile / 'cookies.sqlite'), WebKit2.CookiePersistentStorage.SQLITE)
    else:
        context = WebKit2.WebContext.new_ephemeral()
    if local_only:
        context.get_website_data_manager().set_network_proxy_settings(WebKit2.NetworkProxyMode.NO_PROXY, None)
    view = WebKit2.WebView.new_with_context(context)
    settings = view.get_settings()
    settings.set_enable_developer_extras(True)
    settings.set_allow_file_access_from_file_urls(False)
    settings.set_allow_universal_access_from_file_urls(False)
    view.connect('permission-request', lambda _view, request: (request.deny(), True)[1])
    return view


class Browser(Gtk.Box):
    def __init__(self, status):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.status = status
        self.last_address = ''
        self.failed = False
        self.view = webview()
        bar = Gtk.Box(spacing=6)
        bar.get_style_context().add_class('toolbar')
        self.back = button('go-previous-symbolic', 'Back (Alt+Left)', lambda _: self.view.go_back())
        self.forward = button('go-next-symbolic', 'Forward (Alt+Right)', lambda _: self.view.go_forward())
        self.reload_button = button('view-refresh-symbolic', 'Reload (Ctrl+R)', lambda _: self.reload())
        self.address = Gtk.Entry(placeholder_text='Search your project by URL · localhost:3000')
        self.address.get_accessible().set_name('Browser address')
        self.address.connect('activate', lambda entry: self.navigate(entry.get_text()))
        for w in (self.back, self.forward, self.reload_button):
            bar.pack_start(w, False, False, 0)
        bar.pack_start(self.address, True, True, 0)
        bar.pack_end(button('go-home-symbolic', 'Browser home', lambda _: self.home()), False, False, 0)
        self.remember = Gtk.CheckButton(label='Remember sign-ins')
        self.remember.set_tooltip_text('Use Dream’s saved browser profile. Uncheck to return to a fresh private session; saved sign-ins are retained.')
        self.remember.connect('toggled', self._remember)
        bar.pack_end(self.remember, False, False, 0)
        bar.pack_end(button('open-in-new-symbolic', 'Open in external browser', lambda _: self._external()), False, False, 0)
        self.pack_start(bar, False, False, 0)
        self.progress = Gtk.ProgressBar()
        self.pack_start(self.progress, False, False, 0)

        self.findbar = Gtk.SearchBar()
        findbox = Gtk.Box(spacing=6)
        self.find = Gtk.SearchEntry(placeholder_text='Find on this page')
        self.find.connect('search-changed', self._find)
        self.find.connect('activate', lambda _: self.view.get_find_controller().search_next())
        findbox.pack_start(self.find, True, True, 0)
        findbox.pack_start(button('go-up-symbolic', 'Previous match', lambda _: self.view.get_find_controller().search_previous()), False, False, 0)
        findbox.pack_start(button('go-down-symbolic', 'Next match', lambda _: self.view.get_find_controller().search_next()), False, False, 0)
        self.findbar.add(findbox)
        self.findbar.connect_entry(self.find)
        self.findbar.set_show_close_button(True)
        self.findbar.connect('notify::search-mode-enabled', lambda *_: self.view.get_find_controller().search_finish() if not self.findbar.get_search_mode() else None)
        self.pack_start(self.findbar, False, False, 0)
        self.stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.stack.add_named(self.view, 'web')
        self.empty = self._empty()
        home_scroll = Gtk.ScrolledWindow()
        home_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        home_scroll.add(self.empty)
        self.stack.add_named(home_scroll, 'home')
        self.error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.error_box.get_style_context().add_class('empty')
        self.error_box.set_valign(Gtk.Align.CENTER)
        self.error_box.pack_start(label('This page couldn’t open.', 'headline'), False, False, 0)
        self.error = label('', 'description')
        self.error.set_line_wrap(True)
        self.error.set_max_width_chars(60)
        self.error_box.pack_start(self.error, False, False, 0)
        retry = Gtk.Button(label='Try again')
        retry.set_halign(Gtk.Align.START)
        retry.connect('clicked', lambda _: self.navigate(self.last_address))
        self.error_box.pack_start(retry, False, False, 0)
        error_scroll = Gtk.ScrolledWindow()
        error_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        error_scroll.add(self.error_box)
        self.stack.add_named(error_scroll, 'error')
        self.pack_start(self.stack, True, True, 0)
        self._connect_view()
        self.home()

    def _connect_view(self):
        self.view.connect('load-changed', self._load)
        self.view.connect('load-failed', self._failed)
        self.view.connect('notify::estimated-load-progress', self._progress)
        self.view.connect('notify::uri', self._uri)
        self.view.connect('decide-policy', self._policy)
        self.view.connect('create', self._popup)
        self.view.connect('web-process-terminated', self._terminated)
        self.view.get_context().connect('download-started', self._download)

    def _remember(self, toggle):
        address = self.view.get_uri() or self.last_address
        profile = Path(GLib.get_user_data_dir()) / 'dream' / 'browser' if toggle.get_active() else None
        try:
            replacement = webview(profile=profile)
        except (OSError, GLib.Error) as exc:
            self.status(f'Browser profile unavailable: {exc}')
            return
        self.view.stop_loading()
        self.stack.remove(self.view)
        self.view.destroy()
        self.view = replacement
        self.stack.add_named(self.view, 'web')
        self._connect_view()
        self.view.show()
        if address and address != 'about:blank':
            self.navigate(address)
        else:
            self.home()
        self.status('Saved browser profile enabled' if profile else 'Private browser session enabled')

    def _external(self):
        try:
            address = browser_address(self.view.get_uri() or self.last_address)
            Gio.AppInfo.launch_default_for_uri(address, None)
        except (ValueError, GLib.Error) as exc:
            self.status(f'External browser unavailable: {exc}')

    def _empty(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        outer.get_style_context().add_class('empty')
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)
        icon = Gtk.Image.new_from_icon_name('web-browser-symbolic', Gtk.IconSize.DIALOG)
        icon.set_halign(Gtk.Align.START)
        inner.pack_start(icon, False, False, 0)
        inner.pack_start(label('A browser, right here.', 'headline'), False, False, 0)
        description = label('Open your local development server or a website.\nKeep building in the terminal while you explore.', 'description')
        description.set_line_wrap(True)
        inner.pack_start(description, False, False, 0)
        hint = label('Ctrl+L to enter an address', 'shortcut')
        hint.set_halign(Gtk.Align.START)
        inner.pack_start(hint, False, False, 0)
        outer.pack_start(inner, True, True, 0)
        return outer

    def home(self):
        self.view.stop_loading()
        self.stack.set_visible_child_name('home')
        self.progress.set_fraction(0)
        self.address.set_text('')
        self.status('Browser ready')

    def navigate(self, raw: str):
        try:
            address = browser_address(raw)
        except ValueError as exc:
            self.status(str(exc))
            self.address.get_style_context().add_class('error')
            return
        self.address.get_style_context().remove_class('error')
        self.last_address = address
        self.failed = False
        self.stack.set_visible_child_name('web')
        self.view.load_uri(address)
        self.view.grab_focus()

    def reload(self):
        if self.stack.get_visible_child_name() == 'error':
            self.navigate(self.last_address)
        else:
            self.view.reload()

    def _policy(self, _view, decision, kind):
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if urlsplit(uri).scheme not in ('http', 'https', 'about'):
                decision.ignore()
                self.status('This browser opens http and https websites only.')
                return True
        return False

    def _popup(self, _view, action):
        # Keep requested links inside this workspace; no privileged popup view.
        uri = action.get_request().get_uri()
        GLib.idle_add(self.navigate, uri)
        return None

    def _progress(self, *_):
        self.progress.set_fraction(self.view.get_estimated_load_progress() if self.view.is_loading() else 0)

    def _uri(self, *_):
        uri = self.view.get_uri() or ''
        if uri and uri != 'about:blank':
            self.address.set_text(uri)
        self.back.set_sensitive(self.view.can_go_back())
        self.forward.set_sensitive(self.view.can_go_forward())

    def _load(self, _view, event):
        if event == WebKit2.LoadEvent.STARTED:
            self.failed = False
            self.stack.set_visible_child_name('web')
            self.status('Loading page…')
        elif event == WebKit2.LoadEvent.FINISHED:
            self.progress.set_fraction(0)
            if not self.failed:
                self.status(self.view.get_title() or 'Page loaded')
            self._uri()

    def _failed(self, _view, _event, uri, error):
        # A superseded navigation is not a page failure.
        if error.matches(WebKit2.NetworkError.quark(), WebKit2.NetworkError.CANCELLED):
            return True
        self.failed = True
        self.last_address = uri
        self.error.set_text(f'{uri}\n\n{error.message}\n\nFor a local preview, check that the development server is running.')
        self.stack.set_visible_child_name('error')
        self.progress.set_fraction(0)
        self.status('Page unavailable — retry when it is ready')
        return True

    def _terminated(self, _view, reason):
        self.failed = True
        self.error.set_text(f'The browser process stopped ({reason.value_nick}). Reload to reopen the page. Your terminal session is still running.')
        self.stack.set_visible_child_name('error')
        self.status('Browser stopped — reload to recover')

    def _find(self, entry):
        text = entry.get_text()
        controller = self.view.get_find_controller()
        if text:
            controller.search(text, WebKit2.FindOptions.CASE_INSENSITIVE | WebKit2.FindOptions.WRAP_AROUND, 1000)
        else:
            controller.search_finish()

    def find_page(self):
        self.findbar.set_search_mode(True)
        self.find.grab_focus()

    def _download(self, _context, download):
        download._dream_failed = False
        download.connect('decide-destination', self._destination)
        def failed(item, error):
            item._dream_failed = True
            self.status(f'Download stopped: {error.message}')
        download.connect('failed', failed)
        download.connect('finished', lambda d: self.status('Download saved') if d.get_destination() and not d._dream_failed else None)

    def _destination(self, download, suggested):
        dialog = Gtk.FileChooserDialog(title='Save download', transient_for=self.get_toplevel(), action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Save', Gtk.ResponseType.ACCEPT)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(Path(suggested).name)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            download.set_destination(Path(dialog.get_filename()).as_uri())
        else:
            download._dream_failed = True
            download.cancel()
        dialog.destroy()
        return True
