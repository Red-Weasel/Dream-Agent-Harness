"""A download card in the Nested view (the owner's 15-worker run of 2026-10-02: present_fs_item_for_download said the
card "is showing in Studio"; the owner looked in the Studio tab and found nothing, and the Nested view showed only the
tool-call row). The card is in the chat; the orchestrator's conversation in the Nested view shows it too, with a link
to /api/download that carries the session token, and the tool says where the card really is.

The Nested card is built from the call and its own result, which the history replays: nothing of another call can
reach it (three gate rounds found ways for a shared `studio` event to), and a reconnect or a reload shows it again."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_view import open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
TOOL = 'present_fs_item_for_download'


def said(label, kind):
    """The tool's result, in its own words (test_the_real_tools_words_give_the_card_its_kind checks them)."""
    return (f"Download card '{label}' ({kind}) is showing in the chat (and in the Nested view's conversation), "
            f"not in the Studio tab.")


def use(s, tid, path, label=None, name=TOOL):
    s.srv.bus.publish(Event('tool_use', {'id': tid, 'name': name,
                                         'input': {'path': path, **({'label': label} if label else {})}}))


def result(s, tid, text, error=False):
    s.srv.bus.publish(Event('tool_result', {'id': tid, 'name': TOOL, 'content': text, 'is_error': error}))


async def cards(page):
    return await page.locator('#nd-convo .nd-dl').evaluate_all("""cs => cs.map(c => [c.querySelector('b').textContent,
        c.querySelector('.nd-dl-t span')?.textContent || '', new URL(c.querySelector('a').href).searchParams.get('path')])""")


