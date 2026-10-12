"""Real board identity and provider-neutral workflow contracts; no model calls."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dream.core.engine import Engine
from dream.gui.deepsleep import DeepSleep, Node, layout


@pytest.fixture
def board(tmp_path):
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    for key in ('one', 'two'):
        board.nodes[key] = Node(key, 'codex', '', 'Codex', layout({}), state='ready')
    board._link('one', 'two', False)
    return board


async def test_rename_only_changes_title_and_survives_restore(board):
    before = board.nodes['one']
    token, generation = before.bridge_token, before.launch_generation
    before.input_pending = b'draft'
    result = await board.action({'action': 'rename', 'node': 'one', 'title': '  Research  '})
    assert [n['title'] for n in result['nodes']] == ['Research', 'Codex']
    assert board.nodes['one'] is before and before.state == 'ready'
    assert before.input_pending == b'draft' and before.bridge_token == token
    assert before.launch_generation == generation
    restored = DeepSleep(board.workspace, board.base_url)
    assert restored.nodes['one'].title == 'Research'
    assert restored.nodes['two'].title == 'Codex'
    assert restored.linked('one', 'two')


@pytest.mark.parametrize('title', ['', ' ', 'a' * 101, 'a\nb', 'a\x00b', 'a\x7fb', None, 5])
async def test_invalid_rename_preserves_existing_node(board, title):
    with pytest.raises(ValueError, match='Terminal name'):
        await board.action({'action': 'rename', 'node': 'one', 'title': title})
    assert board.nodes['one'].title == 'Codex'


async def test_save_failure_rolls_back_name_and_child_cannot_rename(board, monkeypatch):
    monkeypatch.setattr(board, 'save', Mock(side_effect=OSError('storage unavailable')))
    with pytest.raises(OSError):
        await board.action({'action': 'rename', 'node': 'one', 'title': 'Changed'})
    assert board.nodes['one'].title == 'Codex'
    with pytest.raises(ValueError):
        await board.bridge(board.nodes['one'].bridge_token, {'operation': 'rename', 'node': 'two', 'title': 'Changed'})
    assert board.nodes['two'].title == 'Codex'


@pytest.mark.parametrize('kind', ['anthropic', 'cli', 'openai'])
@pytest.mark.parametrize('linked', [False, True])
def test_board_routing_guidance_only_when_tools_available(monkeypatch, tmp_path, kind, linked):
    from dream.core import engine as module
    e = Engine.__new__(Engine)
    e.provider = SimpleNamespace(kind=kind)
    e.profile = SimpleNamespace()
    e.model = 'fixture'
    e.workspace = tmp_path
    e.execution_capability = None
    e.execution_scope = None
    e._mcp = SimpleNamespace(servers=[])
    e._local_subs = {}
    e._session_style = 'concise'
    e.store, e.session_id = None, 'fixture'
    e._session_tools = dict.fromkeys(('session_list', 'session_send', 'session_inbox')) if linked else {}
    monkeypatch.setattr(module, 'guidance', lambda *a, **k: 'Provider identity')
    monkeypatch.setattr(module, 'concurrent_provider', lambda _: False)
    monkeypatch.setattr(e, '_subagent_lanes', lambda: 1)
    monkeypatch.setattr(module.system_prompt, 'build_system_prompt', lambda *a, **k: '\n'.join(k['stable_sections']))
    prompt = e._system_prompt()
    assert ('## Other terminals on this DeepSleep board' in prompt) is linked
    if linked:
        assert 'session_list' in prompt and 'session_send' in prompt and 'session_inbox' in prompt
        assert 'A greeting needs no skill' in prompt
        assert 'answer normally' in prompt and 'separate recipients' in prompt


def test_context_unknown_does_not_display_aggregate_or_keep_stale_context():
    e = Engine.__new__(Engine)
    e.total_cost_usd, e.session_tokens, e.last_context_tokens = 0, 0, 50000
    e._absorb_result({'usage': {'prompt_tokens': 403513, 'completion_tokens': 500},
                      'usage_scope': 'turn', 'context_tokens': None})
    assert e.session_tokens == 404013
    assert e.last_context_tokens is None
    e._absorb_result({'usage': {'prompt_tokens': 39772, 'completion_tokens': 27},
                      'usage_scope': 'turn', 'context_tokens': None})
    assert e.session_tokens == 443812 and e.last_context_tokens is None
    # HTTP's final request usage and whole-turn stats retain their separate roles.
    e._absorb_result({'usage': {'prompt_tokens': 40000},
                      'stats': {'prompt_tokens': 120000, 'completion_tokens': 100}})
    assert e.last_context_tokens == 40000 and e.session_tokens == 563912
    e._absorb_result({'usage': {'prompt_tokens': 200000}, 'context_tokens': 46635})
    assert e.last_context_tokens == 46635


def test_usage_cache_is_included_once_for_canonical_and_raw_providers():
    e = Engine.__new__(Engine)
    e.total_cost_usd, e.session_tokens, e.last_context_tokens = 0, 0, None
    e._absorb_result({'usage': {'input_tokens': 10, 'prompt_tokens': 90,
                               'cache_read_tokens': 80, 'output_tokens': 2}, 'context_tokens': None})
    assert e.session_tokens == 92
    e._absorb_result({'usage': {'input_tokens': 10, 'cache_read_input_tokens': 80,
                               'cache_creation_input_tokens': 5, 'output_tokens': 2}, 'context_tokens': 95})
    assert e.session_tokens == 189 and e.last_context_tokens == 95


@pytest.mark.parametrize('latest,expected', [
    ({'input_tokens': 12, 'cache_read_input_tokens': 80, 'cache_creation_input_tokens': 8}, 100),
    (None, None), ({'output_tokens': 8}, None), ({'input_tokens': -1}, None),
])
async def test_sdk_context_is_final_request_not_aggregate(latest, expected):
    from claude_agent_sdk import AssistantMessage, TextBlock
    from test_sdk_main_completion import FiniteClient, backend_for, terminal
    reply = terminal()
    reply.usage = {'input_tokens': 1000, 'cache_read_input_tokens': 5000, 'output_tokens': 100}
    client = FiniteClient(reply, body=[
        AssistantMessage(content=[TextBlock('earlier')], model='fixture', usage={'input_tokens': 900}),
        AssistantMessage(content=[TextBlock('final')], model='fixture', usage=latest),
    ])
    events = [event async for event in backend_for(client).ask('hello')]
    result, = [event.data for event in events if event.kind == 'result']
    assert result['context_tokens'] == expected
    assert result['usage'] == reply.usage and result['usage_scope'] == 'turn'


@pytest.mark.parametrize('cost,label', [(None, 'cost not reported'), (0, 'est. $0.00'),
                                       (.42, 'est. $0.42'), (float('nan'), 'cost not reported')])
def test_completed_turn_distinguishes_usage_from_context_and_missing_cost(cost, label):
    from dream.tui.render import format_turn_stats
    data = {'usage': {'input_tokens': 1200000, 'output_tokens': 900},
            'context_tokens': None, 'total_cost_usd': cost}
    text = format_turn_stats(data, fallback_window=200000)
    assert 'ctx --' in text and '1.2M' not in text and label in text
    data['context_tokens'] = 20000
    assert 'ctx 20.0k/200.0k (10%)' in format_turn_stats(data, fallback_window=200000)
