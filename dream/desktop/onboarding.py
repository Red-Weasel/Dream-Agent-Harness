"""Native engine/workspace selection. All model inspection runs in Dream's venv."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from gi.repository import GLib, Gtk, Pango

from .browser import label


class Onboarding(Gtk.Box):
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.owner = owner
        self.choices = []
        self.settings = None
        self.serial = 0
        self.fields = {}
        self.saved_council = {}
        self.advisor_fields = {}
        self.advisor_effort_fields = {}
        self.council_choices = []
        self.get_style_context().add_class('empty')
        self.set_halign(Gtk.Align.FILL)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.set_size_request(440, -1)
        self.pack_start(label('DREAM', 'kicker'), False, False, 0)
        self.pack_start(label('Start a conversation', 'headline'), False, False, 0)
        introduction = label('Choose your agent and the folder you want to work in.', 'description')
        introduction.set_line_wrap(True)
        self.pack_start(introduction, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.engines = Gtk.ComboBoxText()
        self.engines.set_hexpand(True)
        for cell in self.engines.get_cells():
            cell.set_property('ellipsize', Pango.EllipsizeMode.NONE)
            cell.set_property('wrap-mode', Pango.WrapMode.WORD_CHAR)
            cell.set_property('wrap-width', 480)
        self.engines.connect('changed', self.selected)
        row.pack_start(self.engines, True, True, 0)
        self.refresh = Gtk.Button(label='Refresh')
        self.refresh.connect('clicked', lambda _: self.load_catalog())
        row.pack_end(self.refresh, False, False, 0)
        self.pack_start(label('Agent or local model'), False, False, 0)
        self.pack_start(row, False, False, 0)
        self.note = label('', 'muted')
        self.note.set_line_wrap(True)
        self.note.set_max_width_chars(48)
        self.pack_start(self.note, False, False, 0)
        self.main_model = Gtk.Entry(placeholder_text='Blank uses the provider default')
        self.main_model.get_accessible().set_name('Main model')
        self.pack_start(label('Main model'), False, False, 0)
        self.pack_start(self.main_model, False, False, 0)
        self.main_effort = Gtk.ComboBoxText()
        self.main_effort.get_accessible().set_name('Main reasoning effort')
        self.pack_start(label('Main reasoning effort'), False, False, 0)
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
        self.advanced = Gtk.Expander(label='Advanced model settings')
        self.grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        self.advanced.add(self.grid)
        self.pack_start(self.advanced, False, False, 0)
        actions = Gtk.Box(spacing=10)
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
        self.message = label('Finding installed agents and local models…', 'description')
        self.message.set_line_wrap(True)
        self.message.set_max_width_chars(48)
        self.pack_start(self.message, False, False, 0)
        self.spinner = Gtk.Spinner()
        self.pack_start(self.spinner, False, False, 0)

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
                if data.get('error'):
                    self.message.set_text(data['error'] + ' Refresh to retry.')
                else:
                    callback(data)
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def load_catalog(self):
        self.message.set_text('Finding installed agents and local models…')
        self.query('catalog', self.catalog_loaded)

    def catalog_loaded(self, data):
        self.saved_council = data.get('saved_council') or {}
        self.council_choices = data.get('council_choices', [])
        self.fill_advisors()
        self.choices = []
        self.engines.remove_all()
        self.choices = data['choices']
        for row in self.choices:
            self.engines.append_text(row['label'] + ('' if row['ready'] else ' · setup needed'))
        preferred = next((i for i, c in enumerate(self.choices) if c['ready']), -1)
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
        self.settings = None
        self.advanced.hide()
        self.reset.hide()
        self.summary.set_text('')
        index = self.engines.get_active()
        if not 0 <= index < len(self.choices):
            self.start.set_sensitive(False)
            return
        choice = self.choices[index]
        self.main_model.set_text(choice.get('model') or '')
        self.main_model.set_sensitive(choice['kind'] == 'provider')
        self.main_model.set_placeholder_text('Selected local weights' if choice['kind'] == 'local'
                                             else 'Blank uses the provider default')
        metadata = next((c for c in self.council_choices if c['key'] == choice['provider']), {})
        saved_effort = (self.saved_council.get('orchestrator_effort')
                        if self.saved_council.get('orchestrator') == choice['provider'] else None)
        self.fill_effort(self.main_effort, metadata, saved_effort if choice['kind'] == 'provider' else None)
        self.main_effort.set_sensitive(choice['kind'] == 'provider' and bool(metadata.get('efforts') or saved_effort))
        self.effort_note.set_text('Choose local reasoning effort in Advanced model settings below.'
                                 if choice['kind'] == 'local' else 'Uses the running model\'s current effort.'
                                 if choice['kind'] == 'attach' else metadata.get('effort_note', ''))
        self.advisors_changed()
        self.note.set_text(choice['note'])
        self.message.set_text('')
        self.start.set_sensitive(choice['ready'])
        self.start.set_label('Connect to running model' if choice['kind'] == 'attach' else 'Start conversation')
        if choice['kind'] == 'local':
            self.message.set_text('Reading recommendations and saved settings. No weights are loaded yet…')
            self.query('settings', self.settings_loaded, choice['path'])

    def settings_loaded(self, data):
        self.settings = data
        self.fill_settings(data['selection'])
        self.summary.set_text(f"Context {data['selection']['ctx']:,} · GPUs {data['selection']['gpus'] or 'auto'}\n"
                              + data['sources']['ctx'])
        self.message.set_text('\n'.join(data['notes']))
        self.advanced.show_all()
        self.reset.show()
        self.start.set_sensitive(True)
        self.start.set_label('Load model and start')

    def fill_settings(self, selection):
        for child in self.grid.get_children():
            self.grid.remove(child)
        self.fields = {}
        controls = [dict(name='ctx', kind='int', hint='Conversation context length'),
                    dict(name='gpus', kind='int', hint='GPU count; default = automatic'), *self.settings['controls']]
        for row, control in enumerate(controls):
            name = control['name']
            value = selection.get(name) if name in ('gpus', 'ctx') else selection['options'][name]
            choices = control.get('choices') or (['compact', 'error'] if control['kind'] == 'overflow' else [])
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
            self.fields[name] = (field, control)
            self.grid.attach(label(name.replace('_', ' ')), 0, row, 1, 1)
            self.grid.attach(field, 1, row, 1, 1)
            self.grid.attach(label(self.settings['sources'].get(name, control.get('source', '')), 'muted'), 2, row, 1, 1)

    def reset_settings(self, *_):
        if self.settings:
            self.settings['sources'] = self.settings['recommended_sources']
            self.fill_settings(self.settings['recommended'])
            rec = self.settings['recommended']
            self.summary.set_text(f"Context {rec['ctx']:,} · GPUs {rec['gpus'] or 'auto'}\n" + self.settings['sources']['ctx'])
            self.message.set_text('Recommendations restored. Saved settings change only after a successful load.')
            self.advanced.show_all()

    def launch(self, *_):
        if self.owner.running:
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
                selection = {'options': {}}
                for name, (field, control) in self.fields.items():
                    raw = field.get_active_text() if isinstance(field, Gtk.ComboBoxText) else field.get_text().strip()
                    value = (control.get('default') if raw == 'default' else raw == 'on' if control['kind'] == 'bool'
                             else int(raw) if control['kind'] == 'int' else float(raw) if control['kind'] == 'float'
                             else json.loads(raw) if control['kind'] == 'stop' else raw)
                    (selection if name in ('ctx', 'gpus') else selection['options'])[name] = value
                request.update(selection=selection, identity=self.settings['identity'], revision=self.settings['revision'])
            self.owner.launch_selection(request)
        except (ValueError, TypeError) as exc:
            self.message.set_text(f'Check the advanced settings: {exc}')

    def set_running(self, running):
        self.start.set_sensitive(not running)
        self.engines.set_sensitive(not running)
        self.refresh.set_sensitive(not running)
        self.folder.set_sensitive(not running)
        self.advanced.set_sensitive(not running)
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
