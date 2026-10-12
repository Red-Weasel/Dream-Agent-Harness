"""Bounded native model catalog. Selection never starts an engine."""
from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from .browser import label


def details(choice):
    parts = [choice.get('quantization'), choice.get('format')]
    size = choice.get('size_gb')
    if size is not None:
        parts.append(f'{size:.1f} GB')
    if choice.get('shard_count', 1) > 1:
        parts.append(f"{choice['shard_count']} parts")
    parts.append(choice.get('location'))
    if choice.get('kind') == 'local':
        vision = choice.get('vision')
        parts.append('Vision' if vision is True else 'Text only' if vision is False else 'Vision unreported')
    return ' · '.join(str(p) for p in parts if p)


class ModelChooser(Gtk.MenuButton):
    """ComboBox-compatible selection API with searchable grouped rows."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.choices, self.active = [], -1
        self.get_style_context().add_class('model-chooser')
        self.get_accessible().set_name('Agent or local model')
        header = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = label('Choose an agent or model', 'model-name')
        self.subtitle = label('Installed agents and local model library', 'muted')
        for item in (self.title, self.subtitle):
            item.set_ellipsize(Pango.EllipsizeMode.END)
            item.set_max_width_chars(64)
            text.pack_start(item, False, False, 0)
        header.pack_start(text, True, True, 0)
        header.pack_end(label('⌄'), False, False, 0)
        self.add(header)
        self.popover = Gtk.Popover.new(self)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.set_popover(self.popover)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_border_width(12)
        self.search = Gtk.SearchEntry(placeholder_text='Search names, formats or locations')
        self.search.get_accessible().set_name('Search models')
        self.search.connect('search-changed', self.filter_rows)
        self.search.connect('key-press-event', self.search_key)
        body.pack_start(self.search, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_min_content_height(160)
        self.scroll.set_max_content_height(720)
        self.scroll.set_propagate_natural_height(True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(True)
        self.list.connect('row-activated', self.activate_row)
        self.scroll.add(self.list)
        body.pack_start(self.scroll, True, True, 0)
        self.empty = label('No matching models. Try another name or location.', 'muted')
        body.pack_start(self.empty, False, False, 0)
        body.pack_start(label('Choosing a model only reads its settings.', 'muted'), False, False, 0)
        self.popover.add(body)
        self.popover.connect('show', self.opened)
        self._size_window = self._size_handler = None
        self._refit_source = None
        self._refitting = False
        self.connect('realize', self.watch_window)
        self.connect('destroy', self.unwatch_window)

    def watch_window(self, *_):
        self.unwatch_window()
        self._size_window = self.get_toplevel()
        self._size_handler = self._size_window.connect(
            'size-allocate', lambda *_: self.fit_popup() if self.popover.get_visible() else None)

    def unwatch_window(self, *_):
        if self._refit_source is not None:
            GLib.source_remove(self._refit_source)
            self._refit_source = None
        if self._size_window is not None and self._size_handler is not None:
            if self._size_window.handler_is_connected(self._size_handler):
                self._size_window.disconnect(self._size_handler)
        self._size_window = self._size_handler = None

    def fit_popup(self):
        window = self.get_toplevel()
        point = self.translate_coordinates(window, 0, 0)
        if not point:
            return
        # GTK places the arrow relative to the trigger; reserve space for the
        # search field, footnote and popup padding inside the actual window.
        below = window.get_allocated_height() - point[1] - self.get_allocated_height() - 20
        above = point[1] - 20
        room = max(below, above)
        position = Gtk.PositionType.BOTTOM if below >= above else Gtk.PositionType.TOP
        previous_maximum = self.scroll.get_max_content_height()
        maximum = max(80, min(720, room - 110))
        self.scroll.set_min_content_height(min(160, maximum))
        self.scroll.set_max_content_height(maximum)
        self.popover.set_size_request(min(650, max(280, self.get_allocated_width()), window.get_allocated_width() - 40), -1)
        self.popover.set_position(position)
        if self.popover.get_mapped() and maximum != previous_maximum and self._refit_source is None:
            self._refit_source = GLib.idle_add(self.refit_popup)

    def refit_popup(self):
        """Remap after a real height-cap change: GTK caches mapped popover allocation."""
        self._refit_source = None
        if not self.popover.get_visible():
            return False
        focus = self.get_toplevel().get_focus()
        self._refitting = True
        try:
            self.popover.hide()
            self.popover.popup()
            Gtk.ToggleButton.set_active(self, True)
            if focus is not None:
                focus.grab_focus()
        finally:
            self._refitting = False
        return False

    def opened(self, *_):
        if self._refitting:
            return
        self.fit_popup()
        self.search.set_text('')
        self.popover.get_child().show_all()
        self.filter_rows()
        self.search.grab_focus()

    def set_choices(self, choices):
        self.remove_all()
        self.choices = list(choices)
        previous = None
        for index, choice in enumerate(self.choices):
            kind = choice.get('kind')
            group = {'attach': 'Running models', 'provider': 'Installed agents', 'local': 'Local model library'}.get(kind, 'MODELS')
            row = Gtk.ListBoxRow()
            row.choice_index = index
            row.search_text = ' '.join(str(choice.get(k) or '') for k in
                                      ('display_name', 'label', 'path', 'format', 'quantization', 'location')).casefold()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_border_width(9)
            if group != previous:
                box.pack_start(label(group, 'kicker'), False, False, 0)
            previous = group
            name = label(choice.get('display_name') or choice['label'].split(' · ')[0], 'model-name')
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(58)
            box.pack_start(name, False, False, 0)
            description = details(choice) or choice.get('note', '')
            if not choice.get('ready', False):
                description = 'Setup needed · ' + description
            meta = label(description, 'muted')
            meta.set_ellipsize(Pango.EllipsizeMode.END)
            meta.set_max_width_chars(65)
            box.pack_start(meta, False, False, 0)
            row.add(box)
            row.set_tooltip_text(choice.get('path') or choice.get('note'))
            self.list.add(row)
        self.filter_rows()

    def filter_rows(self, *_):
        query = self.search.get_text().casefold().strip()
        visible = []
        for row in self.list.get_children():
            matched = all(term in row.search_text for term in query.split())
            row.set_visible(matched)
            if matched:
                visible.append(row)
        self.empty.set_visible(not visible)
        self.list.select_row(visible[0] if visible else None)

    def search_key(self, _, event):
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            row = self.list.get_selected_row()
            if row:
                row.grab_focus()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self.list.get_selected_row()
            if row:
                self.activate_row(self.list, row)
            return True
        return False

    def activate_row(self, _, row):
        self.set_active(row.choice_index)
        self.popover.popdown()
        self.grab_focus()

    def get_active(self):
        return self.active

    def get_active_text(self):
        return self.choices[self.active]['label'] if self.active >= 0 else None

    def set_active(self, index):
        self.active = index if 0 <= index < len(self.choices) else -1
        choice = self.choices[self.active] if self.active >= 0 else {}
        self.title.set_text(choice.get('display_name') or choice.get('label', 'Choose an agent or model').split(' · ')[0])
        self.subtitle.set_text(details(choice) or choice.get('note', 'Search your model library'))
        self.emit('changed')

    def remove_all(self):
        for row in self.list.get_children():
            self.list.remove(row)
        self.choices, self.active = [], -1
