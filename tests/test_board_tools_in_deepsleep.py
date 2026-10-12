"""The board tools reach every DeepSleep terminal, whatever its skill preset (the owner, 2026-10-11).

Root cause of "@everyone post to the message board" going wrong: every shipped preset dropped ram_read and ram_post
(they are not core tools and no preset names their group), while Dream's instructions to a DeepSleep terminal tell
it to post there. Each terminal tried, met "No such tool available" and went looking for another way. The link
tools were already kept for a DeepSleep terminal whatever the preset; the board tools now are too."""
from dream import presets
from dream.tools import registry

BOARD_AND_LINKS = {'ram_read', 'ram_post', 'session_list', 'session_send', 'session_inbox'}


def deepsleep_terminal(monkeypatch):
    monkeypatch.setenv('DREAM_DEEPSLEEP_NODE_ID', 'node1')
    monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_URL', 'http://127.0.0.1:9999/api/deepsleep/bridge')
    monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_TOKEN', 'secret-token-123456789')


def test_a_deepsleep_terminal_keeps_the_board_and_link_tools_under_every_preset(monkeypatch):
    deepsleep_terminal(monkeypatch)
    for name, raw in [*presets.presets().items(), ('Bare', {})]:
        monkeypatch.setattr(presets, '_SESSION', presets._normal(name, raw))
        assert BOARD_AND_LINKS <= set(registry.build()['names']), name


def test_outside_deepsleep_the_preset_still_decides(monkeypatch):
    for key in ('DREAM_DEEPSLEEP_NODE_ID', 'DREAM_DEEPSLEEP_BRIDGE_URL', 'DREAM_DEEPSLEEP_BRIDGE_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(presets, '_SESSION', presets._normal('Bare', {}))
    assert not BOARD_AND_LINKS & set(registry.build()['names'])
