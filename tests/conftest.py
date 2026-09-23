"""Shared fixtures. The embedding model is loaded once per test session."""

import pytest

import dream.config as config
from dream.memory.embeddings import Embedder, Reranker


def pytest_addoption(parser):
    parser.addoption("--with-models", action="store_true", default=False,
                     help="Allow tests to download/load embedding and reranking models")


@pytest.fixture(scope="session", autouse=True)
def _model_loading_opt_in(request):
    if request.config.getoption("--with-models"):
        yield
        return
    import sys
    original_embed = Embedder._ensure
    original_rerank = Reranker._ensure
    def guarded(original, module_name, class_name):
        def requires_model(self):
            module = sys.modules.get(module_name)
            factory = getattr(module, class_name, None)
            # Tests that provide a fake factory still exercise real locking and
            # recovery. Only loading the actual model requires the opt-in.
            if factory is not None and not getattr(factory, "__module__", "").startswith("fastembed"):
                return original(self)
            pytest.skip("model-loading check: opt in with --with-models after resource preflight")
        return requires_model
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Embedder, "_ensure", guarded(original_embed, "fastembed", "TextEmbedding"))
        patch.setattr(Reranker, "_ensure", guarded(original_rerank, "fastembed.rerank.cross_encoder", "TextCrossEncoder"))
        yield


@pytest.fixture(autouse=True)
def _isolate_runtime_settings(tmp_path, monkeypatch):
    """Keep settings, the default database, and transcript files inside each test."""
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data)
    # These paths were derived at config import time; changing DATA_DIR alone
    # leaves WorkingMemory and default Engine stores pointing at live data.
    monkeypatch.setattr(config, "SESSIONS_DIR", data / "sessions")
    monkeypatch.setattr(config, "DB_PATH", data / "dream.db")
    monkeypatch.setenv("DREAM_EXTENSION_SETTINGS", str(tmp_path / "extensions.json"))


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """Tests build real Engines whose _log_stderr writes to config.LOG_DIR — send
    that at a temp dir so the suite never pollutes the live var/logs/cli.log (which
    once made fake test errors look like real consolidation 500s)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(config, "LOG_DIR", log_dir)


@pytest.fixture(autouse=True)
def _isolate_memory(tmp_path, monkeypatch):
    """Phase 9 made memory/ the source of truth, and every memory write goes
    straight to config.MEMORY_DIR — so a test that forgets to redirect it would
    file test facts into the user's real memory and migrate their real files early
    (it happened once). Every test gets its own tree; a test that wants a
    specific layout still monkeypatches over this."""
    root = tmp_path / "memory"
    root.mkdir()
    monkeypatch.setattr(config, "MEMORY_DIR", root)
    monkeypatch.setattr(config, "SEMANTIC_DIR", root / "semantic")
    monkeypatch.setattr(config, "PROCEDURAL_DIR", root / "procedural")
    monkeypatch.setattr(config, "EPISODIC_DIR", root / "episodic")
    monkeypatch.setattr(config, "MEMORY_INDEX_FILE", root / "MEMORY.md")
    monkeypatch.setattr(config, "IDENTITY_FILE", root / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", root / "THREADS.md")
    monkeypatch.setattr(config, "INSTRUCTIONS_FILE", root / "INSTRUCTIONS.md")


@pytest.fixture(autouse=True)
def _isolate_plugins(monkeypatch):
    """plugins.load() -- an Engine boot, extensions reviewing a module -- fills a module-global
    roster from the checkout's plugins/ directory (understand-anything's ten agents today) and
    nothing unloads it, so those plugin agents followed the suite into tests/test_local_subagents.py
    (DREAM-099). Every test starts from the roster it inherited and hands it back afterwards; a
    test that loads plugins still sees them for its own duration."""
    from dream import plugins
    for name in ("_LOADED", "_WARNINGS", "_EXTRA_WARNINGS"):
        monkeypatch.setattr(plugins, name, list(getattr(plugins, name)))


@pytest.fixture(scope="session")
def session_embedder():
    e = Embedder()
    if not e.available():
        pytest.skip("embedder unavailable")
    return e


@pytest.fixture(autouse=True)
def _guard_hardware_telemetry(tmp_path):
    from telemetry_guard import guard_telemetry

    with guard_telemetry(tmp_path) as guard:
        yield guard
