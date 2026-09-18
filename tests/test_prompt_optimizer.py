"""Prompt drafting is bounded and never executes the underlying request."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream import prompt_optimizer as po
from dream.core.backends.base import Event
from dream.core.evaluator import ReviewSettings
from dream.core.providers import get_provider


@pytest.mark.parametrize('body', [None, [], {}, {'draft': 1}, {'draft': ' '},
    {'draft': 'x', 'sources_only': 'false'}, {'draft': 'x', 'reasoning': 'max'},
    {'draft': 'x', 'target': 'secret'}, {'draft': 'x', 'answers': [{}]},
    {'draft': 'x'*16001}])
def test_invalid_input_is_visible(body):
    with pytest.raises(ValueError):
        po.validate_request(body)


def test_quick_preserves_explicit_details_and_does_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(po.ScopedReader, 'read', lambda *a: pytest.fail('quick must not read'))
    result = po.quick_structure({'draft': 'Do not execute. Budget $17; path a/b.',
                                'constraints': 'Ask before editing.', 'mode': 'quick'},
                               [{'name': 'photo.png', 'path': 'uploads/photo.png', 'size': 12}])
    assert 'Budget $17; path a/b.' in result['prompt']
    assert 'Ask before editing.' in result['prompt']
    assert result['questions'] == []
    assert result['evidence'][0]['status'] == 'metadata_only'
    assert 'mode' not in po.validate_request({'draft': 'x', 'mode': 'quick', 'metadata': {}})


class Backend:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.prompt = None
    async def connect(self): pass
    async def disconnect(self): self.closed = True
    async def ask(self, prompt):
        self.prompt = prompt
        yield Event('assistant_done', self.response)
        yield Event('result', {'is_error': False, 'subtype': 'success'})


def setup_backend(monkeypatch, response):
    backend = Backend(response)
    def factory(settings, tools, system, cwd):
        assert tools == []
        assert 'source data' in system
        assert Path(cwd).is_dir()
        assert not list(Path(cwd).iterdir())
        return backend
    monkeypatch.setattr(po, 'review_backend', factory)
    return backend


def response(prompt='GOAL\nDraft a concise plan.'):
    return json.dumps({'prompt': prompt, 'questions': [], 'notes': []})


@pytest.mark.asyncio
async def test_model_reads_only_selected_text_and_preserves_details(monkeypatch, tmp_path):
    (tmp_path/'chosen.txt').write_text('Selected evidence')
    (tmp_path/'other.txt').write_text('PRIVATE UNSELECTED')
    backend = setup_backend(monkeypatch, response())
    result = await po.optimize({'draft': 'Do not execute; cost limit $17.'},
        [{'name':'chosen.txt','path':'chosen.txt','size':17}], tmp_path,
        ReviewSettings(get_provider('openai'), 'fixture'))
    assert 'Selected evidence' in backend.prompt
    assert 'PRIVATE UNSELECTED' not in backend.prompt
    assert 'cost limit $17.' in backend.prompt
    assert result['prompt'] == 'GOAL\nDraft a concise plan.'
    assert any('Review the proposed edits' in note for note in result['notes'])
    assert result['evidence'][0]['status'] == 'excerpt'
    assert backend.closed


@pytest.mark.parametrize('raw', ['not json', '{}', response(''),
    json.dumps({'prompt':'GOAL\nx','questions':[{'question':'x','reason':'y'}]*4,'notes':[]}),
    json.dumps({'prompt':'GOAL\nx','questions':[],'notes':'oops'}), 'x'*48001])
@pytest.mark.asyncio
async def test_invalid_output_never_silently_becomes_template(monkeypatch,tmp_path,raw):
    backend = setup_backend(monkeypatch, raw)
    with pytest.raises((ValueError, RuntimeError)):
        await po.optimize({'draft':'Write a plan'}, [], tmp_path, ReviewSettings(get_provider('openai'),'fixture'))
    assert backend.closed


@pytest.mark.asyncio
async def test_symlink_and_image_not_read_as_evidence(monkeypatch,tmp_path):
    (tmp_path/'private.txt').write_text('SECRET')
    (tmp_path/'selected.txt').symlink_to(tmp_path/'private.txt')
    (tmp_path/'photo.png').write_bytes(b'SECRET IMAGE')
    backend=setup_backend(monkeypatch,response())
    result=await po.optimize({'draft':'Describe selected sources'},
        [{'name':'selected.txt','path':'selected.txt','size':6},
         {'name':'photo.png','path':'photo.png','size':12}],tmp_path,
        ReviewSettings(get_provider('openai'),'fixture'))
    assert 'SECRET' not in backend.prompt
    assert [e['status'] for e in result['evidence']] == ['unavailable','metadata_only']


@pytest.mark.asyncio
async def test_cli_uses_direct_consultation_and_closes(monkeypatch,tmp_path):
    monkeypatch.setattr(po,'review_backend',lambda *a: pytest.fail('CLI review protocol forbidden'))
    class CLI:
        closed=False
        def __init__(self,provider,model,**kw): assert model=='pinned'
        def prepare(self): pass
        async def run(self,prompt):
            assert 'VERDICT' not in prompt
            return response()
        def close(self): CLI.closed=True
    monkeypatch.setattr(po,'CLIConsultation',CLI)
    await po.optimize({'draft':'Write a plan'},[],tmp_path,ReviewSettings(get_provider('codex'),'pinned'))
    assert CLI.closed


@pytest.mark.asyncio
async def test_provider_failure_redacts_secret_and_closes(monkeypatch,tmp_path):
    backend=setup_backend(monkeypatch,response())
    async def connect(): raise RuntimeError('api-key SECRET')
    backend.connect=connect
    with pytest.raises(RuntimeError) as error:
        await po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('openai'),'fixture'))
    assert 'SECRET' not in str(error.value)
    assert backend.closed


@pytest.mark.asyncio
async def test_cancel_closes_owned_backend(monkeypatch,tmp_path):
    backend=setup_backend(monkeypatch,response())
    entered=asyncio.Event()
    async def ask(prompt):
        entered.set()
        await asyncio.Event().wait()
        yield Event('assistant_done','unreachable')
    backend.ask=ask
    task=asyncio.create_task(po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('openai'),'fixture')))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert backend.closed


@pytest.mark.asyncio
async def test_cli_prepare_failure_still_closes_and_hides_details(monkeypatch,tmp_path):
    class CLI:
        closed=False
        def __init__(self,*a,**kw): pass
        def prepare(self): raise ValueError('SECRET credential')
        def close(self): CLI.closed=True
    monkeypatch.setattr(po,'CLIConsultation',CLI)
    with pytest.raises(RuntimeError) as error:
        await po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('codex'),'pinned'))
    assert 'SECRET' not in str(error.value)
    assert CLI.closed


@pytest.mark.asyncio
async def test_oversized_file_and_outside_path_are_not_evidence(monkeypatch,tmp_path):
    (tmp_path/'large.txt').write_bytes(b'X'*1048577)
    backend=setup_backend(monkeypatch,response())
    result=await po.optimize({'draft':'Write'},[
        {'name':'large.txt','path':'large.txt'}, {'name':'outside.txt','path':'../outside.txt'}],
        tmp_path,ReviewSettings(get_provider('openai'),'fixture'))
    assert all(e['status']=='unavailable' for e in result['evidence'])
    assert 'XXXX' not in backend.prompt


@pytest.mark.asyncio
async def test_evidence_total_is_bounded_and_unselected_file_never_opened(monkeypatch,tmp_path):
    for i in range(8): (tmp_path/f'{i}.txt').write_text(str(i)*9000)
    setup_backend(monkeypatch,response())
    result=await po.optimize({'draft':'Write'},[
        {'name':f'{i}.txt','path':f'{i}.txt'} for i in range(8)],
        tmp_path,ReviewSettings(get_provider('openai'),'fixture'))
    assert sum(len(e.get('excerpt','')) for e in result['evidence']) == 16000
    assert all(e.get('truncated') for e in result['evidence'][:4])
    assert all(e['status']=='metadata_only' for e in result['evidence'][4:])


@pytest.mark.parametrize('raw', [
    '{"prompt":"GOAL\\nx","prompt":"GOAL\\ny","questions":[],"notes":[]}',
    json.dumps({'prompt':'GOAL\n\x00','questions':[],'notes':[]}),
    json.dumps({'prompt':'Here is the finished solution, executed.','questions':[],'notes':[]}),
])
@pytest.mark.asyncio
async def test_malformed_or_unstructured_draft_is_rejected(monkeypatch,tmp_path,raw):
    setup_backend(monkeypatch,raw)
    with pytest.raises(ValueError):
        await po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('openai'),'fixture'))


@pytest.mark.parametrize('terminal', [None, {}, {'is_error':False,'subtype':'length'}, {'is_error':False,'subtype':'success','duplicate':True}])
@pytest.mark.asyncio
async def test_http_requires_single_success_terminal(monkeypatch,tmp_path,terminal):
    backend=setup_backend(monkeypatch,response())
    async def ask(prompt):
        yield Event('assistant_done',response())
        if terminal is not None:
            yield Event('result',terminal)
            if terminal.get('duplicate'): yield Event('result',terminal)
    backend.ask=ask
    with pytest.raises(RuntimeError):
        await po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('openai'),'fixture'))
    assert backend.closed


@pytest.mark.parametrize('terminal', [True,False])
@pytest.mark.asyncio
async def test_real_sdk_adapter_uses_no_tools_and_requires_its_terminal(monkeypatch,tmp_path,terminal):
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    from dream.core.evaluator import review_backend
    captured={}
    async def query(*,prompt,options):
        captured['options']=options
        async for message in prompt: captured['prompt']=message
        yield AssistantMessage(content=[TextBlock(text=response())],model='fixture')
        if terminal:
            yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='fixture')
    monkeypatch.setattr(po,'review_backend',lambda settings,tools,system,cwd:
        review_backend(settings,tools,system,cwd,sdk_query=query))
    call=po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('anthropic'),'pinned'))
    if terminal:
        assert (await call)['prompt'].startswith('GOAL')
    else:
        with pytest.raises(RuntimeError): await call
    options=captured['options']
    assert options.tools == [] and options.allowed_tools == []
    assert options.setting_sources == [] and options.strict_mcp_config
    assert options.model == 'pinned'
    assert options.cwd != str(tmp_path)
    assert not Path(options.cwd).exists()


@pytest.mark.parametrize('heading', ['**GOAL**', 'GOAL:', '## GOAL', '**GOAL:**'])
@pytest.mark.asyncio
async def test_conventional_goal_headings_are_accepted(monkeypatch,tmp_path,heading):
    setup_backend(monkeypatch,response(heading+'\nWrite a plan.'))
    result=await po.optimize({'draft':'Write'},[],tmp_path,ReviewSettings(get_provider('openai'),'fixture'))
    assert 'Write a plan.' in result['prompt']


def test_quick_rejects_output_above_editable_limit():
    body={'draft':'x'*16000,'ideal_output':'y'*8000,'context':'z'*8000,
          'constraints':'q'*8000,'verification':'v'*7000}
    files=[{'name':'n'*1024,'path':str(i)+'.png'} for i in range(8)]
    with pytest.raises(ValueError,match='limit'):
        po.quick_structure(body,files)
