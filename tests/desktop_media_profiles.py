"""Native media/profile checks, isolated from the owner's browser data."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dream.desktop.browser import webview, Gtk
from dream.desktop.window import DreamWindow


class MediaProfiles(unittest.TestCase):
    def test_saved_profile_is_separate_and_studio_stays_ephemeral(self):
        if not Gtk.init_check()[0]:
            self.skipTest('A graphical display is required')
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary)/'browser'
            private = webview()
            saved = webview(profile=profile)
            studio = webview(local_only=True, profile=profile)
            try:
                self.assertTrue(private.get_context().is_ephemeral())
                self.assertFalse(saved.get_context().is_ephemeral())
                self.assertTrue(studio.get_context().is_ephemeral())
                self.assertEqual(saved.get_context().get_website_data_manager().get_base_data_directory(), str(profile/'data'))
                self.assertFalse(saved.get_settings().get_allow_file_access_from_file_urls())
                self.assertFalse(saved.get_settings().get_allow_universal_access_from_file_urls())
            finally:
                private.destroy(); saved.destroy(); studio.destroy()

    def test_media_result_uses_studio_download_without_exposing_token_to_browser(self):
        window = Mock(address_info=('http://127.0.0.1:3210','secret'))
        action = Mock()
        uri = 'http://127.0.0.1:3210/api/media/assets/123?token=secret'
        action.get_request.return_value.get_uri.return_value = uri
        action.is_user_gesture.return_value = True
        DreamWindow._studio_popup(window, None, action)
        window.studio.download_uri.assert_called_once_with(uri)
        window.browser.navigate.assert_not_called()


if __name__ == '__main__': unittest.main()
