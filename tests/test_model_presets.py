"""Model-free defaults and persistence regressions."""
import json
import struct
from pathlib import Path
import pytest
from dream.local.settings import available_controls

def test_backend_defaults_are_not_replaced_by_ui_constants():
    caps={'sampling':['max_tokens','temperature','top_k'],'load':['threads','prefill_chunk'],
          'defaults':{'max_tokens':16384,'temperature':1.,'top_k':0,'threads':0,'prefill_chunk':256}}
    c={x.name:x for x in available_controls(caps)}
    assert c['max_tokens'].default==16384
    assert c['temperature'].default==1.
    assert c['top_k'].default==0
    assert c['threads'].default is None
    assert c['prefill_chunk'].default==256

@pytest.mark.parametrize('value',[True,-1,'oops',float('nan')])
def test_invalid_backend_defaults_fail_visibly(value):
    with pytest.raises(ValueError):
        available_controls({'sampling':['temperature'],'defaults':{'temperature':value}})

def gguf(path, entries):
    data=b'GGUF'+struct.pack('<IQQ',3,0,len(entries))
    for key,value in entries.items():
        key=key.encode();data+=struct.pack('<Q',len(key))+key
        if isinstance(value,str):
            b=value.encode();data+=struct.pack('<IQ',8,len(b))+b
        else:data+=struct.pack('<II',4,value)
    path.write_bytes(data)
    return path

@pytest.mark.parametrize("model_name", ["GLM-5.3-Flash", "GLM 5.3 Flash"])
def test_metadata_reads_model_limits_not_filename(tmp_path,model_name):
    from dream.local.model_defaults import read_metadata, recommend
    path=gguf(tmp_path/'unrelated.gguf',{'general.architecture':'glm5next','general.name':model_name,'glm5next.context_length':1048576})
    meta=read_metadata(path)
    assert meta['glm5next.context_length']==1048576
    caps={'architecture':'glm5next','sampling':['temperature','top_p','max_tokens'], 'defaults':{'max_tokens':16384},'max_gpus':2}
    rec=recommend(path,caps,hardware={'devices':[{'name':'B70','vram_total_mib':32768,'integrated':False}]*2,'ram_total_gb':512})
    assert rec['gpus']==2
    assert 8192<rec['ctx']<=1048576
    assert rec['options']['temperature']==1.0
    assert rec['options']['top_p']==1.0
    assert 'publisher' in rec['sources']['temperature'].lower()
    assert rec['context_limit']==1048576

def test_unknown_models_and_small_limits_are_conservative(tmp_path):
    from dream.local.model_defaults import recommend,read_metadata
    p=gguf(tmp_path/'GLM-5.3-Flash.gguf',{'general.architecture':'other','other.context_length':2048})
    r=recommend(p,{'sampling':['temperature','max_tokens'],'max_gpus':1},hardware={'devices':[]})
    assert r['ctx']==2048 and r['options']['max_tokens']<=1024
    assert r['options']['temperature']==.7
    p.write_bytes(b'GGUF'+struct.pack('<IQQ',3,0,1)+struct.pack('<Q',2**60))
    with pytest.raises(ValueError):read_metadata(p)

def test_presets_are_exact_model_scoped_atomic_and_conflict_checked(tmp_path):
    from dream.local.model_presets import Presets
    a=tmp_path/'a.gguf';b=tmp_path/'b.gguf';a.write_bytes(b'fixture');b.write_bytes(b'fixture')
    store=Presets(tmp_path/'presets.json')
    assert store.load(a) is None
    value={'gpus':2,'ctx':200000,'options':{'temperature':.3}}
    token=store.save(a,value,expected=None)
    assert store.load(a)['selection']==value
    assert store.load(b) is None
    with pytest.raises(ValueError,match='changed'):store.save(a,value,expected=None)
    store.save(b,value,expected=None)
    store.clear(a,expected=token)
    assert store.load(a) is None and store.load(b)
    assert (tmp_path/'presets.json').stat().st_mode & 0o777 == 0o600

def test_model_replacement_and_corruption_do_not_reuse_old_values(tmp_path):
    from dream.local.model_presets import Presets
    p=tmp_path/'m.gguf';p.write_bytes(b'one');s=Presets(tmp_path/'settings.json')
    s.save(p,{'gpus':1,'ctx':8192,'options':{}},expected=None)
    p.write_bytes(b'replacement')
    assert s.load(p) is None
    (tmp_path/'settings.json').write_text('{bad')
    with pytest.raises(ValueError):s.load(p)
    assert (tmp_path/'settings.json').read_text()=='{bad'


@pytest.mark.asyncio
async def test_picker_loads_recommendations_and_saved_selection(monkeypatch,tmp_path):
    from dream.local import launcher,model_defaults
    from dream.local.model_presets import Presets
    from io import StringIO
    from rich.console import Console
    from types import SimpleNamespace
    monkeypatch.setenv('DREAM_MODEL_PRESETS',str(tmp_path/'presets.json'))
    monkeypatch.setattr(model_defaults,'inspect_hardware',lambda:{'devices':[]})
    p=gguf(tmp_path/'m.gguf',{'general.architecture':'test','test.context_length':65536})
    caps={'sampling':['temperature','max_tokens'],'defaults':{'max_tokens':16384}}
    prompts=[]
    async def ask(prompt,**kw):prompts.append(prompt);return ''
    monkeypatch.setattr(launcher,'_ask',ask)
    output=StringIO();r=SimpleNamespace(system=lambda _:None,error=lambda msg:pytest.fail(msg))
    selected,old=await launcher._choose_model_settings(Console(file=output,width=160),r,p,caps)
    assert selected['ctx']==32768 and selected['options']['max_tokens']==16384
    assert len(prompts)==1
    assert 'source' in output.getvalue() and 'MachX backend' in output.getvalue()
    selected.update(gpus=1,ctx=20000);selected['options']['temperature']=.2
    Presets().save(p,selected,expected=None)
    selected,old=await launcher._choose_model_settings(Console(file=StringIO()),r,p,caps)
    assert selected['ctx']==20000 and selected['options']['temperature']==.2
    assert old is not None

