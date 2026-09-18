"""Native boundary regressions; system Python provides GI. No display needed.

Run: python3 tests/desktop_native_regressions.py
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dream.desktop.window import DreamWindow, Gdk
from dream.desktop.browser import Browser


class NativeBoundaries(unittest.TestCase):
    def test_browser_focused_clipboard_shortcuts_never_feed_terminal(self):
        window = Mock()
        window.terminal.has_focus.return_value = False
        for key in ('C', 'V'):
            event = Mock(state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK,
                         keyval=Gdk.keyval_from_name(key))
            self.assertFalse(DreamWindow._keys(window, None, event))
        window.terminal.copy_clipboard_format.assert_not_called()
        window.terminal.paste_clipboard.assert_not_called()

    def test_terminal_focused_paste_still_works(self):
        window = Mock()
        window.terminal.has_focus.return_value = True
        event = Mock(state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK,
                     keyval=Gdk.keyval_from_name('V'))
        self.assertTrue(DreamWindow._keys(window, None, event))
        window.terminal.paste_clipboard.assert_called_once()

    def test_authenticated_popup_stays_out_of_general_browser(self):
        window = Mock(address_info=('http://127.0.0.1:3210', 'secret'))
        action = Mock()
        action.get_request.return_value.get_uri.return_value = 'http://127.0.0.1:3210/?token=secret'
        action.is_user_gesture.return_value = True
        DreamWindow._studio_popup(window, None, action)
        window.browser.navigate.assert_not_called()
        action.get_request.return_value.get_uri.return_value = 'http://127.0.0.1:3210/api/download?token=secret&path=page.html'
        DreamWindow._studio_popup(window, None, action)
        window.studio.download_uri.assert_called_once()
        window.browser.navigate.assert_not_called()

    def test_failed_download_finished_signal_cannot_report_success(self):
        browser = Mock()
        download = Mock()
        callbacks = {}
        download.connect.side_effect = lambda name, callback: callbacks.__setitem__(name, callback)
        download.get_destination.return_value = 'file:///tmp/output.zip'
        Browser._download(browser, None, download)
        callbacks['failed'](download, Mock(message='connection lost'))
        callbacks['finished'](download)
        browser.status.assert_called_once_with('Download stopped: connection lost')

    def test_failed_studio_page_retries_at_same_server_address(self):
        address = ('http://127.0.0.1:3210','secret')
        window = Mock(stopped=False, child_pid=99, running=True, address_info=address,
                      studio_failed=True, last_connect=0.0, show_sequence=0)
        DreamWindow._polled(window,99,address,{'session':{'provider':'test'},'show_sequence':0},None)
        window._connect_studio.assert_called_once()

    def test_missing_discovery_clears_previous_connectivity(self):
        window = Mock(stopped=False, child_pid=99, running=True, connected=True)
        DreamWindow._polled(window,99,None,None,None)
        self.assertFalse(window.connected)


if __name__ == '__main__':
    unittest.main()
