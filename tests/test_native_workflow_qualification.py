"""Native navigation regressions. Run with /usr/bin/python3 for GI, no display.

The optional scripts/native_workflow_qualification.py checks real widgets and HTTP.
"""
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from dream.desktop.window import DreamWindow
except ModuleNotFoundError as exc:
    if exc.name != 'gi':
        raise
    DreamWindow = None


@unittest.skipIf(DreamWindow is None, 'System Python with GTK GI is required')
class NativeOptimizerNavigation(unittest.TestCase):
    def test_optimizer_navigation_dispatches_without_starting_a_session(self):
        window = Mock()
        self.assertTrue(DreamWindow.navigate(window, 'optimizer'))
        self.assertEqual(window.native_view, 'optimizer')
        window.workspace.set_visible_child_name.assert_called_once_with('studio')
        window._dispatch_view.assert_called_once_with()
        window.start_terminal.assert_not_called()
        window.launch_selection.assert_not_called()

    def test_optimizer_dispatch_keeps_session_identity(self):
        window = Mock(native_view='optimizer', session_id='fixture-session')
        window._trusted_studio_document.return_value = True
        self.assertTrue(DreamWindow._dispatch_view(window))
        script = window.studio.run_javascript.call_args.args[0]
        self.assertIn('"view": "optimizer"', script)
        self.assertIn('"session_id": "fixture-session"', script)
        window._trusted_studio_document.return_value = False
        window.studio.reset_mock()
        self.assertFalse(DreamWindow._dispatch_view(window))
        window.studio.run_javascript.assert_not_called()

    def test_web_optimizer_selection_synchronizes_native_sidebar(self):
        window = Mock(native_view='chat', address_info=('http://127.0.0.1:1234', 'fixture'))
        window._trusted_studio_document.return_value = True
        window.workspace.get_visible_child_name.return_value = 'studio'
        view = Mock()
        view.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value = 'optimizer'
        DreamWindow._web_view_read(window, view, Mock(), (window.address_info, 'chat'))
        self.assertEqual(window.native_view, 'optimizer')
        window._highlight_navigation.assert_called_once_with('optimizer')


class QualificationEvidence(unittest.TestCase):
    def test_inherited_credentials_and_owner_state_are_removed_before_config_import(self):
        from scripts import native_workflow_qualification as qualification
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            inherited = {'DISPLAY': ':117', 'XAUTHORITY': '/tmp/test-xauthority',
                'ANTHROPIC_API_KEY': 'synthetic-secret', 'OPENAI_API_KEY': 'synthetic-secret',
                'ARBITRARY_PROVIDER_TOKEN': 'synthetic-secret', 'DREAM_ROOT': '/private-owner-root',
                'DREAM_DB': '/private-owner-db', 'DREAM_MODEL': 'owner-model',
                'DREAM_SEMANTIC_MEMORY': '1', 'DREAM_PROFILE': 'owner-profile',
                'XDG_CONFIG_HOME': '/private-config', 'PYTHONPATH': '/private-imports'}
            env = qualification.fixture_environment(inherited, workspace)
            self.assertEqual(env['DISPLAY'], ':117')
            self.assertEqual(env['XAUTHORITY'], '/tmp/test-xauthority')
            self.assertNotIn('synthetic-secret', env.values())
            self.assertNotIn('/private-owner-root', env.values())
            self.assertNotIn('DREAM_MODEL', env)
            self.assertNotIn('DREAM_DB', env)
            self.assertEqual(Path(env['DREAM_ROOT']), workspace / 'state')
            for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR'):
                self.assertTrue(Path(env[key]).is_relative_to(workspace))
            result = subprocess.run([sys.executable, '-c',
                f'import os, sys; from pathlib import Path; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r}); '
                'from scripts import native_workflow_qualification as q; '
                f'q.isolate_environment(Path({str(workspace)!r})); '
                'from dream import config; '
                'assert str(config.ROOT) == os.environ["DREAM_ROOT"]; '
                'assert config.DB_PATH.is_relative_to(config.ROOT); '
                'assert not config.SEMANTIC_MEMORY; assert config.MODEL is None; '
                'assert "ANTHROPIC_API_KEY" not in os.environ'],
                cwd=workspace, env=inherited, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_vte_wait_status_requires_an_observed_zero_exit(self):
        from scripts import native_workflow_qualification as qualification
        qualification.require_clean_exit(0)
        for status in (None, 7 << 8, 15):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                qualification.require_clean_exit(status)


if __name__ == '__main__':
    unittest.main()
