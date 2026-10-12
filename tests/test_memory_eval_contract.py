"""Synthetic retrieval contract, separate from shared-corpus qualification."""
from dream.eval import build_fixture_store, run_eval


def test_eval_honors_scope_and_abstention_and_records_latency(tmp_path):
    corpus = [{'kind':'semantic','slug':'a','title':'Cedar','body':'cedar endpoint','project':'alpha'},
              {'kind':'semantic','slug':'b','title':'Cedar','body':'cedar endpoint','project':'beta'}]
    store = build_fixture_store(corpus,tmp_path/'eval.db')
    report = run_eval(store,[{'query':'cedar','expect':['a'],'scope':['alpha'],'forbidden':['b']},
                             {'query':'zzzz','expect':[],'scope':['alpha']}])
    assert report.skipped == 0
    assert report.results[0].got == ['a']
    assert report.results[1].abstained is True
    assert report.results[1].got == []
    assert report.results[0].latency_ms >= 0
    assert report.results[0].violations == []


def test_stale_forbidden_scope_latency_and_recency_contract(tmp_path):
    store=build_fixture_store([{'kind':'semantic','slug':'a','title':'Cedar','body':'cedar',
                               'project':'alpha','lifecycle':{'version':1,'expires_at':'2020-01-01T00:00:00Z'}}],tmp_path/'a.db')
    golden=[{'query':'cedar','expect':[],'scope':['alpha'],'forbidden':['a'],'max_latency_ms':-1}]
    def adapter(query,**kwargs):
        assert kwargs['scope'] == ['alpha']
        return [dict(store.get_memory('a'),project='beta'),{'slug':'browse','via_recency':True}]
    r=run_eval(store,golden,retriever=adapter).results[0]
    assert r.got == ['a']
    assert set(r.violations) == {'forbidden:a','scope:a','expired:a','latency'}
    assert not r.abstained
    active=run_eval(store,[dict(golden[0],max_latency_ms=1000)],active=True)
    assert active.results[0].abstained
    assert active.results[0].violations == []
    assert 'unavailable' in active.casesearch_status


def test_abstention_not_recall_miss_and_missing_expect_explicit_skip(tmp_path):
    s=build_fixture_store([{'kind':'semantic','slug':'a','title':'Cedar','body':'cedar'}],tmp_path/'a.db')
    r=run_eval(s,[{'query':'cedar','expect':['a']},{'query':'absent','expect':[]},
                  {'query':'other','expect':['missing']}])
    assert len(r.results) == 2 and r.skipped == 1
    assert r.recall_at(1) == r.mrr == 1
