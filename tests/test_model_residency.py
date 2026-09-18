"""Model-free loading-policy and server allocation-report regressions."""
from types import SimpleNamespace
import pytest
from dream.local import machx
from dream.local.model_defaults import recommend


def response(monkeypatch, payload):
    monkeypatch.setattr(machx.httpx, 'get', lambda *a, **k: SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None))


def test_actual_residency_is_reported_not_assumed(monkeypatch):
    response(monkeypatch, {'memory_residency': {'host_pinned_bytes': 128*2**30,
        'host_mmap_bytes': 48*2**30, 'gpu_expert_cache_bytes': [21*2**30,22*2**30]}})
    text=machx.residency_summary()
    assert '128.0 GiB pinned' in text and '48.0 GiB' in text
    assert '21.0 / 22.0 GiB' in text and 'partial' in text.lower()
    assert 'may read from disk' in text


def test_fully_pinned_report(monkeypatch):
    response(monkeypatch, {'memory_residency': {'host_pinned_bytes': 176*2**30,
        'host_mmap_bytes': 0, 'gpu_expert_cache_bytes': [21*2**30,22*2**30]}})
    assert 'all active expert banks pinned' in machx.residency_summary()


@pytest.mark.parametrize('payload', [{}, {'memory_residency':None},
    {'memory_residency':{'host_pinned_bytes':True,'host_mmap_bytes':0,'gpu_expert_cache_bytes':[1]}},
    {'memory_residency':{'host_pinned_bytes':-1,'host_mmap_bytes':0,'gpu_expert_cache_bytes':[1]}},
    {'memory_residency':{'host_pinned_bytes':0,'host_mmap_bytes':0,'gpu_expert_cache_bytes':[True]}},
    {'memory_residency':{'host_pinned_bytes':0,'host_mmap_bytes':2**64,'gpu_expert_cache_bytes':[1]}}])
def test_missing_or_invalid_stats_do_not_claim_residency(monkeypatch,payload):
    response(monkeypatch,payload)
    assert machx.residency_summary() is None


def test_load_summary_includes_backend_memory_policy(tmp_path):
    caps={'architecture':'glm5next','memory_policy':{'host_banks':'pinned_auto',
        'expert_cache_bytes':0,'host_floor_gib':40},'defaults':{},'features':{}}
    rec=recommend(tmp_path/'missing.gguf',caps,hardware={'devices':[]})
    assert any('pinning enabled' in s and 'automatic GPU' in s for s in rec['notes'])
    caps['memory_policy']['host_banks']='mmap'
    rec=recommend(tmp_path/'missing.gguf',caps,hardware={'devices':[]})
    assert any('pinning disabled' in s for s in rec['notes'])


def test_old_backend_does_not_look_fixed(tmp_path):
    rec=recommend(tmp_path/'missing.gguf',{'architecture':'glm5next'},hardware={'devices':[]})
    assert any('Rebuild MachX' in s for s in rec['notes'])
