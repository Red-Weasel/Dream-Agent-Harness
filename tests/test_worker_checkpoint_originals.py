"""Host checkpoint loading returns verified originals, without a tool-sized page."""
import pytest

from dream.core.tool_artifacts import ArtifactError, ArtifactStore


def test_host_loads_exact_original_larger_than_a_tool_page(tmp_path):
    store = ArtifactStore(tmp_path, 'session', 'worker')
    text = 'original 🌙 rules\n' * 9000
    receipt = store.save(text, 'snapshot')
    assert len(text.encode()) > store.max_read_bytes
    assert store.load_original(receipt.handle) == text


@pytest.mark.parametrize('session,run', [('foreign', 'worker'), ('session', 'foreign')])
def test_host_original_cannot_cross_receiver_scope(tmp_path, session, run):
    receipt = ArtifactStore(tmp_path, 'session', 'worker').save('private snapshot', 'snapshot')
    with pytest.raises(ArtifactError, match='scope'):
        ArtifactStore(tmp_path, session, run).load_original(receipt.handle)


def test_host_original_detects_changed_saved_bytes(tmp_path):
    store = ArtifactStore(tmp_path, 'session', 'worker')
    receipt = store.save('original snapshot', 'snapshot')
    original = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    original.write_text('changed snapshot!')
    with pytest.raises(ArtifactError, match='integrity'):
        store.load_original(receipt.handle)
