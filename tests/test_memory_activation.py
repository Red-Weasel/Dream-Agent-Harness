"""Model-facing memory obeys lifecycle state; retained originals remain inspectable."""
import pytest

from dream.core import system_prompt
from dream.memory import longterm
from dream.memory.store import MemoryStore
from dream.tools.context import ToolContext, bind_context
from dream.tools.memory_tools import recall


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / 'activation.db')
    s.project = 'alpha'
    yield s
    s.close()


def fact(s, slug, **kwargs):
    return s.upsert_memory('semantic', slug, 'Cedar endpoint routing ' + slug,
                           slug=slug, project='alpha', embed=False, **kwargs)


@pytest.mark.asyncio
async def test_actual_recall_excludes_expired_and_invalidated_without_deleting(store, tmp_path):
    fact(store, 'fresh')
    fact(store, 'expired', lifecycle={'version': 1, 'expires_at': '2020-01-01T00:00:00Z'})
    fact(store, 'invalid', lifecycle={'version': 1, 'invalidated_at': '2020-01-01T00:00:00Z'})
    c = ToolContext(store=store, working=None, browser=None, session_id='synthetic', workspace=tmp_path)
    # read_scope resolves the workspace key, so use explicit all-projects for this synthetic store.
    with bind_context(c):
        result = await recall.handler({'query': 'cedar', 'all_projects': True})
    text = result['content'][0]['text']
    assert 'fresh' in text and 'expired' not in text and 'invalid' not in text
    assert store.get_memory('expired') and store.get_memory('invalid')
    assert store.get_memory('fresh')['access_count'] == 1


@pytest.mark.asyncio
async def test_actual_recall_abstains_and_database_failure_is_not_empty(store, tmp_path, monkeypatch):
    fact(store, 'fresh')
    c = ToolContext(store=store, working=None, browser=None, session_id='synthetic', workspace=tmp_path)
    with bind_context(c):
        result = await recall.handler({'query': 'absentword', 'all_projects': True})
        assert 'No memories found' in result['content'][0]['text']
        assert 'fresh' not in result['content'][0]['text']
        def fail(*args, **kwargs):
            raise ValueError('active scan budget exhausted')
        monkeypatch.setattr(store, 'search_active_memories', fail)
        failure = await recall.handler({'query': 'cedar', 'all_projects': True})
        assert failure.get('is_error') and 'budget exhausted' in failure['content'][0]['text']


def test_startup_and_live_state_exclude_stale_but_preserve_original(store):
    fact(store, 'fresh', salience=2)
    fact(store, 'expired', salience=99,
         lifecycle={'version': 1, 'expires_at': '2020-01-01T00:00:00Z'}, persist_markdown=True)
    assert [r['slug'] for r in system_prompt._top_memories(store, ('alpha',))] == ['fresh']
    wake = system_prompt._wake_context(store, 'synthetic', project='alpha')
    assert 'expired' not in wake and 'fresh' in wake
    assert longterm.path_for(store.get_memory('expired')).exists()


def test_active_wake_preflights_oversized_provenance_and_scopes(store):
    fact(store, 'fresh')
    fact(store, 'oversized', salience=99)
    store._conn.execute('UPDATE memories SET source_session=? WHERE slug=?', ('x' * 100000, 'oversized'))
    store._conn.commit()
    assert [r['slug'] for r in store.top_memories(active=True, scope=('alpha',))] == ['fresh']
    assert store.top_memories(active=True, scope=('beta',)) == []


def test_bounded_scan_failure_reports_unavailable_and_allows_prompt_startup(store, monkeypatch):
    import sqlite3
    def exhausted(**kwargs):
        raise sqlite3.OperationalError('interrupted')
    monkeypatch.setattr(store, 'top_memories', exhausted)
    prompt = system_prompt.build_system_prompt(store, 'synthetic')
    assert 'Memory selection unavailable' in prompt
    assert 'not evidence that no memories exist' in prompt


def test_startup_marks_conflicting_supersession_as_unverified(store):
    fact(store, 'conflict', lifecycle={'version': 1, 'superseded_by': 'missing-target'})
    prompt = system_prompt._wake_context(store, 'synthetic', project='alpha')
    assert 'lifecycle conflict: verify before relying on this fact' in prompt


def test_index_only_conflict_is_explicit(store):
    for i in range(8):
        fact(store, f'fresh-{i}', salience=20)
    fact(store, 'conflict', salience=1, lifecycle={'version': 1, 'superseded_by': 'missing-target'})
    wake = system_prompt._wake_context(store, 'synthetic', project='alpha')
    index = wake.split('**Memory index**', 1)[1]
    assert 'conflict' in index and 'lifecycle conflict: verify before relying on this fact' in index
