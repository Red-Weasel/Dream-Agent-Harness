"""Native navigation boundary, tested without starting a session or a model.

System GTK is optional in pytest's venv; also run with system Python directly.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from dream.desktop.window import DreamWindow, WebKit2
except (ImportError, ValueError):
    DreamWindow = None


@unittest.skipIf(DreamWindow is None, 'System GTK/VTE/WebKit bindings required')
class NativeNavigation(unittest.TestCase):
    def window(self, uri='http://127.0.0.1:3210/?companion=1#token=secret'):
        window = Mock(address_info=('http://127.0.0.1:3210', 'secret'),
                      session_id='fixture', connected=True, native_view='chat')
        window.studio.get_uri.return_value = uri
        window._trusted_studio_document = lambda: DreamWindow._trusted_studio_document(window)
        return window

    def test_invalid_view_never_changes_workspace_or_executes_javascript(self):
        window = self.window()
        self.assertFalse(DreamWindow.navigate(window, 'https://evil.example'))
        window.workspace.set_visible_child_name.assert_not_called()
        window.studio.run_javascript.assert_not_called()

    def test_native_terminal_navigation_does_not_launch_session(self):
        window = self.window()
        self.assertTrue(DreamWindow.navigate(window, 'terminal'))
        window.workspace.set_visible_child_name.assert_called_once_with('terminal')
        window.start_terminal.assert_not_called()
        window.studio.run_javascript.assert_not_called()

    def test_dispatch_is_bound_to_authenticated_root_document(self):
        # The token rides in the fragment (DREAM-187); nothing loads the older ?token= form, so it is not trusted.
        for uri in ('https://evil.example/#token=secret', 'https://evil.example/?token=secret',
                    'http://127.0.0.1:3210/api/download?token=secret', 'http://127.0.0.1:3210/?companion=1',
                    'http://127.0.0.1:3210/?companion=1#token=wrong', 'http://127.0.0.1:3210/?token=wrong',
                    'http://127.0.0.1:3210/?token=secret', 'http://127.0.0.1:3210/?companion=1&token=secret', 'about:blank'):
            with self.subTest(uri=uri):
                window = self.window(uri)
                self.assertFalse(DreamWindow._dispatch_view(window))
                window.studio.run_javascript.assert_not_called()

    def test_selection_carries_session_without_token_or_host_command(self):
        window = self.window()
        self.assertTrue(DreamWindow._dispatch_view(window))
        script = window.studio.run_javascript.call_args.args[0]
        self.assertIn('dream:navigate', script)
        self.assertIn('"session_id": "fixture"', script)
        self.assertIn('"view": "chat"', script)
        self.assertNotIn('secret', script)

    def test_web_selection_updates_highlight_without_native_actions(self):
        window = self.window()
        window.workspace.get_visible_child_name.return_value = 'studio'
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = 'projects'
        DreamWindow._web_view_read(window, window.studio, Mock(), (window.address_info, window.native_view))
        self.assertEqual(window.native_view, 'projects')
        window._highlight_navigation.assert_called_once_with('projects')
        window.workspace.set_visible_child_name.assert_not_called()

    def test_web_cannot_select_native_browser(self):
        window = self.window()
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = 'browser'
        DreamWindow._web_view_read(window, window.studio, Mock(), (window.address_info, window.native_view))
        self.assertEqual(window.native_view, 'chat')
        window._highlight_navigation.assert_not_called()
        window.workspace.set_visible_child_name.assert_not_called()

    def test_late_web_read_cannot_overwrite_new_native_selection(self):
        window = self.window()
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = 'projects'
        DreamWindow._web_view_read(window, window.studio, Mock(), (window.address_info, 'home'))
        self.assertEqual(window.native_view, 'chat')
        window._highlight_navigation.assert_not_called()

    def test_understand_is_a_switch_that_keeps_the_current_page(self):
        """Owner, 2026-09-28: the Understand switch opens or closes the panel and never becomes the selected page."""
        window = self.window()
        self.assertTrue(DreamWindow.navigate(window, 'understand'))
        window._dispatch_view.assert_called_once_with('understand')
        window._highlight_navigation.assert_not_called()
        self.assertEqual(window.native_view, 'chat')
        window = self.window()
        self.assertTrue(DreamWindow._dispatch_view(window, 'understand'))
        self.assertIn('"view": "understand"', window.studio.run_javascript.call_args.args[0])

    def test_the_switch_follows_the_panel_without_toggling_it_back(self):
        window = self.window()
        window.workspace.get_visible_child_name.return_value = 'studio'
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = 'chat|1'
        DreamWindow._web_view_read(window, window.studio, Mock(), (window.address_info, window.native_view))
        window._show_understand_state.assert_called_once_with(True)
        window._highlight_navigation.assert_called_once_with('chat')
        window = self.window()
        window.understand_toggle.get_active.return_value = False
        window.understand_toggle.set_active.side_effect = lambda on: DreamWindow._understand_toggled(
            window, Mock(get_active=Mock(return_value=on)))
        DreamWindow._show_understand_state(window, True)
        window.understand_toggle.set_active.assert_called_once_with(True)
        window.navigate.assert_not_called()                     # a state sync never sends another toggle
        window.understand_knob.set_halign.assert_called_once()
        window.syncing_understand = False
        DreamWindow._understand_toggled(window, Mock(get_active=Mock(return_value=False)))
        window.navigate.assert_called_once_with('understand')   # a click does

    def test_loaded_page_receives_queued_view_only_at_finish(self):
        window = self.window()
        DreamWindow._studio_loaded(window, window.studio, WebKit2.LoadEvent.STARTED)
        window._dispatch_view.assert_not_called()
        DreamWindow._studio_loaded(window, window.studio, WebKit2.LoadEvent.FINISHED)
        window._dispatch_view.assert_called_once()

    def test_session_environments_do_not_inherit_the_shell_webkit_settings(self):
        """DREAM-187: sandbox_environment() sets WEBKIT_FORCE_SANDBOX and GST_REGISTRY for this process's own
        web views. A session, and whatever it starts, is not one of them and does not inherit either."""
        import os
        from unittest.mock import patch
        from dream.desktop.browser import sandbox_environment

        with patch.dict(os.environ, clear=False):
            sandbox_environment()
            self.assertIn('WEBKIT_FORCE_SANDBOX', os.environ)
            env = DreamWindow.child_env(Mock(discovery=Path('/private/session.json')))
        self.assertNotIn('WEBKIT_FORCE_SANDBOX', env)
        self.assertNotIn('GST_REGISTRY', env)
        self.assertEqual(env['DREAM_GUI'], '1')
        self.assertEqual(env['DREAM_DESKTOP_SESSION_FILE'], '/private/session.json')

    def test_every_toplevel_carries_the_class_desktop_control_refuses(self):
        """DREAM-187: the shell's dialogs ('Finish this Dream session?') are windows of their own, with
        GTK's default WM_CLASS, not the main window's set_wmclass. The program class covers them all,
        so another Dream session's desktop control refuses them like the main window."""
        from unittest.mock import patch
        from dream import computer
        from dream.desktop import window as module

        with patch.object(module.Gtk, 'init_check', return_value=(True, [])), patch.object(module, 'DreamWindow'), \
             patch.object(module.Gtk, 'main'), patch.object(module, 'crash_log_arm_window'), \
             patch.object(sys, 'argv', ['window', '--python', 'py', '--cwd', '.']):
            module.main()
        self.assertIn(module.Gdk.get_program_class(), computer._OWN_CLASSES)

    def test_nested_is_a_web_view_the_sidebar_dispatches_and_an_unknown_view_is_refused(self):
        """DREAM-188: Nested Dream is a page of the Studio document, like Sleepwalk: the sidebar selects the
        studio pane, highlights it and dispatches the view; a name that is not a view changes nothing."""
        window = self.window()
        self.assertTrue(DreamWindow.navigate(window, 'nested'))
        window.workspace.set_visible_child_name.assert_called_once_with('studio')
        window._highlight_navigation.assert_called_once_with('nested')
        self.assertEqual(window.native_view, 'nested')
        window._dispatch_view.assert_called_once_with()
        window.start_terminal.assert_not_called()
        window = self.window()
        window.native_view = 'nested'
        self.assertTrue(DreamWindow._dispatch_view(window))
        self.assertIn('"view": "nested"', window.studio.run_javascript.call_args.args[0])
        window = self.window()
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = 'nested|0'
        window.workspace.get_visible_child_name.return_value = 'studio'
        DreamWindow._web_view_read(window, window.studio, Mock(), (window.address_info, window.native_view))
        self.assertEqual(window.native_view, 'nested')             # the page's own selection is mirrored back
        for unknown in ('nested-dream', 'Nested', 'nested '):
            with self.subTest(view=unknown):
                window = self.window()
                self.assertFalse(DreamWindow.navigate(window, unknown))
                window.workspace.set_visible_child_name.assert_not_called()
                window.studio.run_javascript.assert_not_called()


if __name__ == '__main__':
    unittest.main()
