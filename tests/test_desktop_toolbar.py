"""Model-free native header trust checks. Run with system Python/unittest for GI."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dream.desktop.protocol import toolbar_state
try:
    from dream.desktop.window import DreamWindow, Gtk
except (ImportError, ValueError):
    DreamWindow = None


def snapshot(**updates):
    value = {'session_id':'fixture', 'workspace':'/fixture', 'view':'deepsleep', 'controls':{
        'provider': {'label':'Provider', 'disabled':False, 'value':'codex', 'options':[
            {'value':'codex','label':'Codex','disabled':False},
            {'value':'disabled','label':'Unavailable','disabled':True}]},
        'name': {'label':'Terminal name', 'disabled':False, 'value':'Claude · Research'},
        'add': {'label':'Add', 'disabled':False, 'pressed':False},
        'layout': {'label':'Text layout', 'disabled':False, 'value':'terminal', 'options':[
            {'value': value, 'label': value.title(), 'disabled':False}
            for value in ('terminal', 'conversation', 'activity')]},
        'characters': {'label':'Characters', 'disabled':False, 'pressed':True}}}
    value.update(updates)
    return value


class ToolbarProtocol(unittest.TestCase):
    def test_current_snapshot_and_malformed_stale_oversized_refused(self):
        self.assertEqual(toolbar_state(json.dumps(snapshot()), 'fixture', '/fixture'), snapshot())
        for value in [None, [], {'controls':{}}, snapshot(workspace='/other'), snapshot(session_id='old'),
                      snapshot(view='chat'), snapshot(controls={'shell':{}})]:
            self.assertIsNone(toolbar_state(json.dumps(value), 'fixture', '/fixture'))
        for invalid_name in (None, 5, 'x' * 101):
            invalid = snapshot()
            invalid['controls']['name']['value'] = invalid_name
            self.assertIsNone(toolbar_state(json.dumps(invalid), 'fixture', '/fixture'))
        for raw in ['{', 'x' * 65537]:
            self.assertIsNone(toolbar_state(raw, 'fixture', '/fixture'))
        invalid = snapshot()
        invalid['controls']['add']['disabled'] = 'false'
        self.assertIsNone(toolbar_state(json.dumps(invalid), 'fixture', '/fixture'))


@unittest.skipIf(DreamWindow is None, 'System GTK bindings required')
class NativeToolbar(unittest.TestCase):
    def window(self):
        window = Mock(ds_syncing=False, native_view='deepsleep', session_id='fixture', workspace_path='/fixture',
                      address_info=('http://127.0.0.1:1234','token'), ds_snapshot=snapshot()['controls'])
        window.workspace.get_visible_child_name.return_value = 'studio'
        window._trusted_studio_document.return_value = True
        binding = (window.address_info, window.session_id, window.workspace_path)
        window.ds_binding = binding
        window._deepsleep_binding.return_value = binding
        return window

    def test_known_controls_dispatch_only_current_options_and_binding(self):
        window = self.window()
        self.assertTrue(DreamWindow._deepsleep_action(window, 'provider', 'codex'))
        self.assertTrue(DreamWindow._deepsleep_action(window, 'characters'))
        self.assertTrue(DreamWindow._deepsleep_action(window, 'name', 'Claude · Review'))
        self.assertTrue(DreamWindow._deepsleep_action(window, 'layout', 'conversation'))
        code = window.studio.run_javascript.call_args.args[0]
        self.assertIn('DreamDeepSleepToolbar?.dispatch(', code)
        self.assertIn('"workspace": "/fixture"', code)
        self.assertNotIn('token', code)
        window.studio.run_javascript.reset_mock()
        for control, value in [('shell',None),('provider','disabled'),('provider','unknown'),('add','extra'),('layout','invalid'),('name','x'*101),('name',None)]:
            self.assertFalse(DreamWindow._deepsleep_action(window, control, value))
        window.studio.run_javascript.assert_not_called()

    def test_untrusted_old_workspace_inactive_view_and_sync_do_not_dispatch(self):
        for field, value in [('ds_binding',None),('native_view','chat'),('ds_syncing',True)]:
            window = self.window()
            setattr(window, field, value)
            self.assertFalse(DreamWindow._deepsleep_action(window, 'add'))
            window.studio.run_javascript.assert_not_called()
        window = self.window()
        window._trusted_studio_document.return_value = False
        self.assertFalse(DreamWindow._deepsleep_action(window, 'add'))
        window.studio.run_javascript.assert_not_called()
        window = self.window()
        window.workspace.get_visible_child_name.return_value = 'browser'
        self.assertFalse(DreamWindow._deepsleep_action(window, 'add'))
        window.studio.run_javascript.assert_not_called()

    def test_stale_async_snapshot_never_updates_new_workspace(self):
        window = self.window()
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = json.dumps(snapshot())
        DreamWindow._deepsleep_state_read(window, window.studio, Mock(), ('old','fixture','/fixture'))
        window.ds_header.set_sensitive.assert_not_called()
        self.assertFalse(window.ds_reading)

    def test_navigation_hides_header_without_retaining_action_binding(self):
        window = self.window()
        window.nav_buttons = {}
        DreamWindow._highlight_navigation(window, 'deepsleep')
        window.ds_header.set_visible.assert_called_with(True)
        DreamWindow._highlight_navigation(window, 'chat')
        window.ds_header.set_visible.assert_called_with(False)
        self.assertIsNone(window.ds_binding)
        window.ds_header.set_sensitive.assert_called_with(False)

    def test_real_pill_geometry_survives_show_all_and_identity_refresh(self):
        if not Gtk.init_check([])[0]:
            self.skipTest('Display required for native geometry')
        window = self.window()
        window.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        window.nav_labels = []
        window.frontier_message = ''
        DreamWindow._add_frontier_controls(window)
        host = Gtk.Window()
        host.add(window.sidebar)
        try:
            host.show_all()
            while Gtk.events_pending(): Gtk.main_iteration()
            before = [(row.get_preferred_height(), row.get_allocation().height) for row in window.sidebar.get_children()]
            for username in ['Research <user>', '', None, 'Long identity ' * 30]:
                DreamWindow._show_frontier_state(window, {'controls':{'codex':{'signed_in':True,'title':'Verified','username':username}}})
                host.show_all()
                while Gtk.events_pending(): Gtk.main_iteration()
                after = [(row.get_preferred_height(), row.get_allocation().height) for row in window.sidebar.get_children()]
                self.assertEqual(before, after)
                self.assertFalse(window.frontier_users['codex'].get_visible())
        finally:
            host.destroy()

    def test_real_name_entry_roundtrip_preserves_focused_typing(self):
        if not Gtk.init_check([])[0]:
            self.skipTest('Display required for native name entry')
        window = self.window()
        window._layout_deepsleep_toolbar = lambda width: DreamWindow._layout_deepsleep_toolbar(window, width)
        window._deepsleep_action = lambda *args: DreamWindow._deepsleep_action(window, *args)
        header = Gtk.HeaderBar()
        DreamWindow._add_deepsleep_toolbar(window, header)
        window.ds_snapshot = snapshot()['controls']
        window.ds_binding = window._deepsleep_binding()
        window._layout_deepsleep_toolbar(2560)
        host = Gtk.Window()
        host.set_titlebar(header)
        try:
            host.show_all()
            while Gtk.events_pending(): Gtk.main_iteration()
            entry = window.ds_controls['name']
            entry.set_sensitive(True)
            entry.grab_focus()
            entry.set_text('Claude · Drafting')
            self.assertIn('"control": "name"', window.studio.run_javascript.call_args.args[0])
            self.assertIn('Drafting', window.studio.run_javascript.call_args.args[0])
            window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = json.dumps(snapshot())
            DreamWindow._deepsleep_state_read(window, window.studio, Mock(), window.ds_binding)
            self.assertEqual(entry.get_text(), 'Claude · Drafting')
            window.ds_controls['add'].grab_focus()
            cleared = snapshot()
            cleared['controls']['name']['value'] = ''
            window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = json.dumps(cleared)
            DreamWindow._deepsleep_state_read(window, window.studio, Mock(), window.ds_binding)
            self.assertEqual(entry.get_text(), '')
            self.assertEqual(entry.get_max_length(), 100)
        finally:
            host.destroy()

    def test_real_toolbar_wide_inline_and_narrow_overflow(self):
        if not Gtk.init_check([])[0]:
            self.skipTest('Display required for native geometry')
        window = self.window()
        window._layout_deepsleep_toolbar = lambda width: DreamWindow._layout_deepsleep_toolbar(window, width)
        header = Gtk.HeaderBar()
        DreamWindow._add_deepsleep_toolbar(window, header)
        window._layout_deepsleep_toolbar(2560)
        self.assertEqual(len(window.ds_inline.get_children()), 12)
        self.assertEqual(window.ds_controls['layout'].get_tooltip_text(), 'Text view')
        self.assertFalse(window.ds_more.get_visible())
        window._layout_deepsleep_toolbar(720)
        self.assertEqual(len(window.ds_overflow.get_children()), 12)
        self.assertEqual(len(window.ds_inline.get_children()), 0)
        self.assertTrue(window.ds_more.get_visible())
        self.assertTrue(window.ds_more.get_child().get_visible())
        self.assertEqual(window.ds_more.get_child().get_text(), 'Controls')
        self.assertLess(header.get_preferred_width().minimum_width, 720)


    def test_real_window_header_layout_without_starting_session(self):
        if not Gtk.init_check([])[0]:
            self.skipTest('Display required for actual DreamWindow')
        import os
        import tempfile
        import time
        import cairo
        from gi.repository import Gdk
        if Gdk.Screen.get_default().get_resolution() <= 0:
            Gdk.Screen.get_default().set_resolution(96)
        with tempfile.TemporaryDirectory(prefix='dream-toolbar-workspace-') as workspace:
            window = DreamWindow(sys.executable, workspace, [], autostart=False)
            try:
                window.studio_stack.set_visible_child_name('web')
                window.navigate('deepsleep')
                for key in ('provider', 'profile', 'model', 'layout'):
                    window.ds_controls[key].append('fixture', 'Model-free fixture')
                    window.ds_controls[key].set_active_id('fixture')
                for width in (2560, 960):
                    window.resize(width, 900)
                    for step in range(30):
                        while Gtk.events_pending(): Gtk.main_iteration()
                        # Continue the drag after GTK recomputes the previous
                        # inline layout's minimum for the new overflow layout.
                        if step == 10:
                            window.resize(width, 900)
                        time.sleep(.01)
                    allocation = window.get_allocation()
                    self.assertLessEqual(window.get_size().width, width, {
                        'header': tuple(window.get_titlebar().get_preferred_width()),
                        'body': tuple(window.get_child().get_preferred_width()),
                        'toolbar': tuple(window.ds_header.get_preferred_width()),
                        'inline': len(window.ds_inline.get_children()),
                        'overflow': len(window.ds_overflow.get_children()),
                        'allocation': allocation.width})
                    self.assertTrue(window.ds_header.get_visible())
                    if width == 2560:
                        self.assertEqual(len(window.ds_inline.get_children()), 12)
                    else:
                        self.assertEqual(len(window.ds_overflow.get_children()), 12)
                        self.assertTrue(window.ds_more.get_child().get_visible())
                        self.assertGreater(window.ds_more.get_child().get_allocation().width, 20)
                        self.assertGreaterEqual(window.ds_more.get_child().get_allocation().width,
                                                window.ds_more.get_child().get_preferred_width().minimum_width)
                    before = tuple(window.get_size())
                    for username in ('reader@example.test', '', 'Long identity ' * 30):
                        window._show_frontier_state({'controls': {'codex': {
                            'signed_in': True, 'title': 'Verified', 'username': username}}})
                        window.show_all()
                        while Gtk.events_pending(): Gtk.main_iteration()
                        self.assertEqual(tuple(window.get_size()), before)
                        self.assertFalse(window.frontier_users['codex'].get_visible())
                    self.assertIsNone(window.child_pid)
                    self.assertFalse(window.running)
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, allocation.width, allocation.height)
                    window.draw(cairo.Context(surface))
                    folder = os.environ.get('DREAM_TOOLBAR_NATIVE_SHOTS')
                    if folder:
                        Path(folder).mkdir(parents=True, exist_ok=True)
                        surface.write_to_png(str(Path(folder) / f'native-toolbar-{width}.png'))
            finally:
                window.destroy()


if __name__ == '__main__':
    unittest.main()
