"""DREAM-187 webkit: the desktop's web content runs sandboxed and the Browser tab is a browsing view only.

System GTK is optional in pytest's venv; also run with system Python directly.
"""
import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from dream.desktop import browser
    from dream.desktop.browser import Browser, GLib, Gtk, WebKit2, webview
except (ImportError, ValueError):
    browser = Browser = GLib = Gtk = WebKit2 = webview = None


def web_processes(parent: int) -> dict[int, list[str]]:
    """Our WebKitWebProcess descendants, each with the names of the processes between it and us."""
    processes = {}
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            stat = Path(f'/proc/{entry}/stat').read_text()
            command = Path(f'/proc/{entry}/cmdline').read_bytes().split(b'\0')[0]
        except OSError:
            continue
        # comm is cut at 15 characters ("WebKitWebProces"); the command line carries the full name.
        processes[int(entry)] = (os.path.basename(command).decode(errors='replace'), int(stat[stat.rindex(')') + 2:].split()[1]))
    found = {}
    for pid, (name, _) in processes.items():
        chain, current = [], pid
        while current in processes and current != parent:
            chain.append(processes[current][0])
            current = processes[current][1]
        if current == parent and name == 'WebKitWebProcess':
            found[pid] = chain[1:]
    return found


@unittest.skipIf(browser is None, 'System GTK/WebKit bindings required')
class BrowserHardening(unittest.TestCase):
    def test_every_web_context_sandboxes_its_web_process(self):
        if not Gtk.init_check()[0]:
            self.skipTest('A graphical display is required')
        with tempfile.TemporaryDirectory() as temporary:
            private = webview()
            saved = webview(profile=Path(temporary) / 'browser')
            studio = webview(local_only=True)
            try:
                for view in (private, saved, studio):
                    self.assertTrue(view.get_context().get_sandbox_enabled())
                # DevTools belong to the Studio view only; a browsing view has no use for the surface.
                self.assertFalse(private.get_settings().get_enable_developer_extras())
                self.assertFalse(saved.get_settings().get_enable_developer_extras())
                self.assertTrue(studio.get_settings().get_enable_developer_extras())
            finally:
                private.destroy(); saved.destroy(); studio.destroy()

    def test_sandbox_environment_reaches_every_pool_and_binds_only_the_registry_directory(self):
        registry_dir = Path(GLib.get_user_cache_dir()) / 'gstreamer-1.0'
        registry = registry_dir / f'registry.{platform.machine()}.bin'
        with patch.dict(os.environ, clear=False):
            os.environ.pop('WEBKIT_FORCE_SANDBOX', None)
            os.environ.pop('GST_REGISTRY', None)
            browser.sandbox_environment()
            # WEBKIT_FORCE_SANDBOX also covers the pools Dream never creates itself (the Web Inspector's).
            self.assertEqual(os.environ['WEBKIT_FORCE_SANDBOX'], '1')
            # bubblewrap binds the PARENT of GST_REGISTRY read-write: a file inside gstreamer-1.0, never the
            # directory itself, whose parent is all of ~/.cache.
            self.assertEqual(Path(os.environ['GST_REGISTRY']), registry)
            self.assertTrue(registry_dir.is_dir())
            # An inherited registry straight under a broad directory would hand the web process that whole
            # directory read-write; the launcher's value is not kept.
            os.environ['GST_REGISTRY'] = str(Path(GLib.get_home_dir()) / 'registry.bin')
            browser.sandbox_environment()
            self.assertEqual(Path(os.environ['GST_REGISTRY']), registry)

    def test_a_disabled_sandbox_refuses_every_web_view_before_a_context_exists(self):
        # WebKit honours WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS over set_sandbox_enabled and
        # WEBKIT_FORCE_SANDBOX; rather than run web content unconfined, Dream opens no web view at all.
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS': '1'}), \
             patch.object(browser.WebKit2, 'WebContext') as contexts, patch.object(browser.WebKit2, 'WebView') as views:
            for kwargs in ({}, {'local_only': True}, {'profile': Path(temporary) / 'browser'}):
                with self.subTest(**kwargs), self.assertRaisesRegex(RuntimeError, 'WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS'):
                    webview(**kwargs)
            contexts.new_ephemeral.assert_not_called()
            contexts.new_with_website_data_manager.assert_not_called()
            views.new_with_context.assert_not_called()
            self.assertFalse((Path(temporary) / 'browser').exists())

    def test_web_process_runs_under_bubblewrap_without_the_cache_directory(self):
        if not Gtk.init_check()[0]:
            self.skipTest('A graphical display is required')
        view = webview()
        try:
            loop = GLib.MainLoop()
            view.connect('load-changed', lambda _view, event: loop.quit() if event == WebKit2.LoadEvent.FINISHED else None)
            GLib.timeout_add_seconds(20, loop.quit)
            view.load_uri('about:blank')
            loop.run()
            processes = web_processes(os.getpid())
            self.assertTrue(processes, 'no web process was started for the view')
            cache = GLib.get_user_cache_dir()
            for pid, ancestry in processes.items():
                # The flag alone is not confinement: WebKit skips bubblewrap silently in a container or under
                # WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS. The process tree and its mounts are the proof.
                self.assertIn('bwrap', ancestry)
                mounts = [line.split()[4] for line in Path(f'/proc/{pid}/mountinfo').read_text().splitlines()]
                self.assertIn(f'{cache}/gstreamer-1.0', mounts)
                self.assertNotIn(cache, mounts)
                self.assertNotIn(GLib.get_home_dir(), mounts)
        finally:
            view.destroy()

    def test_popup_navigates_the_tab_only_on_a_user_gesture(self):
        tab = Mock()
        action = Mock()
        action.get_request.return_value.get_uri.return_value = 'https://example.com/'
        with patch.object(browser.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)):
            action.is_user_gesture.return_value = False
            self.assertIsNone(Browser._popup(tab, None, action))
            tab.navigate.assert_not_called()
            action.is_user_gesture.return_value = True
            self.assertIsNone(Browser._popup(tab, None, action))
            tab.navigate.assert_called_once_with('https://example.com/')


if __name__ == '__main__':
    unittest.main()
