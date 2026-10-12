"""The load screen (dream/desktop/onboarding.py) after the owner's settings plan of 2026-10-01.

- The model card's recommendations are a "Recommended settings" card to the RIGHT of the mode row and the Advanced
  fields: a heading per mode (Thinking / Instruct, as the card gives them) with its use and its values in aligned rows,
  the mode in use marked, the reasoning efforts, the source once, and the load screen's notes -- which used to be small
  lines under the buttons. "Use recommendations" still restores the recommended values.
- A lanes model's fields include parallel (default 1) and the sub-agent context, named "sub-agent context".

The card's content is protocol.recommended_card (plain data, tested here without a display). The screen itself is
rendered with real GTK when a display is there (run-sys.sh with GDK_BACKEND=broadway): the card sits to the right of
the fields, its text is readable, and with DREAM_LOAD_SCREEN_SHOTS set the screens are saved as PNGs to look at. No
model loads; nothing runs but the widget.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.desktop.protocol import recommended_card
from dream.local.model_defaults import recommend
from dream.local.settings import available_controls
from test_model_presets import gguf

HW = {'devices': [{'name': 'B70', 'vram_total_mib': 32768, 'integrated': False}] * 2, 'ram_total_gb': 256}
SAMPLING = ['temperature', 'top_k', 'top_p', 'min_p', 'repeat_penalty', 'repeat_last_n', 'presence_penalty',
            'frequency_penalty', 'seed', 'max_tokens', 'stop']
DEFAULTS = {'temperature': .7, 'top_k': 40, 'top_p': .95, 'min_p': 0., 'repeat_penalty': 1., 'repeat_last_n': 64,
            'presence_penalty': 0., 'frequency_penalty': 0., 'seed': 0, 'max_tokens': 65000, 'stop': [], 'threads': 0,
            'prefill_chunk': 2048, 'parallel': 1, 'slot_ctx': 0, 'prompt_cache': True}
QWEN38 = '<think> Reasoning effort is set to xhigh.'
MODELS = {   # (architecture, header identity, extra load controls, efforts, thinking default)
    'qwen27': ('qwen35', {'general.name': 'Qwen3.8-27B', 'tokenizer.chat_template': QWEN38},
               ['slot_ctx', 'prompt_cache'], ('low', 'medium', 'high', 'xhigh'), True),
    'qwenflash': ('qwen4exp', {'general.name': 'Qwen3.8-Flash-Next', 'tokenizer.chat_template': QWEN38},
                  ['slot_ctx', 'prompt_cache'], ('low', 'medium', 'high', 'xhigh'), True),
    'glm': ('glm5next', {'general.name': 'GLM 5.3 Flash'}, ['threads'], ('low', 'high', 'max'), True),
    'mimo': ('mimo2', {'general.name': 'MiMo-V2.6-Flash-RL'}, ['prompt_cache'], (), True),
    'plain': ('llama', {'general.name': 'Some Llama'}, [], (), True),
}
NOTES = ['Context is a starting recommendation, not a calculated memory-fit maximum. Advanced can increase it; live '
         'preflight and the engine still validate allocation.']


def settings_for(tmp_path, key):
    """What startup.model_settings gives the screen for one of MODELS, from the real recommend() and controls."""
    architecture, header, load, efforts, thinking = MODELS[key]
    path = gguf(tmp_path / f'{key}.gguf', {'general.architecture': architecture,
                                           f'{architecture}.context_length': 262144, **header})
    caps = {'architecture': architecture, 'supported': True, 'sampling': SAMPLING,
            'load': ['gpus', 'ctx', 'prefill_chunk', 'parallel', 'thinking', *load]
            + (['reasoning_effort'] if efforts else []),
            'defaults': {**DEFAULTS, 'thinking': thinking},
            'features': {'prompt_cache': True},
            'reasoning': {'effort_levels': list(efforts), 'default_effort': efforts[-1] if efforts else '',
                          'thinking_description': 'on/off; controls model thinking template'}}
    rec = recommend(path, caps, hardware=HW)
    selection = {k: rec[k] for k in ('gpus', 'ctx', 'options')}
    return dict(selection=selection, recommended=selection, sources=dict(rec['sources']),
                recommended_sources=rec['sources'], notes=rec['notes'], context_limit=rec['context_limit'],
                max_gpus=2, controls=[asdict(c) for c in available_controls(caps)], identity='fixture-key',
                revision=None, architecture=architecture, memory_policy={}, memory_planner='resident',
                capabilities=caps, modes=rec['modes'])


# --- the card's content ------------------------------------------------------------------------------------------------

def test_the_card_has_a_heading_per_mode_its_values_the_efforts_the_source_and_the_notes(tmp_path):
    data = settings_for(tmp_path, 'qwen27')
    card = recommended_card(data['modes'], data['notes'])
    assert card['title'] == 'Recommended settings'
    assert card['source'] == 'From the model card · huggingface.co/Qwen/Qwen3.8-27B'
    thinking, instruct = card['modes']
    assert (thinking['name'], thinking['active'], instruct['name'], instruct['active']) == (
        'Thinking', True, 'Instruct', False)
    assert thinking['rows'] == [['Temperature', '1.0'], ['Top-p', '0.95'], ['Top-k', '20'], ['Min-p', '0.0'],
                                ['Presence penalty', '0.0'], ['Repetition penalty', '1.0'],
                                ['Max output', '256K reasoning / 128K answer']]
    assert instruct['rows'] == [['Temperature', '0.7'], ['Top-p', '0.8'], ['Top-k', '20'], ['Min-p', '0.0'],
                                ['Presence penalty', '1.5'], ['Repetition penalty', '1.0']]
    assert 'coding' in thinking['when'] and 'quick answers' in instruct['when']
    assert [level for level, _ in card['effort']] == ['xhigh', 'high', 'medium', 'low']
    assert card['notes'] == data['notes'] and card['empty'] == ''
    assert [m['active'] for m in recommended_card(data['modes'], [], 'instruct')['modes']] == [False, True]


def test_a_model_that_always_thinks_has_one_mode_its_efforts_and_the_cards_caveat(tmp_path):
    data = settings_for(tmp_path, 'glm')
    card = recommended_card(data['modes'], data['notes'])
    [thinking] = card['modes']
    assert thinking['name'] == 'Thinking (always on)' and thinking['rows'][:2] == [['Temperature', '1.0'],
                                                                                    ['Top-p', '0.95']]
    assert thinking['unset'] == ['Top-k', 'Min-p', 'Presence penalty', 'Repetition penalty']
    assert [level for level, _ in card['effort']] == ['max', 'high', 'low']
    assert card['notes'][0] == 'Thinking off is unofficial: the model card has no non-thinking mode.'


def test_a_model_without_a_recommendation_says_so_and_keeps_its_notes(tmp_path):
    data = settings_for(tmp_path, 'plain')
    card = recommended_card(data['modes'], data['notes'])
    assert data['modes'] is None and card['modes'] == [] and card['source'] == ''
    assert card['empty'] == "This model's card gives no recommended settings, so the fields show the engine's own defaults."
    assert card['notes'] == data['notes']


def test_the_notes_leave_the_lines_under_the_buttons(onboarding_class, tmp_path):
    """settings_loaded no longer writes the notes under the buttons: the card has them."""
    panel = _mocked_panel(onboarding_class, settings_for(tmp_path, 'qwen27'))
    panel.settings_loaded(panel.settings)
    panel.message.set_text.assert_called_with('')
    assert panel.rendered[-1]['notes'] == NOTES


def test_a_lanes_models_fields_name_the_sub_agent_context(onboarding_class, tmp_path):
    data = settings_for(tmp_path, 'qwen27')
    names = [c['name'] for c in data['controls']]
    assert names[names.index('parallel') + 1] == 'slot_ctx'
    assert data['selection']['options']['parallel'] == 1 and data['selection']['options']['slot_ctx'] == 0
    assert onboarding_class.__init__.__globals__['FIELD_LABELS']['slot_ctx'] == 'Sub-agent context · tokens'


# --- the screen's logic, with GTK replaced ---------------------------------------------------------------------------

@pytest.fixture
def onboarding_class(monkeypatch):
    """The screen's controller with GTK replaced (as test_council_startup.py does), without opening a display."""
    import importlib.util
    import sys
    from types import ModuleType
    from unittest.mock import MagicMock
    gi = ModuleType('gi.repository')
    gi.Gtk = SimpleNamespace(Box=object, ComboBoxText=type('ComboBoxText', (), {}))
    gi.GLib = gi.Pango = SimpleNamespace()
    browser = ModuleType('dream.desktop.browser')
    browser.label = lambda *args: MagicMock()
    monkeypatch.setitem(sys.modules, 'gi', ModuleType('gi'))
    monkeypatch.setitem(sys.modules, 'gi.repository', gi)
    monkeypatch.setitem(sys.modules, 'dream.desktop.browser', browser)
    from dream.desktop import startup
    spec = importlib.util.spec_from_file_location('dream.desktop._onboarding_fixture',
                                                Path(startup.__file__).with_name('onboarding.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Onboarding


def _mocked_panel(cls, settings):
    from unittest.mock import MagicMock
    panel = cls.__new__(cls)
    panel.settings, panel.rendered = settings, []
    for name in ('message', 'summary', 'advanced', 'reset', 'start', 'mode_box', 'card', 'grid', 'fields_column'):
        setattr(panel, name, MagicMock())
    panel.fill_settings = lambda selection: setattr(panel, 'fields', {})
    panel.render_card = lambda: panel.rendered.append(recommended_card(panel.settings['modes'], panel.settings['notes']))
    return panel


def test_without_a_recommendation_the_mode_row_hides_and_the_card_says_so(onboarding_class, tmp_path):
    # DREAM-180 kept: an uncarded model (or an engine `recommended: null`) has no Mode/Effort row; the card says it.
    panel = _mocked_panel(onboarding_class, settings_for(tmp_path, 'plain'))
    panel.fields = {}
    panel.fill_mode()
    panel.mode_box.hide.assert_called_once()
    assert panel.rendered[-1]['empty'].startswith("This model's card gives no recommended settings")


# --- the real screen -------------------------------------------------------------------------------------------------

def _gtk():
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
    except (ImportError, ValueError):
        pytest.skip('GTK 3 bindings are not available to this Python')
    if not Gtk.init_check([])[0]:
        pytest.skip('A display is required (run-sys.sh with GDK_BACKEND=broadway draws one offscreen)')
    return Gtk


def _render(Gtk, settings, *, width, expanded=True, mode=None, shot=None):
    """The screen in an offscreen window `width` wide and as tall as it needs, a local model chosen and its settings
    read; Advanced open (or not), the Thinking / Instruct switch on `mode`. Saves a PNG of the whole screen when
    DREAM_LOAD_SCREEN_SHOTS is set."""
    from gi.repository import Gdk
    from dream.desktop import onboarding
    css = Gtk.CssProvider()
    css.load_from_path(str(Path(onboarding.__file__).with_name('style.css')))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Gtk.Settings.get_default().set_property('gtk-font-name', 'Ubuntu Sans 11')
    owner = SimpleNamespace(cwd=str(Path.home()), python='python3', stopped=False, running=False,
                            open_terminal=lambda: None, launch_selection=lambda request: None, child_env=dict)
    window = Gtk.OffscreenWindow()
    window.set_size_request(width, -1)
    panel = onboarding.Onboarding(owner)
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)          # the welcome page's box (window._welcome)
    page.pack_start(panel, True, True, 0)
    window.add(page)
    window.show_all()
    panel.advanced.hide()
    panel.reset.hide()
    panel.mode_box.hide()
    panel.card.hide()
    panel.query = lambda action, callback, *args: callback(settings)
    panel.catalog_loaded({'choices': [dict(id='m', label='Qwen3.8-27B · 28.6 GB · Data1 · text only', path='/m.gguf',
                                           kind='local', provider='machx', ready=True,
                                           note='Load local weights using saved settings or recommendations.')],
                          'council_choices': [], 'saved_council': None})
    panel.advanced.set_expanded(expanded)
    if mode == 'instruct':
        panel.mode_instruct.set_active(True)
    for _ in range(200):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    folder = os.environ.get('DREAM_LOAD_SCREEN_SHOTS')
    if folder and shot:
        Path(folder).mkdir(parents=True, exist_ok=True)
        window.get_pixbuf().savev(str(Path(folder) / f'{shot}.png'), 'png', [], [])
    return window, panel


def _box(widget, ancestor):
    x, y = widget.translate_coordinates(ancestor, 0, 0)
    allocation = widget.get_allocation()
    return x, y, allocation.width, allocation.height


def _labels(widget):
    from gi.repository import Gtk
    if isinstance(widget, Gtk.Label):
        yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from _labels(child)


@pytest.mark.parametrize('key, mode, width', [('qwen27', None, 2000), ('qwen27', 'instruct', 2000),
                                              ('glm', None, 2000), ('mimo', None, 2000), ('plain', None, 2000),
                                              ('qwen27', None, 1366)])
def test_the_card_sits_to_the_right_of_the_fields_and_reads_clearly(tmp_path, key, mode, width):
    Gtk = _gtk()
    from gi.repository import Pango
    settings = settings_for(tmp_path, key)
    if key == 'mimo':
        settings['notes'].insert(0, 'Memory: this model uses GPU slots, bounded pinned RAM and disk-backed weights. The '
                                    'full checkpoint need not fit in RAM. The engine keeps 40 GiB of host RAM available '
                                    'and sizes expert slots from free GPU memory.')
    window, panel = _render(Gtk, settings, width=width, mode=mode, shot=f'{key}-{mode or "default"}-{width}')
    assert panel.card.get_visible() and panel.card.get_mapped()
    card = _box(panel.card, panel)
    grid = _box(panel.grid, panel)
    mode_row = _box(panel.mode_box, panel)
    assert card[0] >= grid[0] + grid[2] + 24, (card, grid)              # to the right of the fields, with room
    assert card[0] + card[2] <= panel.get_allocation().width            # and inside the screen
    first = mode_row if panel.mode_box.get_visible() else _box(panel.advanced, panel)
    assert abs(card[1] - first[1]) <= 4, (card, first)                   # its top level with the first row
    sizes = [lab.get_style_context().get_property('font', Gtk.StateFlags.NORMAL).get_size() / Pango.SCALE * 96 / 72
             for lab in _labels(panel.card) if lab.get_text().strip()]  # points -> CSS px at 96 dpi
    assert sizes and min(sizes) >= 11, sizes                            # no tiny text: 11 px at the least (the pill)
    assert sorted(sizes)[len(sizes) // 2] >= 13, sizes                  # most of it 13-14 px
    texts = [lab.get_text() for lab in _labels(panel.card)]
    assert texts[0] == 'Recommended settings'
    assert any(t.startswith('Context is a starting recommendation') for t in texts)   # the notes moved here
    assert not panel.message.get_text().startswith('Context is a starting recommendation')
    if key == 'qwen27':
        names = [lab.get_text() for lab in _labels(panel.grid)]
        assert 'Sub-agent context · tokens' in names and 'Parallel requests' in names and 'slot ctx' not in names
        active = 'INSTRUCT' if mode == 'instruct' else 'THINKING'
        pill = next(lab for lab in _labels(panel.card) if lab.get_text() == 'In use')
        heading = pill.get_parent().get_children()[0]
        assert heading.get_text() == active
    window.destroy()


def test_use_recommendations_still_restores_the_cards_values(tmp_path):
    Gtk = _gtk()
    window, panel = _render(Gtk, settings_for(tmp_path, 'qwen27'), width=1800)
    panel.fields['temperature'][0].set_text('0.2')
    panel.fields['parallel'][0].set_text('4')
    panel.reset_settings()
    assert panel.fields['temperature'][0].get_text() == '1.0' and panel.fields['parallel'][0].get_text() == '1'
    assert panel.card.get_visible() and panel.message.get_text().startswith('Recommendations restored.')
    window.destroy()


def test_the_screen_with_advanced_closed_keeps_the_card(tmp_path):
    Gtk = _gtk()
    window, panel = _render(Gtk, settings_for(tmp_path, 'qwen27'), width=2000, expanded=False,
                            shot='qwen27-advanced-closed-2000')
    assert panel.card.get_mapped() and not panel.advanced.get_expanded()
    card = _box(panel.card, panel)
    assert card[0] > panel.fields_column.get_allocation().width          # where it is with Advanced open: it stays put
    window.destroy()
