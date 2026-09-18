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
    def window(self, uri='http://127.0.0.1:3210/?token=secret&companion=1'):
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
        for uri in ('https://evil.example/?token=secret', 'http://127.0.0.1:3210/api/download?token=secret',
                    'http://127.0.0.1:3210/?token=wrong', 'about:blank'):
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

    def test_loaded_page_receives_queued_view_only_at_finish(self):
        window = self.window()
        DreamWindow._studio_loaded(window, window.studio, WebKit2.LoadEvent.STARTED)
        window._dispatch_view.assert_not_called()
        DreamWindow._studio_loaded(window, window.studio, WebKit2.LoadEvent.FINISHED)
        window._dispatch_view.assert_called_once()


if __name__ == '__main__':
    unittest.main()
