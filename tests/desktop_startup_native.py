"""Opt-in GTK onboarding check. Uses fixtures; never starts a session or model.

Run with /usr/bin/python3 tests/desktop_startup_native.py from a graphical session.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dream.desktop.window import DreamWindow, Gtk, GLib


def main():
    if not Gtk.init_check([])[0]:
        raise SystemExit('A graphical display is required.')
    window = DreamWindow(str(ROOT / '.venv/bin/python'), str(ROOT), [], autostart=False)
    panel = window.onboarding
    selection = dict(gpus=2, ctx=32768, options={'temperature': 1.0, 'thinking': True, 'reasoning_effort': 'max'})
    sources = {key: 'Fixture recommendation' for key in ('gpus', 'ctx', *selection['options'])}
    settings = dict(selection=selection, recommended=selection, sources=sources,
                    recommended_sources=sources, notes=['Metadata-only fixture'], identity='fixture', revision=None,
                    controls=[dict(name='temperature', kind='float', hint='Sampling', choices=[], default=1.0),
                              dict(name='thinking', kind='bool', hint='Thinking', choices=[], default=True),
                              dict(name='reasoning_effort', kind='enum', hint='Effort', choices=['low','high','max'], default='max')])
    panel.query = lambda action, callback, *args: callback(settings)
    panel.catalog_loaded({'choices': [dict(id='local', label='Fixture GGUF', path='/fixture.gguf',
                                          kind='local', provider='machx', ready=True, note='No model loading')]})
    captured = []
    window.launch_selection = captured.append
    panel.launch()
    assert captured[0]['selection'] == selection
    assert captured[0]['workspace'] == str(ROOT)
    panel.fields['ctx'][0].set_text('bad number')
    panel.launch()
    assert len(captured) == 1
    assert 'Check the advanced settings' in panel.message.get_text()
    panel.reset_settings()
    panel.launch()
    assert len(captured) == 2 and captured[-1]['selection'] == selection
    panel.set_running(True)
    assert not panel.start.get_sensitive()
    panel.set_running(False)
    assert panel.start.get_sensitive()
    assert not window.running
    assert window.workspace.get_visible_child_name() == 'studio'
    window.workspace.set_visible_child_name('terminal')
    assert window.terminal_panel.get_visible()
    window.workspace.set_visible_child_name('studio')
    print('PASS: native selection, workspace, Advanced round-trip, invalid input, reset and busy recovery; no session/model started.', flush=True)
    GLib.timeout_add(200, lambda: (window.destroy(), False)[1])
    Gtk.main()


if __name__ == '__main__':
    main()
