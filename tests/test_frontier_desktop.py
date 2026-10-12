"""Native frontier bridge boundaries; system Python/GTK, no running sessions."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from dream.desktop.window import DreamWindow, Gtk
except (ImportError, ValueError):
    DreamWindow = None

@unittest.skipIf(DreamWindow is None, 'System GTK bindings required')
class NativeFrontiers(unittest.TestCase):
    def window(self, trusted=True):
        window=Mock(syncing_frontiers=False, frontier_message='', frontier_buttons={key:Mock() for key in ('anthropic','grok','antigravity','codex')})
        window.frontier_knobs={key:Mock() for key in window.frontier_buttons}
        window.frontier_logins=window.frontier_buttons.copy()
        window.frontier_users={key:Mock() for key in window.frontier_buttons}
        window._trusted_studio_document.return_value=trusted
        return window

    def test_invalid_target_and_untrusted_document_execute_nothing(self):
        for action,key,trusted in [('login','gemini',True),('shell','codex',True),('toggle','codex',True),('login','codex',False)]:
            window=self.window(trusted)
            self.assertFalse(DreamWindow._frontier_action(window,action,key,True))
            window.studio.run_javascript.assert_not_called()

    def test_gemini_login_dispatches_only_antigravity_without_commands_or_credentials(self):
        window=self.window()
        self.assertTrue(DreamWindow._frontier_action(window,'login','antigravity'))
        script=window.studio.run_javascript.call_args.args[0]
        self.assertIn('dream:frontier-login',script)
        self.assertIn('"provider": "antigravity"',script)
        self.assertNotIn('gemini',script)
        self.assertNotIn('auth',script)
        self.assertNotIn('token',script)

    def test_roster_membership_never_turns_account_light_green(self):
        window=self.window()
        DreamWindow._show_frontier_state(window,{'controls':{'codex':{'on':True,'signed_in':False,'status':'unknown','title':'Account unchecked'}},'loginBusy':False})
        for dot in window.frontier_knobs.values():
            dot.get_style_context.return_value.add_class.assert_called_once_with('account-unknown')
        window._frontier_action.assert_not_called()
        window.frontier_buttons['codex'].set_sensitive.assert_called_once_with(True)

    def test_verified_account_sync_updates_green_username_and_busy_without_dispatch(self):
        window=self.window()
        DreamWindow._show_frontier_state(window,{'controls':{'codex':{'signed_in':True,'status':'signed_in','title':'Codex signed in','username':'Research <user>'}},'message':'Account checked','loginBusy':True})
        window._frontier_action.assert_not_called()
        window.frontier_knobs['codex'].get_style_context.return_value.add_class.assert_called_once_with('account-green')
        window.frontier_knobs['anthropic'].get_style_context.return_value.add_class.assert_called_once_with('account-unknown')
        window.frontier_users['codex'].set_text.assert_called_once_with('')
        window.frontier_buttons['codex'].set_tooltip_text.assert_called_with('Codex signed in · Research <user>')
        window.frontier_users['codex'].set_markup.assert_not_called()
        window.frontier_users['codex'].hide.assert_called_once()
        window.frontier_logins['codex'].set_sensitive.assert_called_once_with(False)
        self.assertFalse(window.syncing_frontiers)

    def test_poll_reads_confirmed_state_only_from_current_authenticated_document(self):
        window=self.window()
        window.address_info=('http://127.0.0.1:3210','fixture')
        window.native_view='chat'
        window._trusted_studio_document.return_value=True
        window.studio.run_javascript_finish.return_value.get_js_value.return_value.to_string.return_value='chat|0|'+json.dumps({'controls':{'codex':{'signed_in':True,'status':'signed_in'}}})
        DreamWindow._web_view_read(window,window.studio,Mock(),(window.address_info,'chat'))
        window._show_frontier_state.assert_called_once_with({'controls':{'codex':{'signed_in':True,'status':'signed_in'}}})
        window._show_frontier_state.reset_mock()
        DreamWindow._web_view_read(window,window.studio,Mock(),(('http://127.0.0.1:9999','old'),'chat'))
        window._show_frontier_state.assert_not_called()

    def test_real_native_sidebar_order_and_accessible_login_buttons(self):
        if not Gtk.init_check([])[0]: self.skipTest('Display required for native widgets')
        window=self.window()
        window.sidebar=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        window.nav_labels=[]
        understand=Gtk.ToggleButton(label='Understand')
        window.sidebar.pack_start(understand,False,False,0)
        DreamWindow._add_frontier_controls(window)
        children=window.sidebar.get_children()
        self.assertIs(children[0],understand)
        names=[]
        for key,row in zip(('anthropic','grok','antigravity','codex'),children[1:]):
            pill,username=row.get_children()
            names.append(pill.get_accessible().get_name())
            self.assertFalse(isinstance(pill,Gtk.ToggleButton))
            self.assertFalse(pill.get_sensitive())
            self.assertGreaterEqual(row.get_margin_top()+row.get_margin_bottom(),10)
            self.assertTrue(pill.get_style_context().has_class('frontier-pill'))
            self.assertTrue(window.frontier_knobs[key].get_style_context().has_class('account-unknown'))
            self.assertTrue(username.get_no_show_all())
            pill.emit('clicked')
            window._frontier_action.assert_called_with('login',key)
        self.assertEqual(names,['Sign in to Claude','Sign in to Grok','Sign in to Gemini','Sign in to Codex'])

    def test_signed_out_unavailable_and_unverified_have_distinct_lights(self):
        window=self.window()
        DreamWindow._show_frontier_state(window,{'controls':{
            'grok':{'status':'unknown','signed_in':False},
            'antigravity':{'status':'signed_out','signed_in':False},
            'codex':{'status':'unavailable','signed_in':False},
            'anthropic':{'status':'signed_in','signed_in':True}}})
        for provider,expected in [('grok','account-unknown'),('antigravity','account-red'),
                                  ('codex','account-red'),('anthropic','account-green')]:
            window.frontier_knobs[provider].get_style_context.return_value.add_class.assert_called_once_with(expected)

if __name__=='__main__': unittest.main()
