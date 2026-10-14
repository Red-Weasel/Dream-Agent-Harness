"""The prompt box while a turn works, and the prompt optimizer at Dream's terminal prompt (the owner, 2026-10-11:
[feedback]).

While a turn works the prompt box stays at the bottom between its two lines; Enter sends what was typed into the
running turn when it can take it (the model reads it after its current step), else it waits and goes as the next
prompt. The frame draws its lines and dashes instead of printing them as \\u2500 escapes. The optimizer reads a typed
prompt twice; a rewrite waits for Send or Edit."""
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import prompt_optimizer
from dream.tui.render import Renderer, _frame_char
from dream.tui.turn_input import TurnInput


def typed_into(*chunks):
    sent = []
    box = TurnInput(sent.append)
    for chunk in chunks:
        box.feed(chunk)
    return box, sent


def test_typing_editing_and_enter_send_one_message_each():
    box, sent = typed_into(b'fix the cache', b'\x7f\x7f\x7f\x7f\x7fquery\r', b'second\r')
    assert sent == ['fix the query', 'second'] and box.text == ''
    box, sent = typed_into(b'wrong\x15right', b'\r', b'\r   \r')   # Ctrl+U empties; blank lines send nothing
    assert sent == ['right']


def test_arrows_split_characters_and_pastes_arrive_whole():
    box, sent = typed_into(b'a\x1b[Ab\x1b[1;5C', b'c\x1bOD', 'é'.encode()[:1], 'é'.encode()[1:])
    assert box.text == 'abcé' and not sent
    # A bracketed paste keeps its lines; even cut between reads, its end marker is found.
    box, sent = typed_into(b'\x1b[200~line one\r\nline', b' two\x1b[20', b'1~\r')
    assert sent == ['line one\nline two']
    box, sent = typed_into(b'first\x1b[13;2usecond\x1b\rthird\r')       # Shift+Enter, Alt+Enter: new lines
    assert sent == ['first\nsecond\nthird']


def test_the_optimizer_buttons_keys_work_while_a_turn_works_too():
    pressed = []
    box = TurnInput(lambda text: None)
    box.keys = {'\x0f': lambda: pressed.append('optimizer'), '\x1d': lambda: pressed.append('skip')}
    box.feed(b'ab\x0fc\x1d')
    assert pressed == ['optimizer', 'skip'] and box.text == 'abc'


def test_the_working_frame_draws_lines_and_dashes_and_escapes_only_what_terminals_disagree_on():
    for kept in '─—›…éü∴':
        assert _frame_char(kept) == kept
    for escaped in ('😀', '界', '⌚', '❤', '‍', '️', '́'):
        assert _frame_char(escaped).isascii() and _frame_char(escaped).startswith('\\')


def app_at(tmp_path, monkeypatch, width=70):
    from dream.tui.app import App
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.prompt_optimizer = True   # the tests' setting starts it off (conftest)
    app.renderer = Renderer(Console(width=width, force_terminal=True, color_system=None, record=True))
    app.engine = SimpleNamespace(store=object(), provider=SimpleNamespace(key='machx'), model='fixture', profile=None)
    monkeypatch.setattr(app, '_status_data', lambda: dict(
        mode_label='auto mode on', mode_color='#fbbf24', effort_short='high', ws='~/repo', context='ctx 1k/32k'))
    return app


def frame_text(app):
    console = app.renderer.console
    app.renderer._status_cb = app._footer
    segments = console.render(app.renderer._live_view())
    return ''.join(s.text for s in segments if not s.control)


