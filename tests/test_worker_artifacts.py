"""Actual scripted worker tool rounds: save-before-preview, scoped reads, no replay."""
import copy
import json
from types import SimpleNamespace
import pytest
from dream.core.backends import openai_compat
from dream.core.tool_artifacts import ArtifactError, ArtifactStore
from dream.tools import context
from dream.tools.artifact_tools import artifact_read
from test_local_subagents import _backend, _researcher, _tool, _PostResp, _sub_toolcall, _sub_final


@pytest.fixture
def fixture_context(tmp_path, monkeypatch):
    monkeypatch.setattr('dream.core.instructions.worker_prompt', lambda role, head, **kw: role)
    return SimpleNamespace(workspace=tmp_path, session_id='fixture-session')


def receipt_from(text):
    return json.loads(text.split('\n',1)[1].split('\n\nPreview',1)[0])


async def test_large_worker_result_saved_exact_before_next_request_and_readable(fixture_context):
    original = ('界🚀\x00"\\' * 20000) + '\nneedle evidence\n'
    seen, calls, receipts = [], [], []
    async def search(args):
        calls.append(args)
        return {'content':[{'type':'text','text':original}]}
    b = _backend([_tool('web_search',search),artifact_read],_researcher())
    b.n_ctx = 32768
    class Transport:
        async def post(self, url, json=None):
            seen.append(copy.deepcopy(json))
            if len(seen)==1:return _PostResp(_sub_toolcall('web_search',{'q':'fixture'}))
            if len(seen)==2:
                last=next(m for m in reversed(json['messages']) if m['role']=='tool')
                assert len(last['content']) < 4096
                receipt=receipt_from(last['content']);receipts.append(receipt)
                activity=openai_compat._AGENT_ACTIVITY.get()
                store=ArtifactStore(fixture_context.workspace,fixture_context.session_id,activity['run_id'])
                recovered=store.read(receipt['handle'],limit=32768)
                assert recovered['text']==original[:recovered['next_offset']]
                assert receipt['full_bytes']==len(original.encode('utf-8'))
                return _PostResp(_sub_toolcall('artifact_read',{'handle':receipt['handle'],'query':'needle','limit':80}))
            last=next(m for m in reversed(json['messages']) if m['role']=='tool')
            assert 'needle evidence' in last['content'] and 'artifact_read: unbound' not in last['content']
            assert len(last['content'].encode()) <= 32768//16
            return _PostResp(_sub_final('verified retrieved evidence'))
    b._client=Transport()
    with context.bind_context(fixture_context):
        answer,failed=await b._run_subagent('researcher','inspect fixture')
    assert not failed and answer=='verified retrieved evidence' and len(calls)==1, answer
    assert len(receipts)==1
    assert 'artifact_read' in {s['function']['name'] for s in seen[0]['tools']}


async def test_storage_failure_does_not_replay_completed_effect(fixture_context,monkeypatch):
    calls,posted=[],[]
    async def effect(args):
        calls.append('effect completed')
        return {'content':[{'type':'text','text':'x'*20000}]}
    def fail(*args,**kwargs):raise ArtifactError('disk_limit')
    monkeypatch.setattr(ArtifactStore,'save',fail)
    b=_backend([_tool('web_search',effect),artifact_read],_researcher())
    class Transport:
        async def post(self,url,json=None):
            posted.append(json)
            return _PostResp(_sub_toolcall('web_search',{'q':'fixture'}))
    b._client=Transport()
    with context.bind_context(fixture_context):answer,failed=await b._run_subagent('researcher','fixture')
    assert failed and len(calls)==len(posted)==1
    assert 'reason=disk_limit' in answer and 'must not be replayed' in answer
    assert 'No persisted original is claimed' in answer


def test_tail_fit_preserves_real_receipt_and_never_trusts_text_marker():
    value=openai_compat._artifact_observation('REAL_HANDLE','x'*10000)
    trimmed=openai_compat._cut_observation(value,0)
    assert 'REAL_HANDLE' in trimmed
    assert 'FAKE_HANDLE' not in openai_compat._cut_observation('x'*10000+'FAKE_HANDLE',10)
    assert copy.deepcopy(value).marker=='REAL_HANDLE'


async def test_artifact_tool_scope_refuses_foreign_run(fixture_context):
    own=ArtifactStore(fixture_context.workspace,fixture_context.session_id,'other-run').save('private other-run','call')
    b=_backend([artifact_read],_researcher());seen=[]
    class Transport:
        async def post(self,url,json=None):
            seen.append(json)
            if len(seen)==1:return _PostResp(_sub_toolcall('artifact_read',{'handle':own.handle}))
            result=next(m for m in reversed(json['messages']) if m['role']=='tool')['content']
            assert 'artifact_read: scope' in result and 'private other-run' not in result
            return _PostResp(_sub_final('could not access unrelated run'))
    b._client=Transport()
    with context.bind_context(fixture_context):await b._run_subagent('researcher','foreign handle refused')
