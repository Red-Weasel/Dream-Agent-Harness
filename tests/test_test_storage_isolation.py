"""Exercise default test storage paths before any fixture can write live state."""
import json
from pathlib import Path

import pytest

from dream import config
from dream.core import engine as engine_module
from dream.core.engine import Engine
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory


def test_working_memory_writes_transcript_under_test_data(tmp_path):
    # Fail before constructing WorkingMemory if the shared fixture regresses.
    assert config.SESSIONS_DIR.is_relative_to(tmp_path), 'Session logs escape the test directory'
    store = MemoryStore(tmp_path / 'working.db')
    try:
        store.start_session('storage-isolation-fixture')
        working = WorkingMemory(store, 'storage-isolation-fixture')
        working.log_turn('user', 'storage isolation fixture')
        assert working.log_path.parent == config.DATA_DIR / 'sessions'
        assert json.loads(working.log_path.read_text())['content'] == 'storage isolation fixture'
        assert store.session_turns('storage-isolation-fixture')[0]['content'] == 'storage isolation fixture'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_engine_initializes_database_and_transcript_in_test_directory(tmp_path, monkeypatch):
    # Guards make the red test safe even before default DB/session isolation exists.
    assert config.DB_PATH.is_relative_to(tmp_path), 'Database escapes the test directory'
    assert config.SESSIONS_DIR.is_relative_to(tmp_path), 'Session logs escape the test directory'
    monkeypatch.setattr(config, 'SEMANTIC_MEMORY', False)
    for name in ('VAR_DIR', 'LOOP_DIR', 'SCREENSHOT_DIR', 'CUSTOM_TOOLS_DIR'):
        monkeypatch.setattr(config, name, tmp_path / name.lower())

    class StorageInitialized(Exception):
        pass

    def stop_before_browser_and_provider():
        raise StorageInitialized

    # Run real Engine storage initialization, stopping at its first external edge.
    monkeypatch.setattr(engine_module, 'get_browser', stop_before_browser_and_provider)
    engine = Engine(provider='machx', model='storage-fixture', workspace=tmp_path)
    try:
        with pytest.raises(StorageInitialized):
            await engine._start()
        assert Path(engine.store.db_path) == config.DATA_DIR / 'dream.db'
        assert engine.store.get_session(engine.session_id)['id'] == engine.session_id
        engine.working.log_turn('assistant', 'storage initialization complete')
        assert engine.working.log_path.parent == config.DATA_DIR / 'sessions'
        assert json.loads(engine.working.log_path.read_text())['content'] == 'storage initialization complete'
        assert engine.backend is None
    finally:
        if engine.store:
            engine.store.close()