@pytest.mark.asyncio
async def test_reset_then_cancel_preserves_saved_preset(monkeypatch,tmp_path):
    from dream.local import launcher,model_defaults
    from dream.local.model_presets import Presets
    from rich.console import Console
    from io import StringIO
    from types import SimpleNamespace
    monkeypatch.setenv('DREAM_MODEL_PRESETS',str(tmp_path/'presets.json'))
    monkeypatch.setattr(model_defaults,'inspect_hardware',lambda:{'devices':[]})
    p=gguf(tmp_path/'m.gguf',{'general.architecture':'test','test.context_length':65536})
    store=Presets();before=store.save(p,{'gpus':1,'ctx':20000,'options':{'temperature':.2}},expected=None)
    answers=iter(['reset','cancel'])
    async def ask(*a,**kw):return next(answers)
    monkeypatch.setattr(launcher,'_ask',ask)
    assert await launcher._choose_model_settings(Console(file=StringIO()),SimpleNamespace(system=lambda _:None,error=lambda _:None),p,{'sampling':['temperature']}) is None
    assert store.load(p)['revision']==before

@pytest.mark.asyncio
@pytest.mark.parametrize('ready',[False,True])
async def test_launch_saves_only_after_success(monkeypatch,tmp_path,ready):
    from dream.local import launcher,machx,preflight
    from dream.local.model_presets import Presets
    from rich.console import Console
    from io import StringIO
    from types import SimpleNamespace
    monkeypatch.setenv('DREAM_MODEL_PRESETS',str(tmp_path/'presets.json'))
    path=tmp_path/'m.gguf';path.write_bytes(b'fixture')
    choice={'gpus':1,'ctx':32768,'options':{'temperature':1.,'max_tokens':16384}}
    async def choose(*a):return choice,None
    async def harness(*a,**kw):pass
    monkeypatch.setattr(launcher,'_choose_model_settings',choose)
    monkeypatch.setattr(launcher,'_run_harness',harness)
    monkeypatch.setattr(machx,'capabilities',lambda p:{'supported':True})
    monkeypatch.setattr(preflight,'check_live',lambda *a:SimpleNamespace(should_load=True,reason='fixture'))
    calls=[]
    monkeypatch.setattr(machx,'serve',lambda p,**kw:calls.append(kw) or object())
    monkeypatch.setattr(machx,'wait_ready',lambda p:ready)
    monkeypatch.setattr(machx,'served_model_id',lambda:'fixture')
    monkeypatch.setattr(machx,'stop',lambda:None)
    r=SimpleNamespace(system=lambda _:None,error=lambda _:None)
    await launcher.serve_and_run(Console(file=StringIO()),r,'fixture',path,keep_hot=True)
    assert calls[0]['options']['max_tokens']==16384
    saved=Presets().load(path)
    assert (saved is not None)==ready
    if saved: assert saved['selection']==choice

def test_corrupt_saved_selection_is_preserved(tmp_path):
    from dream.local.model_presets import Presets,model_key
    p=tmp_path/'m.gguf';p.write_bytes(b'fixture')
    f=tmp_path/'settings.json'
    data={'version':1,'models':{model_key(p):{'selection':{},'revision':'old'}}}
    f.write_text(json.dumps(data))
    with pytest.raises(ValueError):Presets(f).load(p)
    assert json.loads(f.read_text())==data

def test_shard_identity_handles_literal_glob_characters(tmp_path):
    from dream.local.model_presets import model_key
    paths=[]
    for stem in ['model[1]','model[2]']:
        for part in [1,2]:
            p=tmp_path/f'{stem}-{part:05}-of-00002.gguf'
            p.write_bytes(b'fixture')
            if part==1:paths.append(p)
    assert model_key(paths[0])!=model_key(paths[1])

def test_related_backend_defaults_are_validated_together():
    c={x.name:x for x in available_controls({'sampling':['temperature'],
        'features':{'speculative':True},'defaults':{'temperature':0,'speculative':True}})}
    assert c['speculative'].default is True and c['temperature'].default==0

def test_save_refuses_model_identity_changed_since_selection(tmp_path):
    from dream.local.model_presets import Presets,model_key
    p=tmp_path/'m.gguf';p.write_bytes(b'original')
    key=model_key(p);p.write_bytes(b'replacement')
    store=Presets(tmp_path/'settings.json')
    with pytest.raises(ValueError,match='identity'):
        store.save(p,{'gpus':1,'ctx':8192,'options':{}},expected=None,expected_key=key)
    assert store.load(p) is None

def test_accepted_preset_options_are_normalized(tmp_path):
    from dream.local.model_presets import Presets,model_key
    p=tmp_path/'m.gguf';p.write_bytes(b'fixture');f=tmp_path/'presets.json'
    f.write_text(json.dumps({'version':1,'models':{model_key(p):{'selection':{
        'gpus':1,'ctx':8192,'options':{'max_tokens':'default'}},'revision':'manual'}}}))
    assert Presets(f).load(p)['selection']['options']['max_tokens']==4096