async def test_a_download_card_shows_in_the_orchestrators_conversation(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Write the report and hand it over.')
        s.srv.bus.publish(Event('turn_start', {}))
        use(s, 't-1', 'out/SWARM_REPORT.md')
        s.srv.bus.publish(Event('studio', {'op': 'download', 'path': 'out/SWARM_REPORT.md', 'label': 'SWARM_REPORT.md',
                                           'kind': 'file (2,113 bytes)'}))       # the chat's card; the view does not need it
        result(s, 't-1', said('SWARM_REPORT.md', 'file (2,113 bytes)'))
        card = page.locator('#nd-convo .nd-dl')
        await expect(card).to_have_count(1)
        assert await cards(page) == [['SWARM_REPORT.md', 'file (2,113 bytes)', 'out/SWARM_REPORT.md']]
        link = card.get_by_role('link', name='Download', exact=True)
        await expect(link).to_be_visible()
        href = urlsplit(await link.get_attribute('href'))
        token = await s.page.evaluate('TOKEN')
        assert href.path == '/api/download' and parse_qs(href.query) == {'path': ['out/SWARM_REPORT.md'], 'token': [token]}
        assert await link.get_attribute('download') is not None
        # it stays through the rest of the turn, after the call's own row
        s.srv.bus.publish(Event('text_delta', 'The report is ready.'))
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(page.locator('.nd-msg.nd-dream .nd-stream')).to_contain_text('The report is ready.')
        order = await page.locator('#nd-convo').evaluate(
            "c => [...c.children].map(e => e.classList.contains('nd-dl') ? 'card' : e.classList.contains('nd-tool-msg') ? 'call'"
            " : e.classList.contains('nd-dream') ? 'reply' : e.classList.contains('nd-you') ? 'you' : 'other')")
        assert order == ['you', 'call', 'card', 'reply'], order
        assert s.errors == []


async def test_a_label_or_path_with_markup_is_text_and_the_whole_workspace_is_the_project(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        evil = '<img src=x onerror="window.__x=1">'
        use(s, 't-1', 'a"><b>.md', label=evil)
        result(s, 't-1', said(evil, 'file (1 bytes)'))
        use(s, 't-2', '')                                               # no path: the whole workspace, zipped
        result(s, 't-2', said('Project', 'folder (zipped)'))
        use(s, 't-3', 'c.md', label="x' (y")                             # a label that imitates the result's own words
        result(s, 't-3', said("x' (y", 'file (3 bytes)'))
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(3)
        assert await cards(page) == [[evil, 'file (1 bytes)', 'a"><b>.md'], ['Project', 'folder (zipped)', ''],
                                     ["x' (y", 'file (3 bytes)', 'c.md']]
        assert await page.locator('#nd-convo .nd-dl img').count() == 0 and await s.page.evaluate('window.__x') is None
        assert s.errors == []


async def test_the_card_is_there_again_after_a_reload_with_its_size(studio):  # noqa: F811
    """Gate round 1: the card was drawn from the `studio` event, which the history does not keep, so a reconnect lost
    it. The call and its result are in the history."""
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Hand over the report.')
        s.srv.bus.publish(Event('turn_start', {}))
        use(s, 't-1', './out/a b/ü&x.md')
        result(s, 't-1', said('ü&x.md', 'file (2,113 bytes)'))
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(1)
        live = await cards(page)
        assert live == [['ü&x.md', 'file (2,113 bytes)', './out/a b/ü&x.md']]   # the path as the orchestrator wrote it
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(1)
        assert await cards(page) == live
        assert await page.locator('#nd-convo .nd-dl .nd-when').count() == 0   # a replayed row has no time
        assert s.errors == []


async def test_a_call_that_failed_or_never_answered_shows_no_card(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        use(s, 't-1', 'missing.md')
        result(s, 't-1', 'No such file or folder: /w/missing.md', error=True)
        use(s, 't-2', 'read.md')                                        # its result never comes
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        result(s, 't-9', said('Ghost', 'file (1 bytes)'))               # a result for a call this page never saw
        await expect(page.locator('#nd-convo .nd-tool-msg')).to_have_count(2)
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(0)
        assert s.errors == []


async def test_each_card_is_its_own_calls_whatever_the_order(studio):  # noqa: F811
    """The gate's rounds 2, 2b and 2c: with the `studio` events shared between calls, calls announced together (a Claude
    lead announces every call of a reply first), results out of order and paths with a shared ending gave a card
    another call's label, size or link. Nothing is shared now: each card has its own call's path and its own result."""
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        calls = [('t-1', 'a.md', 'Top', 'file (1 bytes)'), ('t-2', 'x/a.md', 'Inner', 'file (2 bytes)'),
                 ('t-3', 'dir/', None, 'folder (zipped)'), ('t-4', '/w/x/dir', 'Deep', 'folder (zipped)')]
        for tid, path, label, _ in calls:
            use(s, tid, path, label, name='mcp__dream__' + TOOL)        # the Claude SDK's name for the tool
        use(s, 't-5', 'c.md', name='read_file')
        for path in ('a.md', 'x/a.md', 'dir', 'x/dir'):                 # every event first, as a Claude lead might send them
            s.srv.bus.publish(Event('studio', {'op': 'download', 'path': path, 'label': 'event', 'kind': 'file (9 bytes)'}))
        s.srv.bus.publish(Event('tool_result', {'id': 't-5', 'name': 'read_file', 'content': 'c', 'is_error': False}))
        for tid, path, label, kind in reversed(calls):                  # the results in the reverse of the calls
            result(s, tid, said(label or 'dir', kind))
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(4)
        assert await cards(page) == [['Deep', 'folder (zipped)', '/w/x/dir'], ['dir', 'folder (zipped)', 'dir/'],
                                     ['Inner', 'file (2 bytes)', 'x/a.md'], ['Top', 'file (1 bytes)', 'a.md']]
        assert s.errors == []


async def test_studio_events_and_other_tools_add_no_card(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('studio', {'op': 'follow', 'on': False}))
        s.srv.bus.publish(Event('studio', {'op': 'download', 'path': 'a.md', 'label': 'a.md', 'kind': 'file (1 bytes)'}))
        use(s, 't-1', 'a.md', name='read_file')
        s.srv.bus.publish(Event('tool_result', {'id': 't-1', 'name': 'read_file', 'is_error': False,
                                                'content': said('a.md', 'file (1 bytes)')}))
        s.srv.bus.publish(Event('text_delta', 'Hello.'))
        await expect(page.locator('.nd-msg.nd-dream .nd-stream')).to_contain_text('Hello.')
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(0)
        assert s.errors == []


async def test_a_result_in_other_words_still_gives_a_card_without_its_kind(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        use(s, 't-1', 'out/report.md')
        result(s, 't-1', 'Presented.')
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(1)
        assert await cards(page) == [['report.md', '', 'out/report.md']]
        assert s.errors == []


async def test_the_real_tools_words_give_the_card_its_kind(studio, tmp_path, monkeypatch):  # noqa: F811
    """The tool's result and description name the chat, not "Studio" (the Studio tab does not show the card); and the
    view reads what the item is from those very words, for a file and for a folder."""
    from dream.tools import studio as tools

    (tmp_path / 'report.md').write_text('done')
    (tmp_path / 'out').mkdir()
    emitted = []
    context = SimpleNamespace(workspace=tmp_path, emit=emitted.append)
    monkeypatch.setattr(tools, 'ctx', lambda: context)
    monkeypatch.setattr(tools, 'studio', lambda: object())
    handler = getattr(tools.present_fs_item_for_download, 'handler', tools.present_fs_item_for_download)
    texts = [(await handler({'path': path}))['content'][0]['text'] for path in ('report.md', 'out')]
    assert [e.data['op'] for e in emitted] == ['download', 'download']
    assert texts[0] == said('report.md', 'file (4 bytes)') and texts[1] == said('out', 'folder (zipped)'), texts
    assert all('in the chat' in t and 'in Studio' not in t for t in texts), texts
    description = getattr(tools.present_fs_item_for_download, 'description', '')
    assert 'in Studio' not in description and 'chat' in description, description
    async with studio() as s:
        page = await open_nested(s)
        for tid, path, text in (('t-1', 'report.md', texts[0]), ('t-2', 'out', texts[1])):
            use(s, tid, path)
            result(s, tid, text)
        await expect(page.locator('#nd-convo .nd-dl')).to_have_count(2)
        assert await cards(page) == [['report.md', 'file (4 bytes)', 'report.md'], ['out', 'folder (zipped)', 'out']]
        assert s.errors == []