def test_while_a_turn_works_the_prompt_box_sits_between_two_drawn_lines(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    screen = frame_text(app)
    lines = screen.splitlines()
    assert '\\u2500' not in screen and lines[0] == '─' * 70, lines
    assert lines[1].startswith('you › ') and 'type here: Enter sends it to the agent now' in lines[1]
    assert lines[2].startswith('─') and lines[2].endswith(' [ Optimizer: on ]  [ Skip next: off ] ──')
    assert 'auto mode on' in lines[3] and 'repo' in lines[4]
    app.turn_input.feed(b'also run the tests')
    app._typed_queue[:] = ['/optimizer off']
    lines = frame_text(app).splitlines()
    assert lines[0] == '  queued › /optimizer off' and lines[2] == 'you › also run the tests '


def test_shift_enter_in_the_box_starts_a_new_row_not_a_symbol(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    app.turn_input.feed(b'first line\x1b[13;2usecond line')                # Shift+Enter between them
    lines = frame_text(app).splitlines()
    at = lines.index('you › first line')
    assert lines[at + 1] == '      second line ' and '↵' not in frame_text(app)
    assert lines[at + 2].startswith('─') and '[ Optimizer: on ]' in lines[at + 2]   # the box's bottom line after it


class Inbox:
    def __init__(self):
        self.open, self.receipts, self.sent = True, {}, []

    async def submit(self, text, identifier):
        self.sent.append(text)
        self.receipts[identifier] = {'status': 'pending'}


async def test_enter_sends_into_the_running_turn_or_waits_for_its_end(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    inbox = app.engine._steering_inbox = Inbox()
    app._ordinary_chat_active = True
    await app._send_typed('also check the tests')                   # the turn can take it: in now
    await app._send_typed('/optimizer off')                          # a command waits for the turn's end
    assert inbox.sent == ['also check the tests'] and app._typed_queue == ['/optimizer off']
    assert 'sent into this turn' in app.renderer.console.export_text()
    app._ordinary_chat_active = False                                # a /review, a /loop: no running chat turn
    await app._send_typed('then summarise')
    assert app._typed_queue == ['/optimizer off', 'then summarise']
    # The turn ended before it read the correction (retained): it goes first, then the rest, in order.
    inbox.receipts[next(iter(inbox.receipts))]['status'] = 'retained'
    app.interrupted = False
    assert [app._take_typed(), app._take_typed(), app._take_typed(), app._take_typed()] == [
        'also check the tests', '/optimizer off', 'then summarise', None]


async def test_after_a_stop_what_waits_goes_back_into_the_prompt_box(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    app._typed_queue[:] = ['one', 'two']
    app.interrupted = True
    assert app._take_typed() is None and app._prefill == 'one\ntwo' and not app._typed_queue


def reviewing(monkeypatch, found=None, error=None):
    calls = []

    async def review_twice(text, recent, workspace, settings, step=lambda n: None):
        calls.append(text)
        step(1)
        if error:
            raise error
        return found
    monkeypatch.setattr(prompt_optimizer, 'review_twice', review_twice)
    return calls


async def test_the_optimizer_sends_its_rewrite_on_send_and_puts_it_in_the_box_on_edit(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    calls = reviewing(monkeypatch, {'rewritten': 'Rewrite the cache key; run the tests.', 'problems': ['no scope']})
    choice = ['send']

    async def send_or_edit():
        return choice[0]
    monkeypatch.setattr(app, '_send_or_edit', send_or_edit)
    assert await app._optimized('fix the cache thing') == 'Rewrite the cache key; run the tests.'
    shown = app.renderer.console.export_text()
    assert 'Rewritten prompt' in shown and 'Unclear as typed: no scope' in shown
    choice[0] = 'edit'
    assert await app._optimized('fix the cache thing') == '' and app._prefill == 'Rewrite the cache key; run the tests.'
    assert await app._optimized('my edited version of it') == 'my edited version of it'   # Edit's text goes as typed
    assert len(calls) == 2


async def test_the_setting_starts_it_on_or_off(tmp_path, monkeypatch):
    from dream.tui.app import App
    monkeypatch.setenv('DREAM_PROMPT_OPTIMIZER', '1')
    assert App(provider='machx', model='fixture', workspace=tmp_path).prompt_optimizer is True
    monkeypatch.setenv('DREAM_PROMPT_OPTIMIZER', '0')
    assert App(provider='machx', model='fixture', workspace=tmp_path).prompt_optimizer is False
    monkeypatch.delenv('DREAM_PROMPT_OPTIMIZER')
    assert App(provider='machx', model='fixture', workspace=tmp_path).prompt_optimizer is False   # off by default (the owner, 2026-10-11)


async def test_the_buttons_on_the_prompt_line_show_and_flip_both_switches(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    assert app._optimizer_label().plain == '[ Optimizer: on ]  [ Skip next: off ]'
    app.turn_input.feed(b'\x1d')                                          # Ctrl+], the [ Skip next ] button
    assert app._optimizer_label().plain == '[ Optimizer: on ]  [ Skip next: on ]'
    app.turn_input.feed(b'\x0f')                                          # Ctrl+O, the [ Optimizer ] button
    assert app._optimizer_label().plain == '[ Optimizer: off ]' and not app.prompt_optimizer


async def test_off_skipped_short_clear_or_failing_prompts_go_as_typed(tmp_path, monkeypatch):
    app = app_at(tmp_path, monkeypatch)
    calls = reviewing(monkeypatch, None)
    assert await app._optimized('yes') == 'yes' and await app._optimized('/model x y') == '/model x y'
    assert await app._optimized('a clear enough prompt') == 'a clear enough prompt' and calls   # clear: as typed
    calls.clear()
    app._toggle_skip_once()                                              # [ Skip next ]
    assert await app._optimized('skip this one please') == 'skip this one please' and not calls
    assert app.optimizer_skip_once is False                              # one message, then as before
    app._toggle_optimizer()                                              # [ Optimizer ]
    assert await app._optimized('optimizer is off now') == 'optimizer is off now' and not calls
    app.prompt_optimizer = True
    reviewing(monkeypatch, error=TimeoutError())
    assert await app._optimized('the review is too slow') == 'the review is too slow'
    assert 'prompt optimizer could not read it (a read took longer than 60 s); sent as typed' in \
        ' '.join(app.renderer.console.export_text().split())
    reviewing(monkeypatch, error=RuntimeError('server gone'))
    assert await app._optimized('the review cannot run') == 'the review cannot run'
    assert 'prompt optimizer could not read it (RuntimeError: server gone); sent as typed' in \
        ' '.join(app.renderer.console.export_text().split())


@pytest.mark.parametrize('reply,expected', [
    ('{"rewrite": true, "problems": ["vague"], "rewritten": "Do X."}', {'rewritten': 'Do X.', 'problems': ['vague']}),
    ('thinking first... {"rewrite": true, "problems": [], "rewritten": "Do X."} done', {'rewritten': 'Do X.', 'problems': []}),
    ('{"rewrite": false, "rewritten": "Do X."}', None),
    ('{"rewrite": true, "rewritten": "same text"}', None),
    ('not json at all', None),
])
def test_the_second_reads_answer(reply, expected):
    assert prompt_optimizer.verdict(reply, 'same text') == expected


@pytest.mark.parametrize('sequence,expected', [
    (b'\x1b[200~one\r\ntwo\x1b[201~', 'one\ntwo'),
    (b'first\x1b[13;2usecond', 'first\nsecond'),
    (b'first\x1b\rsecond', 'first\nsecond'),
    (b'a\x1b[1;5Cb\x1bODc', 'abc'),
])
def test_escape_sequences_survive_every_read_boundary(sequence, expected):
    chunks = [[sequence[:at], sequence[at:]] for at in range(1, len(sequence))]
    chunks.append([sequence[at:at + 1] for at in range(len(sequence))])
    for reads in chunks:
        box, sent = typed_into(*reads)
        assert not sent, reads
        assert box.text == expected, reads
        box.feed(b'\r')
        assert sent == [expected] and box.text == ''
