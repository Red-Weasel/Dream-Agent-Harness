"""A real child process exits mid-chat; reopening must not replay its effect."""
import os
from pathlib import Path
import subprocess
import sys

from dream import config
from test_project_handoff import archive  # noqa: F401


def test_abrupt_engine_exit_retains_unknown_outcome_and_existing_effect(archive, tmp_path):
    library, project, store = archive
    script = r'''
import asyncio, os, sys
from pathlib import Path
from dream import config
from dream.core.engine import Engine
from dream.core.providers import Provider
from dream.core.backends.base import Event
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext

workspace = Path(sys.argv[2])
class Backend:
    async def ask(self, prompt):
        # A deterministic local effect occurred before the process died.
        (workspace / 'completed-step.txt').write_text('one completed step')
        yield Event('assistant_done', 'Saved completed-step.txt; further checks pending.')
        os._exit(23)  # Deliberately bypass finally: this is the crash boundary.

async def main():
    engine = Engine(provider=Provider('fixture', 'Fixture', 'openai',
                    base_url='http://fixture.invalid/v1'), model='fixture', workspace=workspace)
    engine.session_id = 'session-a'
    engine.store = MemoryStore(Path(sys.argv[1]))
    engine.working = WorkingMemory(engine.store, engine.session_id)
    engine._tool_context = ToolContext(engine.store, engine.working, None,
                                      engine.session_id, workspace=workspace)
    engine.backend = Backend()
    engine._started = True
    async for event in engine.ask_chat('Continue the existing work'):
        pass
asyncio.run(main())
'''
    private = tmp_path / 'crash-runtime'
    private.mkdir()
    env = {key: value for key, value in os.environ.items() if key in ('PATH', 'LANG', 'TERM')}
    for key in ('HOME', 'DREAM_ROOT', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR'):
        path = private / key
        path.mkdir(mode=0o700)
        env[key] = str(path)
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               DREAM_SEMANTIC_MEMORY='0', DREAM_RERANK='0', DREAM_CONSOLIDATE='0',
               DREAM_MONITOR='0', DREAM_GUI_OPEN='0', DREAM_SEARXNG_AUTOSTART='0',
               CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='', ROCR_VISIBLE_DEVICES='',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    child = subprocess.run([sys.executable, '-c', script, str(config.DB_PATH),
                            project['workspace']], env=env, capture_output=True, text=True, timeout=20)
    assert child.returncode == 23, child.stderr
    artifact = Path(project['workspace']) / 'completed-step.txt'
    assert artifact.read_text() == 'one completed step'
    before = artifact.stat().st_mtime_ns
    reopened = type(library)(library.path)
    report = reopened.transcript(project['id'], 'session-a')
    assert report['recovery']['state'] == 'unknown'
    assert report['recovery']['needs_inspection'] is True
    assert any(turn['role'] == 'assistant' and 'further checks pending' in turn['content']
               for turn in report['turns'])
    for text in (reopened.restored_context(project['id'], 'session-a'),
                 reopened.handoff(project['id'], 'session-a')['document']['content']):
        assert 'UNKNOWN' in text
        assert 'explicit continuation' in text
    assert artifact.stat().st_mtime_ns == before
    assert artifact.read_text() == 'one completed step'
