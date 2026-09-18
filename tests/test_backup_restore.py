import json
import pytest
from dream import backup
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from dream.library.store import Library


def test_restore_preserves_task_history_and_library_versions(tmp_path):
    root = tmp_path / "original"
    store = MemoryStore(root / "data/dream.db")
    tasks = TaskStore(store)
    task = tasks.add("Verify report", "Initial note")
    tasks.update(task["id"], status="done", append_note="Verified evidence")
    library = Library(root / "data/library/library.db", root / "data/library/blobs")
    artifact = library.create(b"version one", name="memo.md")
    library.replace(artifact.id, b"version two")
    (root / "memory").mkdir()
    (root / "memory/preference.md").write_text("A fixture fact")
    snapshot = tmp_path / "snapshot"
    backup.create(root, snapshot)
    restored = tmp_path / "restored"
    backup.restore(snapshot, restored)
    recovered = MemoryStore(restored / "data/dream.db")
    row = TaskStore(recovered).get(task["id"])
    assert row["status"] == "done" and "Verified evidence" in row["notes"]
    archived = Library(restored / "data/library/library.db", restored / "data/library/blobs")
    assert archived.read(artifact.id, version=1)[0] == "version one"
    assert archived.read(artifact.id)[0] == "version two"
    assert (restored / "memory/preference.md").read_text() == "A fixture fact"
    store.close()
    recovered.close()
    library.close()
    archived.close()


def test_corruption_or_escape_never_restores(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "backup.json").write_text(json.dumps({"format": "dream-backup/v1", "files": [
        {"path": "../../escape", "bytes": 1, "sha256": "fake"}]}))
    with pytest.raises(ValueError, match="path"):
        backup.restore(snapshot, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
