"""Native engine/workspace selection. All model inspection runs in Dream's venv."""
from __future__ import annotations

import json
import subprocess
import threading
import traceback
from pathlib import Path
from gi.repository import GLib, Gtk, Pango

from .browser import label
from .protocol import recommended_card, refill

# The field names the Advanced grid shows where `name.replace('_', ' ')` would not read well.
FIELD_LABELS = {'temperature': 'Temperature', 'top_k': 'Top-k', 'top_p': 'Top-p', 'min_p': 'Min-p',
                'repeat_penalty': 'Repetition penalty', 'presence_penalty': 'Presence penalty',
                'frequency_penalty': 'Frequency penalty', 'seed': 'Seed', 'stop': 'Stop sequences',
                'prompt_cache': 'Prompt cache', 'thinking': 'Thinking', 'reasoning_effort': 'Reasoning effort',
                'context_overflow': 'Context overflow', 'ctx': 'Context · tokens', 'gpus': 'GPU count',
                'slot_ctx': 'Sub-agent context · tokens', 'parallel': 'Parallel requests',
                'prefill_chunk': 'Prefill chunk · tokens', 'max_tokens': 'Max output · tokens',
                'speculative': 'Speculative decoding', 'spec_k': 'Draft tokens per step',
                'repeat_last_n': 'Repetition window · tokens', 'threads': 'CPU threads'}


def wrapped(text, style='', chars=46):
    """A label that wraps at about `chars` characters and starts at the left. Its minimum width is that too (or its
    text's, when shorter), so a grid or box asks for the height it really takes: a wrapping label measured at its
    narrowest makes the screen leave a tall empty band."""
    widget = label(text, style)
    widget.set_line_wrap(True)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(chars)
    widget.set_width_chars(min(16, len(text)))
    return widget


class Onboarding(Gtk.Box):
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.owner = owner
        self.load_only = False   # opened by the sidebar's Load model: local models, loaded without a new conversation
        self.choices = []
        self.settings = None
        self.serial = 0
        self.fields = {}
        self.saved_council = {}
        self.advisor_fields = {}
        self.advisor_effort_fields = {}
        self.council_choices = []
        self.get_style_context().add_class('empty')
        self.set_halign(Gtk.Align.START)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.FILL)
        self.set_vexpand(True)
        self.set_size_request(360, -1)
        self.connect('size-allocate', self.adapt_layout)
        hero = Gtk.Box(spacing=24)
        headings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        headings.pack_start(label('YOUR WORKSPACE, YOUR MODEL', 'kicker'), False, False, 0)
        self.headline = label('Start a conversation', 'headline')
        headings.pack_start(self.headline, False, False, 0)
        introduction = wrapped('Choose an agent or a model on this computer. Make it yours, then start.', 'description', 65)
        headings.pack_start(introduction, False, False, 0)
        hero.pack_start(headings, True, True, 0)
        self.pack_start(hero, False, False, 0)
        row = Gtk.Box(spacing=8)
        from .model_chooser import ModelChooser
        self.engines = ModelChooser()
        self.engines.set_hexpand(True)
        self.engines.connect('changed', self.selected)
        row.pack_start(self.engines, True, True, 0)
        self.refresh = Gtk.Button(label='Refresh')
        self.refresh.connect('clicked', lambda _: self.load_catalog())
        row.pack_end(self.refresh, False, False, 0)
        self.pack_start(label('Agent or local model'), False, False, 0)
        self.pack_start(row, False, False, 0)
        library_actions = Gtk.Box(spacing=8)
        self.browse_file = Gtk.Button(label='Choose GGUF file…')
        self.browse_folder = Gtk.Button(label='Choose model folder…')
        self.browse_file.connect('clicked', lambda _: self.browse_model(False))
        self.browse_folder.connect('clicked', lambda _: self.browse_model(True))
        library_actions.pack_start(self.browse_file, False, False, 0)
        library_actions.pack_start(self.browse_folder, False, False, 0)
        self.pack_start(library_actions, False, False, 0)
        self.locations = Gtk.Expander(label='Library locations & discovery')
        self.location_text = wrapped('Refresh checks configured model locations and mounted drives. Choosing a file reads local metadata; nothing is uploaded.', 'muted', 92)
        self.locations.add(self.location_text)
        self.pack_start(self.locations, False, False, 0)
        self.file_details = Gtk.Expander(label='Selected model details')
        self.file_text = wrapped('', 'muted', 90)
        self.file_text.set_selectable(True)
        self.file_details.add(self.file_text)
        self.pack_start(self.file_details, False, False, 0)
        self.note = label('', 'muted')
        self.note.set_line_wrap(True)
        self.note.set_max_width_chars(48)
        self.pack_start(self.note, False, False, 0)
        self.main_model = Gtk.Entry(placeholder_text='Blank uses the provider default')
        self.main_model.get_accessible().set_name('Main model')
        self.main_model_label = label('Model override (optional)')
        self.pack_start(self.main_model_label, False, False, 0)
        self.pack_start(self.main_model, False, False, 0)
        self.main_effort = Gtk.ComboBoxText()
        self.main_effort.get_accessible().set_name('Main reasoning effort')
        self.main_effort_label = label('Main reasoning effort')
        self.pack_start(self.main_effort_label, False, False, 0)
        self.pack_start(self.main_effort, False, False, 0)
        self.effort_note = label('', 'muted')
        self.effort_note.set_line_wrap(True)
        self.effort_note.set_max_width_chars(48)
        self.pack_start(self.effort_note, False, False, 0)
        self.council = Gtk.Expander(label='Council advisors (optional)')
        self.council_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.council_box.get_style_context().add_class('council-startup')
        help_text = label('Enable advisors; they act only within Dream\'s permission mode. Selecting one does not contact it or load weights.', 'muted')
        help_text.set_line_wrap(True)
        self.council_box.pack_start(help_text, False, False, 0)
        self.advisor_grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.council_box.pack_start(self.advisor_grid, False, False, 0)
        self.council.add(self.council_box)
        self.pack_start(self.council, False, False, 0)
        self.pack_start(label('Workspace folder'), False, False, 0)
        self.folder = Gtk.FileChooserButton(title='Choose your workspace', action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.folder.set_current_folder(owner.cwd)
        self.folder.set_filename(owner.cwd)
        self.pack_start(self.folder, False, False, 0)
        self.summary = label('', 'description')
        self.summary.set_line_wrap(True)
        self.summary.set_max_width_chars(48)
        self.pack_start(self.summary, False, False, 0)
        # DREAM-179: the model's mode and effort, with the card's recommended sampling per mode. Both drive the
        # Advanced thinking / reasoning_effort rows, which stay the launch's source of truth.
        self.modes = self.active_mode = None
        self.mode_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.mode_row = row = Gtk.Box(spacing=8)
        self.mode_think = Gtk.RadioButton(label='Thinking', draw_indicator=False)
        self.mode_instruct = Gtk.RadioButton(label='Instruct', group=self.mode_think, draw_indicator=False)
        self.mode_think.connect('toggled', self.mode_toggled)
        self.mode_effort = Gtk.ComboBoxText()
        self.mode_effort.get_accessible().set_name('Model reasoning effort')
        self.mode_effort.connect('changed', lambda f: self.mirror('reasoning_effort', f))
        self.mode_label, self.effort_label = label('Mode'), label('Effort')
        for widget in (self.mode_label, self.mode_think, self.mode_instruct, self.effort_label, self.mode_effort):
            row.pack_start(widget, False, False, 0)
        self.mode_box.pack_start(row, False, False, 0)
        self.advanced = Gtk.Expander(label='Advanced model settings')
        self.grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        self.advanced.add(self.grid)
        # The owner's plan of 2026-10-01: the model card's recommendations as a card to the RIGHT of the mode row and
        # the Advanced fields (render_card), with the load screen's notes, instead of small lines above and below them.
        self.model_area = Gtk.Box(spacing=28)
        self.fields_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.fields_column.pack_start(self.mode_box, False, False, 0)
        self.fields_column.pack_start(self.advanced, False, False, 0)
        self.model_area.pack_start(self.fields_column, True, True, 0)
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.card.get_style_context().add_class('rec-card')
        self.card.set_valign(Gtk.Align.START)
        self.card.set_size_request(330, -1)
        self.model_area.pack_start(self.card, True, True, 0)
        self.pack_start(self.model_area, False, False, 0)
        actions = Gtk.Box(spacing=10)
        actions.get_style_context().add_class("launch-actions")
        self.actions = actions
        self.start = Gtk.Button(label='Start conversation')
        self.start.get_style_context().add_class('suggested-action')
        self.start.connect('clicked', self.launch)
        actions.pack_start(self.start, False, False, 0)
        self.reset = Gtk.Button(label='Use recommendations')
        self.reset.connect('clicked', self.reset_settings)
        actions.pack_start(self.reset, False, False, 0)
        terminal = Gtk.Button(label='Open Terminal')
        terminal.connect('clicked', lambda _: owner.open_terminal())
        actions.pack_start(terminal, False, False, 0)
        self.pack_start(actions, False, False, 0)
        self.reorder_child(actions, self.get_children().index(self.model_area))
        self.message = label('Finding installed agents and local models…', 'description')
        self.message.set_line_wrap(True)
        self.message.set_max_width_chars(48)
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.START)
        status = Gtk.Box(spacing=10)
        status.pack_start(self.spinner, False, False, 0)
        status.pack_start(self.message, True, True, 0)
        self.pack_start(status, False, False, 0)
        status.show_all()
        status.set_no_show_all(True)
        self.message.connect('notify::label', lambda *_: status.set_visible(bool(self.message.get_text())))
        self.reorder_child(status, self.get_children().index(self.locations))
        self.start.set_sensitive(False)
        self.file_details.set_no_show_all(True)
        self.file_text.show()

    def do_get_preferred_width(self):
        minimum, _natural = Gtk.Box.do_get_preferred_width(self)
        return minimum, max(minimum, 1190)

    def adapt_layout(self, _widget, allocation):
        if hasattr(self, 'model_area'):
            narrow = allocation.width < 960
            self.model_area.set_orientation(Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)
            self.fields_column.set_size_request(-1, -1)
            self.actions.set_orientation(Gtk.Orientation.VERTICAL if allocation.width < 620 else Gtk.Orientation.HORIZONTAL)

    def browse_model(self, folder=False):
        dialog = Gtk.FileChooserDialog(title='Choose model folder' if folder else 'Choose GGUF model',
                                       transient_for=self.get_toplevel(),
                                       action=Gtk.FileChooserAction.SELECT_FOLDER if folder else Gtk.FileChooserAction.OPEN)
        dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Choose', Gtk.ResponseType.ACCEPT)
        if not folder:
            models = Gtk.FileFilter()
            models.set_name('GGUF models')
            models.add_pattern('*.gguf')
            models.add_pattern('*.GGUF')
            dialog.add_filter(models)
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response == Gtk.ResponseType.ACCEPT and path:
            self.message.set_text('Reading local model metadata…')
            self.query('inspect', self.browsed_model, path)

    def browsed_model(self, data):
        choice = data['choice']
        found = next((i for i, row in enumerate(self.choices) if row.get('path') == choice['path']), None)
        if found is None:
            self.choices.append(choice)
            found = len(self.choices) - 1
        else:
            self.choices[found] = choice
        self._browsed_settings = (choice['path'], data['settings'])
        self.engines.set_choices(self.choices)
        self.engines.set_active(found)

    def query(self, action, callback, *args):
        self.serial += 1
        serial = self.serial
        self.start.set_sensitive(False)
        self.spinner.start()
        def work():
            try:
                result = subprocess.run([self.owner.python, '-m', 'dream.desktop.startup', action, *args],
                                        cwd=self.owner.cwd, env=self.owner.child_env(), capture_output=True,
                                        text=True, timeout=120)
                data = json.loads(result.stdout)
                if result.returncode and 'error' not in data:
                    data = {'error': result.stderr[-1500:] or 'Could not inspect model settings.'}
            except Exception as exc:
                data = {'error': str(exc)}
            def finish():
                if self.owner.stopped or serial != self.serial:
                    return False
                self.spinner.stop()
                self.engines.set_sensitive(True)
                if data.get('error'):
                    self.message.set_text(data['error'] + ' Refresh to retry.')
                else:
                    try:
                        callback(data)
                    except Exception:
                        self.inspection_failed()
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def load_catalog(self):
        self.message.set_text('Refreshing local model locations… No weights are loaded or uploaded.')
        self.engines.set_sensitive(False)
        self.query('catalog', self.catalog_loaded)

    def set_load_only(self, on):
        """The sidebar's Load model (2026-10-10): this selector loads a local model for the open session and its
        terminals, and starts no conversation."""
        self.load_only = bool(on)
        self.headline.set_text('Load a model' if self.load_only else 'Start a conversation')
        self.council.set_visible(not self.load_only)

    def catalog_loaded(self, data):
        self.saved_council = data.get('saved_council') or {}
        self.council_choices = data.get('council_choices', [])
        self.fill_advisors()
        old_path = self.choices[self.engines.get_active()].get('id') if 0 <= self.engines.get_active() < len(self.choices) else None
        self.choices = ([c for c in data['choices'] if c['kind'] == 'local'] if getattr(self, 'load_only', False)
                        else data['choices'])
        self.engines.set_choices(self.choices)
        discovery = data.get('discovery') or {}
        if discovery:
            self.location_text.set_text('\n\n'.join(str(discovery.get(key, '')) for key in ('scope', 'refresh', 'privacy')) + '\n\n' + '\n'.join(str(path) for path in discovery.get('roots', [])))
        preferred = next((i for i, c in enumerate(self.choices) if c['ready']), -1)
        if old_path:
            preferred = next((i for i, c in enumerate(self.choices) if c.get('id') == old_path), preferred)
        self.engines.set_active(preferred)
        if preferred == -1:
            self.message.set_text('No ready agent found. Set up a provider or add GGUF models, then refresh.')

    def fill_advisors(self):
        for child in self.advisor_grid.get_children():
            self.advisor_grid.remove(child)
        self.advisor_fields = {}
        self.advisor_effort_fields = {}
        saved_models = self.saved_council.get('advisor_models', {})
        for i, choice in enumerate(self.council_choices):
            key = choice['key']
            enabled = Gtk.CheckButton(label=choice['label'])
            enabled.get_accessible().set_name(f"Enable {choice['label']} advisor")
            enabled.set_active(key in self.saved_council.get('advisors', []))
            model = Gtk.Entry(placeholder_text='Provider default model')
            model.set_text(saved_models.get(key, ''))
            model.get_accessible().set_name(f"{choice['label']} advisor model")
            enabled.set_tooltip_text(choice['note'])
            model.set_tooltip_text(choice['note'])
            effort = Gtk.ComboBoxText()
            effort.get_accessible().set_name(f"{choice['label']} advisor reasoning effort")
            self.fill_effort(effort, choice, self.saved_council.get('advisor_efforts', {}).get(key))
            effort.set_tooltip_text(choice.get('effort_note', ''))
            self.advisor_effort_fields[key] = effort
            self.advisor_fields[key] = (enabled, model)
            enabled.connect('toggled', self.advisors_changed)
            self.advisor_grid.attach(enabled, 0, i * 2, 1, 1)
            self.advisor_grid.attach(model, 1, i * 2, 1, 1)
            self.advisor_grid.attach(effort, 2, i * 2, 1, 1)
            note = label(choice['note'] + ' ' + choice.get('effort_note', ''), 'muted')
            note.set_line_wrap(True)
            note.set_max_width_chars(68)
            self.advisor_grid.attach(note, 0, i * 2 + 1, 3, 1)
        self.council_box.show_all()

    @staticmethod
    def fill_effort(field, choice, saved=None):
        field.remove_all()
        field.append('', 'Provider default effort')
        levels = choice.get('efforts', [])
        for effort in levels:
            field.append(effort, effort)
        if saved and saved not in levels:
            field.append(saved, f'{saved} (saved, support unverified)')
        field.set_active_id(saved or '')

    def advisors_changed(self, *_):
        for choice in self.council_choices:
            toggle, model = self.advisor_fields[choice['key']]
            toggle.set_sensitive(choice['available'] or toggle.get_active())
            model.set_sensitive(toggle.get_active())
            self.advisor_effort_fields[choice['key']].set_sensitive(
                toggle.get_active() and bool(choice.get('efforts')
                    or self.saved_council.get('advisor_efforts', {}).get(choice['key'])))
        count = sum(toggle.get_active() for toggle, _ in self.advisor_fields.values())
        self.council.set_label(f'Council advisors (optional, {count} enabled)')

    def selected(self, *_):
        self.serial += 1
        self.spinner.stop()
        self.engines.set_sensitive(True)
        self.settings = None
        self.advanced.hide()
        self.reset.hide()
        self.mode_box.hide()
        self.card.hide()
        self.summary.set_text('')
        index = self.engines.get_active()
        if not 0 <= index < len(self.choices):
            self.start.set_sensitive(False)
            return
        choice = self.choices[index]
        for widget in (self.main_model_label, self.main_model):
            widget.set_visible(choice['kind'] == 'provider')
        self.file_details.set_visible(choice['kind'] == 'local')
        self.file_text.set_text(choice.get('path', '') + ('\nIdentity from ' + choice['metadata_source'] if choice.get('metadata_source') else ''))
        self.main_model.set_text(choice.get('model') or '')
        self.main_model.set_sensitive(choice['kind'] == 'provider')
        self.main_model.set_placeholder_text('Selected local weights' if choice['kind'] == 'local'
                                             else 'Blank uses the provider default')
        metadata = next((c for c in self.council_choices if c['key'] == choice['provider']), {})
        saved_effort = (self.saved_council.get('orchestrator_effort')
                        if self.saved_council.get('orchestrator') == choice['provider'] else None)
        self.fill_effort(self.main_effort, metadata, saved_effort if choice['kind'] == 'provider' else None)
        self.main_effort.set_sensitive(choice['kind'] == 'provider' and bool(metadata.get('efforts') or saved_effort))
        for widget in (self.main_effort_label, self.main_effort, self.effort_note):   # local: Mode/Effort below
            widget.set_visible(choice['kind'] != 'local')
        self.effort_note.set_text('Choose local reasoning effort in Advanced model settings below.'
                                 if choice['kind'] == 'local' else 'Uses the running model\'s current effort.'
                                 if choice['kind'] == 'attach' else metadata.get('effort_note', ''))
        self.advisors_changed()
        self.note.set_text(choice['note'])
        self.message.set_text('')
        self.start.set_sensitive(choice['ready'])
        self.start.set_label('Load model' if getattr(self, 'load_only', False) else
                             'Connect to running model' if choice['kind'] == 'attach' else 'Start conversation')
        if choice['kind'] == 'local':
            self.message.set_text('Reading recommendations and saved settings. No weights are loaded yet…')
            cached = getattr(self, '_browsed_settings', None)
            self._browsed_settings = None
            if cached and cached[0] == choice['path']:
                self.settings_loaded(cached[1])
            else:
                self.query('settings', self.settings_loaded, choice['path'])

    def inspection_failed(self):
        """A failed UI callback must not leave a partial form that can launch."""
        traceback.print_exc()
        self.settings = None
        self.spinner.stop()
        self.engines.set_sensitive(True)
        self.start.set_sensitive(False)
        self.summary.set_text('')
        for widget in (self.advanced, self.reset, self.mode_box, self.card):
            widget.hide()
        self.message.set_text('Could not display model information. Refresh to retry.')

    def settings_loaded(self, data):
        # Browsed files reuse their inspected payload directly; protect that path
        # as well as the asynchronous query callback.
        self.start.set_sensitive(False)
        try:
            self.settings = data
            self.fill_settings(data['selection'])
            self.fill_mode()
            self.summary.set_text(f"Context {data['selection']['ctx']:,} · GPUs {data['selection']['gpus'] or 'auto'}\n"
                                  + data['sources']['ctx'])
            self.message.set_text('')                 # the notes are in the card (render_card)
            self.show_fields()
            self.reset.show()
            self.start.set_sensitive(True)
            self.start.set_label('Load model' if getattr(self, 'load_only', False) else 'Load model and start')
        except Exception:
            self.inspection_failed()

    def fill_settings(self, selection):
        for child in self.grid.get_children():
            self.grid.remove(child)
        self.fields, self.source_labels = {}, {}
        controls = [dict(name='ctx', kind='int', hint='Conversation context length'),
                    dict(name='gpus', kind='int', hint='GPU count; default = automatic'), *self.settings['controls']]
        for row, control in enumerate(controls):
            name = control['name']
            value = selection.get(name) if name in ('gpus', 'ctx') else selection['options'][name]
            choices = list(control.get('choices') or [])
            if control['kind'] == 'bool':
                choices = ['on', 'off'] + (['default'] if value is None else [])
            if choices:
                field = Gtk.ComboBoxText()
                for option in choices:
                    field.append_text(option)
                shown = 'default' if value is None else 'on' if value is True else 'off' if value is False else value
                field.set_active(choices.index(shown) if shown in choices else 0)
            else:
                field = Gtk.Entry()
                field.set_text('default' if value is None else json.dumps(value) if isinstance(value, list) else str(value))
            field.set_tooltip_text(control['hint'])
            field.get_accessible().set_name(FIELD_LABELS.get(name, name.replace('_', ' ').capitalize()))
            self.grid.attach(label(FIELD_LABELS.get(name, name.replace('_', ' ')), 'field-name'), 0, row, 1, 1)
            self.grid.attach(field, 1, row, 1, 1)
            text = self.settings['sources'].get(name, control.get('source', ''))
            # The card names the model card's page once; a row says where its value came from in a word or two.
            source = wrapped('Model card' if text.startswith('Model card · ') else text, 'source', 34)
            self.fields[name], self.source_labels[name] = (field, control), source
            field.set_tooltip_text(control['hint'] + '\n' + text)
            field.set_hexpand(True)
            if isinstance(field, Gtk.Entry):
                field.set_width_chars(12)
            field.connect('changed', lambda *_: self.refresh_facts())

    def show_fields(self):
        """The Advanced fields, shown; their column keeps their width with Advanced closed, so the card beside it stays
        where it is."""
        self.advanced.show_all()
        self.fields_column.set_size_request(-1, -1)

    def fill_mode(self):
        self.modes = self.settings.get('modes')
        thinking, effort = self.fields.get('thinking'), self.fields.get('reasoning_effort')
        self.active_mode = None
        if not self.modes or not thinking:
            self.mode_box.hide()        # DREAM-180: no Mode/Effort row; the card says when there is no recommendation
            self.render_card()
            return
        self.mode_effort.remove_all()
        for choice in (effort[1]['choices'] if effort else []):
            self.mode_effort.append_text(choice)
        thinking[0].connect('changed', self.thinking_changed)
        if effort:
            effort[0].connect('changed', lambda f: self.mode_effort.set_active(f.get_active()))
            self.mode_effort.set_active(effort[0].get_active())
        self.mode_box.show_all()
        for widget in (self.mode_label, self.mode_think, self.mode_instruct):
            widget.set_visible(bool(self.modes['instruct']))       # GLM: effort only; its thinking-off is unofficial
        self.mode_instruct.set_label(self.modes['instruct']['label'] if self.modes['instruct'] else 'Instruct')
        self.effort_label.set_visible(bool(effort))
        self.mode_effort.set_visible(bool(effort))
        self.thinking_changed(thinking[0])

    def mirror(self, name, source):
        if name in self.fields and source.get_active() >= 0:
            self.fields[name][0].set_active(source.get_active())

    def mode_toggled(self, button):
        if self.modes and self.modes['instruct'] and 'thinking' in self.fields:
            choices = self.fields['thinking'][0].get_model()
            wanted = 'on' if button.get_active() else 'off'
            self.fields['thinking'][0].set_active(next(i for i, row in enumerate(choices) if row[0] == wanted))

    def thinking_changed(self, field):
        mode = 'instruct' if field.get_active_text() == 'off' and self.modes['instruct'] else 'thinking'
        if self.active_mode and mode != self.active_mode:     # re-seed the sampling rows the user has not edited
            current = {}
            for name in self.modes[mode]['values']:
                try:
                    current[name] = float(self.fields[name][0].get_text())
                except (KeyError, ValueError):
                    pass
            for name, value in refill(current, self.modes[self.active_mode]['values'], self.modes[mode]['values']).items():
                self.fields[name][0].set_text(str(value))
                self.source_labels[name].set_text('Model card (mode switch)')
                self.fields[name][0].set_tooltip_text(self.fields[name][1]['hint'] + '\nModel card (mode switch)')
        if self.active_mode:
            self.source_labels['thinking'].set_text('Mode: ' + (self.modes[mode]['label'] if field.get_active_text() != 'off'
                                                            or mode == 'instruct' else 'thinking off (unofficial)'))
        self.active_mode = mode
        (self.mode_instruct if mode == 'instruct' else self.mode_think).set_active(True)
        self.mode_effort.set_sensitive(field.get_active_text() != 'off')
        self.render_card()

    def refresh_facts(self):
        if not hasattr(self, 'facts_box') or not self.settings:
            return
        facts = dict(self.settings.get('facts') or {})
        facts.setdefault('context_limit', self.settings.get('context_limit'))
        facts['speculative'] = dict(facts.get('speculative') or {})
        for field_name, key in (('ctx', 'configured_context'), ('spec_k', 'count'), ('speculative', 'enabled')):
            if field_name not in self.fields:
                continue
            field, _ = self.fields[field_name]
            try:
                value = (None if field.get_active_text() == 'default' else field.get_active_text() == 'on') if field_name == 'speculative' else int(field.get_text())
                (facts if field_name == 'ctx' else facts['speculative'])[key] = value
            except ValueError:
                pass
        if 'ctx' in self.fields and 'gpus' in self.fields:
            try:
                context = int(self.fields['ctx'][0].get_text())
                gpu = self.fields['gpus'][0].get_text()
                origin = (self.settings['sources'].get('ctx', '') if context == self.settings['selection']['ctx']
                          else 'Custom context for this launch')
                self.summary.set_text(f"Context {context:,} · GPUs {'auto' if gpu == 'default' else gpu}\n{origin}")
            except ValueError:
                pass
        data = recommended_card(self.modes, [], self.active_mode, facts=facts)
        for child in self.facts_box.get_children():
            self.facts_box.remove(child)
        for name, value in data.get('facts', []):
            row = Gtk.Box(spacing=16)
            key = wrapped(name, 'rec-key', 28)
            val = wrapped(str(value), 'rec-fact-value', 30)
            row.pack_start(key, True, True, 0)
            row.pack_start(val, True, True, 0)
            self.facts_box.pack_start(row, False, False, 0)
        self.facts_box.show_all()

    def render_card(self):
        """The Recommended settings card, from protocol.recommended_card: per mode its use and values (the mode in use
        marked), the reasoning efforts, the source, and the load screen's notes."""
        for child in self.card.get_children():
            self.card.remove(child)
        data = recommended_card(self.modes, (self.settings or {}).get('notes'), self.active_mode)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        head.pack_start(label(data['title'], 'rec-title'), False, False, 0)
        if data['source']:
            head.pack_start(wrapped(data['source'], 'rec-source', 64), False, False, 0)
        self.card.pack_start(head, False, False, 0)
        self.facts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.card.pack_start(self.facts_box, False, False, 0)
        self.refresh_facts()
        current = next((mode for mode in data['modes'] if mode['active']), None)
        if current:
            self.card.pack_start(wrapped(current['name'] + ' · ' + current['when'], 'rec-when', 60), False, False, 0)
        details = Gtk.Expander(label='Sampling, reasoning & model notes')
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        details.add(detail_box)
        self.card.pack_start(details, False, False, 0)
        if data['empty']:
            self.card.pack_start(wrapped(data['empty'], 'rec-text'), False, False, 0)
        for mode in data['modes']:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.get_style_context().add_class('rec-mode')
            if mode['active']:
                box.get_style_context().add_class('active')
            top = Gtk.Box(spacing=10)
            top.pack_start(label(mode['name'].upper(), 'rec-mode-name'), False, False, 0)
            if mode['active']:
                pill = label('In use', 'rec-pill')
                pill.set_valign(Gtk.Align.CENTER)
                top.pack_start(pill, False, False, 0)
            box.pack_start(top, False, False, 0)
            if mode['when']:
                box.pack_start(wrapped(mode['when'][0].upper() + mode['when'][1:], 'rec-when', 50), False, False, 0)
            values = Gtk.Grid(column_spacing=28, row_spacing=5)
            for row, (name, value) in enumerate(mode['rows']):
                values.attach(label(name, 'rec-key'), 0, row, 1, 1)
                values.attach(wrapped(value, 'rec-value', 26), 1, row, 1, 1)
            box.pack_start(values, False, False, 0)
            if mode['unset']:
                box.pack_start(wrapped('Not on the card, so off: ' + ', '.join(mode['unset']).lower() + '.',
                                       'rec-text', 50), False, False, 0)
            detail_box.pack_start(box, False, False, 0)
        for title, rows in (('Reasoning effort', data['effort']), ('Notes', [['•', note] for note in data['notes']])):
            if not rows:
                continue
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            section.pack_start(label(title, 'rec-heading'), False, False, 0)
            grid = Gtk.Grid(column_spacing=12, row_spacing=6)
            for row, (name, text) in enumerate(rows):
                key = label(name, 'rec-level')
                key.set_valign(Gtk.Align.START)
                grid.attach(key, 0, row, 1, 1)
                grid.attach(wrapped(text, 'rec-text', 50), 1, row, 1, 1)
            section.pack_start(grid, False, False, 0)
            detail_box.pack_start(section, False, False, 0)
        self.card.show_all()

    def reset_settings(self, *_):
        if self.settings:
            self.settings['sources'] = self.settings['recommended_sources']
            self.fill_settings(self.settings['recommended'])
            self.fill_mode()
            rec = self.settings['recommended']
            self.summary.set_text(f"Context {rec['ctx']:,} · GPUs {rec['gpus'] or 'auto'}\n" + self.settings['sources']['ctx'])
            self.message.set_text('Recommendations restored. Saved settings change only after a successful load.')
            self.show_fields()

    def launch(self, *_):
        if self.owner.running and not getattr(self, 'load_only', False):
            return
        index = self.engines.get_active()
        if not 0 <= index < len(self.choices):
            return
        request = dict(choice=dict(self.choices[index]), workspace=self.folder.get_filename() or self.owner.cwd)
        try:
            if request['choice']['kind'] == 'provider':
                request['choice']['model'] = self.main_model.get_text().strip() or None
            advisors = [key for key, (toggle, _) in self.advisor_fields.items() if toggle.get_active()]
            council = {**self.saved_council, 'orchestrator': request['choice']['provider'],
                       'advisors': advisors, 'advisor_models': {
                           key: field.get_text().strip() for key, (_, field) in self.advisor_fields.items()
                           if key in advisors and field.get_text().strip()}}
            # Default omits an override. Saved advanced limits remain intact.
            council['orchestrator_effort'] = self.main_effort.get_active_id() or None
            council['advisor_efforts'] = {
                key: field.get_active_id() for key, field in self.advisor_effort_fields.items()
                if key in advisors and field.get_active_id()}
            request['council'] = council
            if request['choice']['kind'] == 'local':
                if self.settings is None:
                    self.message.set_text('Wait for model settings before starting.')
                    return
                selection = {'options': {}}
                for name, (field, control) in self.fields.items():
                    raw = field.get_active_text() if isinstance(field, Gtk.ComboBoxText) else field.get_text().strip()
                    try:
                        value = (control.get('default') if raw == 'default' else raw == 'on' if control['kind'] == 'bool'
                                 else int(raw) if control['kind'] == 'int' else float(raw) if control['kind'] == 'float'
                                 else json.loads(raw) if control['kind'] == 'stop' else raw)
                    except (ValueError, TypeError):
                        title = FIELD_LABELS.get(name, name.replace('_', ' ').title())
                        expected = {'int': 'a whole number', 'float': 'a number',
                                    'stop': 'a list of stop sequences, such as ["END"]'}.get(control['kind'], 'a valid value')
                        self.advanced.set_expanded(True)
                        field.grab_focus()
                        raise ValueError(f'Enter {expected} for {title}.') from None
                    (selection if name in ('ctx', 'gpus') else selection['options'])[name] = value
                request.update(selection=selection, identity=self.settings['identity'], revision=self.settings['revision'])
            if getattr(self, 'load_only', False):
                if request['choice']['kind'] == 'local':
                    self.owner.load_model(request)
                return
            self.owner.launch_selection(request)
        except (ValueError, TypeError) as exc:
            self.message.set_text(f'Check the advanced settings: {exc}')

    def set_running(self, running):
        self.start.set_sensitive(not running)
        self.engines.set_sensitive(not running)
        self.refresh.set_sensitive(not running)
        self.browse_file.set_sensitive(not running)
        self.browse_folder.set_sensitive(not running)
        self.folder.set_sensitive(not running)
        self.advanced.set_sensitive(not running)
        self.mode_box.set_sensitive(not running)
        self.council.set_sensitive(not running)
        index = self.engines.get_active()
        self.main_model.set_sensitive(not running and 0 <= index < len(self.choices)
                                      and self.choices[index]['kind'] == 'provider')
        main = self.choices[index] if 0 <= index < len(self.choices) else {}
        metadata = next((c for c in self.council_choices if c['key'] == main.get('provider')), {})
        self.main_effort.set_sensitive(not running and main.get('kind') == 'provider'
                                       and bool(metadata.get('efforts') or self.main_effort.get_active_id()))
        self.reset.set_sensitive(not running)
        self.spinner.start() if running else self.spinner.stop()
