"""Operator-selected generic JSON plans and actual temporary files, no models."""
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path

import pytest


def document():
    return {'version':1,'goal':'Produce a generic result','limits':{'max_dispatches':4,'max_wall_seconds':60},
        'phases':[{'id':'phase-1','goal':'Create generic result','contract':'- result bytes match',
                   'check_name':'result-file','acceptance':{'all':[{'path':'out/result.txt','text':'hello\n',
                       'min_bytes':6,'lines':1,'nonempty_lines':1,'sha256':hashlib.sha256(b'hello\n').hexdigest()}]}}]}


def save(tmp_path,data=None):
    path=tmp_path/'selected-plan.json'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o644)
    with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(document() if data is None else data,out)
    return path


def test_actual_predicates_and_definition_snapshot(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    path=save(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir()
    plan=load_phase_plan(path,workspace=workspace)
    from dream.memory.project import project_key
    assert plan.workspace==workspace and plan.project==project_key(workspace) and len(plan.phases)==1
    assert plan.source_sha256==hashlib.sha256(path.read_bytes()).hexdigest()
    assert not plan.phases[0].check()
    (workspace/'out').mkdir();(workspace/'out/result.txt').write_bytes(b'hello\n')
    assert plan.phases[0].check() is True
    original=plan.phases[0].check_version
    changed=document();changed['phases'][0]['acceptance']['all'][0]['text']='different'
    save(tmp_path,changed)
    assert plan.phases[0].check() is True and plan.phases[0].check_version==original
    reloaded=load_phase_plan(path,workspace=workspace)
    assert reloaded.phases[0].check_version!=original and reloaded.phases[0].check() is False
    with pytest.raises(FrozenInstanceError):plan.goal='changed'


@pytest.mark.parametrize('data',[
    {'version':True}, {'version':2}, {'workspace':'elsewhere'}, {'command':'echo forged'},
    {'goal':''}, {'project':None}, {'phases':[]},
    {'limits':{'max_dispatches':True,'max_wall_seconds':60}},
    {'limits':{'max_dispatches':4,'max_wall_seconds':float('inf')}},
    {'limits':{'max_dispatches':4,'max_wall_seconds':60,'extra':1}},
])
def test_unknown_or_invalid_top_level_rejected(tmp_path,data):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw.update(data)
    with pytest.raises(ValueError):load_phase_plan(save(tmp_path,raw),workspace=tmp_path)


@pytest.mark.parametrize('acceptance',[
    {'all':[]}, {'any':[{'path':'a','min_bytes':1}]}, {'all':[{'path':'a'}]},
    {'all':[{'path':'a','min_bytes':True}]}, {'all':[{'path':'a','min_bytes':-1}]},
    {'all':[{'path':'a','lines':1.0}]}, {'all':[{'path':'a','sha256':'f'*63}]},
    {'all':[{'path':'a','text':None}]}, {'all':[{'path':'a','command':'pass'}]},
    {'all':[{'path':'a','min_bytes':1}]*2},
])
def test_invalid_acceptance_schema_rejected(tmp_path,acceptance):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw['phases'][0]['acceptance']=acceptance
    with pytest.raises(ValueError):load_phase_plan(save(tmp_path,raw),workspace=tmp_path)


@pytest.mark.parametrize('path',['/absolute','../escape','a/../escape','a//result','./result','a/',
    'C:\\private','~private','a\x00b','a\nb','x/'*33+'result'])
def test_unsafe_relative_paths_rejected(tmp_path,path):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw['phases'][0]['acceptance']={'all':[{'path':path,'min_bytes':0}]}
    with pytest.raises(ValueError):load_phase_plan(save(tmp_path,raw),workspace=tmp_path)


@pytest.mark.parametrize('body',[b'{"version":1,"version":1}',b'{"nested":{"key":1,"key":2}}',b'\xff',
    b'{"version":NaN}',b'{"version":Infinity}',b'[]',b'null',b'{',b'['*2000+b']'*2000])
def test_bad_json_rejected(tmp_path,body):
    from dream.core.phase_plan import load_phase_plan
    path=tmp_path/'selected-plan.json';path.write_bytes(body)
    with pytest.raises(ValueError):load_phase_plan(path,workspace=tmp_path)


def simple(tmp_path,predicates):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw['phases'][0]['acceptance']={'all':predicates}
    return load_phase_plan(save(tmp_path,raw),workspace=tmp_path).phases[0].check


@pytest.mark.parametrize('body,lines,nonempty',[(b'',0,0),(b'\n',1,0),(b'a\n',1,1),
    (b'a\n\n',2,1),(b'a\nb',2,2),(b' \t\r\n\r\n',2,0),(b'a\rb',1,1),(b'\xff',1,1)])
def test_explicit_raw_lf_line_semantics(tmp_path,body,lines,nonempty):
    (tmp_path/'result').write_bytes(body)
    check=simple(tmp_path,[{'path':'result','lines':lines,'nonempty_lines':nonempty}])
    assert check() is True


@pytest.mark.parametrize('kind',['file_symlink','parent_symlink','hardlink','fifo','directory'])
def test_unsafe_result_types_refused_without_following(tmp_path,kind):
    outside=tmp_path/'outside';outside.mkdir();(outside/'result').write_bytes(b'ok')
    if kind=='file_symlink':(tmp_path/'result').symlink_to(outside/'result')
    elif kind=='parent_symlink':(tmp_path/'nested').symlink_to(outside,target_is_directory=True)
    elif kind=='hardlink':os.link(outside/'result',tmp_path/'result')
    elif kind=='fifo':os.mkfifo(tmp_path/'result')
    else:(tmp_path/'result').mkdir()
    path='nested/result' if kind=='parent_symlink' else 'result'
    assert simple(tmp_path,[{'path':path,'min_bytes':0}])() is False


@pytest.mark.parametrize('kind',['file_symlink','parent_symlink','hardlink','fifo','directory','oversized'])
def test_unsafe_plan_descriptor_refused(tmp_path,kind):
    from dream.core.phase_plan import load_phase_plan,MAX_PLAN_BYTES
    actual=save(tmp_path);selected=tmp_path/'selected'
    if kind=='file_symlink':selected.symlink_to(actual)
    elif kind=='parent_symlink':
        folder=tmp_path/'linked';folder.symlink_to(tmp_path,target_is_directory=True);selected=folder/actual.name
    elif kind=='hardlink':os.link(actual,selected)
    elif kind=='fifo':os.mkfifo(selected)
    elif kind=='directory':selected.mkdir()
    else:selected.write_bytes(b' '* (MAX_PLAN_BYTES+1))
    with pytest.raises((ValueError,OSError)):load_phase_plan(selected,workspace=tmp_path)


def test_changed_workspace_is_not_new_scope_and_new_result_dirs_are_allowed(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    workspace=tmp_path/'workspace';workspace.mkdir();plan=load_phase_plan(save(tmp_path),workspace=workspace)
    (workspace/'out').mkdir();(workspace/'out/result.txt').write_bytes(b'hello\n')
    assert plan.phases[0].check()
    workspace.rename(tmp_path/'original-workspace');workspace.mkdir()
    (workspace/'out').mkdir();(workspace/'out/result.txt').write_bytes(b'hello\n')
    assert plan.phases[0].check() is False


def test_workspace_ancestor_symlink_refused(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    real=tmp_path/'actual';real.mkdir();alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True)
    with pytest.raises(OSError):load_phase_plan(save(tmp_path),workspace=alias)


def test_oversized_file_and_total_reads_are_bounded(tmp_path,monkeypatch):
    import dream.core.phase_plan as module
    raw_read=module.os.read;read_bytes=[]
    def observed(fd,count):
        body=raw_read(fd,count);read_bytes.append(len(body));return body
    check=simple(tmp_path,[{'path':'large','min_bytes':1}])
    (tmp_path/'large').write_bytes(b'x'*(module.MAX_FILE_BYTES+1))
    monkeypatch.setattr(module.os,'read',observed)
    assert check() is False and read_bytes==[]
    monkeypatch.setattr(module.os,'read',raw_read)
    paths=[]
    for i in range(5):
        name=f'part-{i}';(tmp_path/name).write_bytes(b'x'*module.MAX_FILE_BYTES);paths.append({'path':name,'min_bytes':1})
    check=simple(tmp_path,paths);read_bytes.clear();monkeypatch.setattr(module.os,'read',observed)
    assert check() is False and sum(read_bytes)<=module.MAX_CHECK_BYTES


def test_replaced_file_during_read_cannot_pass(tmp_path,monkeypatch):
    import dream.core.phase_plan as module
    path=tmp_path/'result';path.write_bytes(b'ok');check=simple(tmp_path,[{'path':'result','text':'ok'}])
    raw_read=module.os.read;changed=False
    def replace_during_read(fd,count):
        nonlocal changed
        body=raw_read(fd,count)
        if not changed:
            changed=True;replacement=tmp_path/'new';replacement.write_bytes(b'bad');replacement.replace(path)
        return body
    monkeypatch.setattr(module.os,'read',replace_during_read)
    assert check() is False


def test_early_file_changed_while_later_file_read_cannot_pass(tmp_path,monkeypatch):
    import dream.core.phase_plan as module
    first=tmp_path/'first';first.write_bytes(b'ok');(tmp_path/'second').write_bytes(b'ok')
    check=simple(tmp_path,[{'path':'first','text':'ok'},{'path':'second','text':'ok'}]);read=module._read;calls=[]
    def changing(root,parts,maximum):
        result=read(root,parts,maximum);calls.append(parts)
        if parts==('second',):first.write_bytes(b'bad')
        return result
    monkeypatch.setattr(module,'_read',changing)
    assert check() is False and len(calls)==2


def test_plan_replacement_during_read_is_refused(tmp_path,monkeypatch):
    import dream.core.phase_plan as module
    path=save(tmp_path);read=module.os.read;changed=False
    def changing(fd,count):
        nonlocal changed
        body=read(fd,count)
        if not changed:
            changed=True;other=tmp_path/'other';other.write_bytes(path.read_bytes());other.replace(path)
        return body
    monkeypatch.setattr(module.os,'read',changing)
    with pytest.raises(ValueError):module.load_phase_plan(path,workspace=tmp_path)


def test_explicit_close_is_idempotent_and_closes_checks(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    plan=load_phase_plan(save(tmp_path),workspace=tmp_path)
    anchor=plan.phases[0].check.anchor;fd=anchor.fd
    assert os.fstat(fd).st_ino==tmp_path.stat().st_ino
    plan.close();plan.close()
    with pytest.raises(OSError):os.fstat(fd)
    assert plan.phases[0].check() is False
    with pytest.raises(ValueError):plan.__enter__()


def test_context_manager_releases_workspace_descriptor(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    with load_phase_plan(save(tmp_path),workspace=tmp_path) as plan:
        fd=plan.phases[0].check.anchor.fd;os.fstat(fd)
    with pytest.raises(OSError):os.fstat(fd)


def test_failed_loads_do_not_leak_descriptors(tmp_path):
    import dream.core.phase_plan as module
    path=save(tmp_path,{'version':True})
    before=len(os.listdir('/proc/self/fd'))
    for _ in range(40):
        with pytest.raises(ValueError):module.load_phase_plan(path,workspace=tmp_path)
    assert len(os.listdir('/proc/self/fd'))==before
    for _ in range(40):
        with module.load_phase_plan(save(tmp_path),workspace=tmp_path):pass
    assert len(os.listdir('/proc/self/fd'))==before


def test_directory_replacement_during_read_is_refused(tmp_path,monkeypatch):
    import dream.core.phase_plan as module
    folder=tmp_path/'out';folder.mkdir();(folder/'result').write_bytes(b'ok')
    check=simple(tmp_path,[{'path':'out/result','text':'ok'}]);read=module._read
    def changing(root,parts,maximum):
        result=read(root,parts,maximum)
        folder.rename(tmp_path/'old-out');folder.mkdir();(folder/'result').write_bytes(b'ok')
        return result
    monkeypatch.setattr(module,'_read',changing)
    assert check() is False


def test_checker_source_version_and_exact_plan_bytes_pin_check_digest(tmp_path,monkeypatch):
    import dream.core.phase_plan as module
    path=save(tmp_path)
    with module.load_phase_plan(path,workspace=tmp_path) as first:
        old=first.phases[0].check_version
        path.write_bytes(path.read_bytes()+b'\n')
        with module.load_phase_plan(path,workspace=tmp_path) as reformatted:
            assert reformatted.phases[0].check_version!=old
        save(tmp_path);reader=module._read_plan
        def changed_source(selected,**kwargs):
            data=reader(selected,**kwargs)
            return data+b'\n# hypothetical next checker implementation\n' if selected==Path(module.__file__) else data
        monkeypatch.setattr(module,'_read_plan',changed_source)
        with module.load_phase_plan(path,workspace=tmp_path) as new_source:
            assert new_source.phases[0].check_version!=old


@pytest.mark.parametrize('count',[0,33])
def test_phase_count_bounds(tmp_path,count):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw['phases']=raw['phases']*count
    with pytest.raises(ValueError):load_phase_plan(save(tmp_path,raw),workspace=tmp_path)


def test_predicate_count_and_duplicate_phase_bounds(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw['phases'][0]['acceptance']['all']=[{'path':f'file-{i}','min_bytes':0} for i in range(17)]
    with pytest.raises(ValueError):load_phase_plan(save(tmp_path,raw),workspace=tmp_path)
    raw=document();raw['phases']*=2
    with pytest.raises(ValueError):load_phase_plan(save(tmp_path,raw),workspace=tmp_path)


async def test_loader_specs_bind_actual_runner_host_checks_and_changed_plan_refuses_resume(tmp_path,worker):
    from dream.core.phase_plan import load_phase_plan
    from dream.core.phase_runner import PhaseRunner
    from dream.core.run_state import inspect_run
    from test_phase_runner import configured
    raw=document();raw['phases'][0]['acceptance']={'all':[{'path':'artifact.txt','text':raw['phases'][0]['goal']}]}
    from dream.memory.project import project_key
    worker.project=project_key(tmp_path)
    worker.tasks._store.project=worker.project
    path=save(tmp_path,raw);factory=configured(worker,tmp_path)
    with load_phase_plan(path,workspace=tmp_path) as plan:
        runner=PhaseRunner(worker,plan.phases,goal=plan.goal,project=plan.project,state_dir=tmp_path/'schedules',
            limits=plan.limits,evaluator_backend_factory=factory)
        result=await runner.run()
        assert result.status=='done',result.message
        assert len(worker.invocations)==1
        row=runner.inspect(result.schedule_id)['phases'][0]
        saved=inspect_run(tmp_path/'schedules/children',row['run_id'])['state']
        assert saved['managed']['project']==worker.project==plan.project
        assert worker.tasks.get(saved['managed']['task_id'])['project']==worker.project
        assert saved['phase_check']['check_version']==plan.phases[0].check_version
        assert saved['phase_check']['status']=='passed' and saved['managed']['completed_receipt']
    raw['phases'][0]['acceptance']['all'][0]['text']='changed definition'
    with load_phase_plan(save(tmp_path,raw),workspace=tmp_path) as changed:
        runner=PhaseRunner(worker,changed.phases,goal=changed.goal,project=changed.project,state_dir=tmp_path/'schedules',
            limits=changed.limits,evaluator_backend_factory=factory)
        assert (await runner.run(resume_schedule_id=result.schedule_id)).status=='error'
        assert len(worker.invocations)==1


from test_managed_loop import worker


@pytest.mark.parametrize('project',['','human project label','foreign-scope'])
def test_explicit_project_mismatch_is_refused_at_load(tmp_path,project):
    from dream.core.phase_plan import load_phase_plan
    raw=document();raw['project']=project
    with pytest.raises(ValueError,match='project'):
        load_phase_plan(save(tmp_path,raw),workspace=tmp_path)


def test_explicit_canonical_project_is_accepted(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    from dream.memory.project import project_key
    raw=document();raw['project']=project_key(tmp_path)
    with load_phase_plan(save(tmp_path,raw),workspace=tmp_path) as plan:
        assert plan.project==project_key(tmp_path)


def create_mode(path,body,mode):
    previous=os.umask(0)
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    finally:os.umask(previous)
    with os.fdopen(fd,'wb') as out:out.write(body)


@pytest.mark.parametrize('mode',[0o664,0o666])
def test_operator_plan_group_or_world_writable_file_is_not_authority(tmp_path,monkeypatch,mode):
    from dream.core.phase_plan import load_phase_plan
    path=tmp_path/'unsafe.json';create_mode(path,json.dumps(document()).encode(),mode)
    assert path.stat().st_mode&0o022
    if mode==0o664:
        from dream.core import tool_artifacts
        from types import SimpleNamespace
        monkeypatch.setattr(tool_artifacts.grp,'getgrgid',lambda gid:SimpleNamespace(gr_mem=['foreign-member']))
    before=len(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError,match='authority'):load_phase_plan(path,workspace=tmp_path)
    assert len(os.listdir('/proc/self/fd'))==before
    assert path.stat().st_mode&0o777==mode


@pytest.mark.parametrize('mode',[0o775,0o777])
def test_operator_plan_nonsticky_shared_ancestor_is_not_authority(tmp_path,monkeypatch,mode):
    from dream.core.phase_plan import load_phase_plan
    folder=tmp_path/'shared';previous=os.umask(0)
    try:folder.mkdir(mode=mode)
    finally:os.umask(previous)
    path=save(folder)
    if mode==0o775:
        from dream.core import tool_artifacts
        from types import SimpleNamespace
        monkeypatch.setattr(tool_artifacts.grp,'getgrgid',lambda gid:SimpleNamespace(gr_mem=['foreign-member']))
    before=len(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError,match='authority'):load_phase_plan(path,workspace=tmp_path)
    assert len(os.listdir('/proc/self/fd'))==before
    assert folder.stat().st_mode&0o777==mode


def test_sticky_trusted_ancestor_and_public_readable_plan_are_supported(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    sticky=tmp_path/'sticky';previous=os.umask(0)
    try:sticky.mkdir(mode=0o1777)
    finally:os.umask(previous)
    path=save(sticky)
    assert path.stat().st_mode&0o777==0o644
    with load_phase_plan(path,workspace=tmp_path) as plan:assert plan.goal==document()['goal']
    assert sticky.stat().st_mode&0o7777==0o1777


@pytest.mark.parametrize('target',['plan','ancestor','sticky_child'])
def test_foreign_owner_on_actual_descriptor_is_refused_and_closed(tmp_path,monkeypatch,target):
    from types import SimpleNamespace
    import dream.core.phase_plan as module
    parent=tmp_path/'parent';parent.mkdir(mode=0o700)
    if target=='sticky_child':
        sticky=tmp_path/'sticky';previous=os.umask(0)
        try:sticky.mkdir(mode=0o1777)
        finally:os.umask(previous)
        parent.rename(sticky/'child');parent=sticky/'child'
    path=save(parent);selected=path if target=='plan' else parent
    selected_id=(selected.stat().st_dev,selected.stat().st_ino);fstat=module.os.fstat;checked=[]
    def foreign(fd):
        info=fstat(fd)
        if (info.st_dev,info.st_ino)==selected_id:
            checked.append(fd)
            fields={key:getattr(info,key) for key in dir(info) if key.startswith('st_')}
            fields['st_uid']=65534 if os.geteuid()!=65534 else 65533
            return SimpleNamespace(**fields)
        return info
    before=len(os.listdir('/proc/self/fd'));monkeypatch.setattr(module.os,'fstat',foreign)
    with pytest.raises(ValueError,match='authority owner'):module.load_phase_plan(path,workspace=tmp_path)
    assert checked and len(os.listdir('/proc/self/fd'))==before


def _writer_acl(*,directory=False):
    import struct
    owner=7 if directory else 6;foreign=65534 if os.geteuid()!=65534 else 65533
    return struct.pack('<I',2)+b''.join(struct.pack('<HHI',tag,permissions,identity) for tag,permissions,identity in (
        (1,owner,0xffffffff),(2,owner,foreign),(4,4,0xffffffff),(16,owner,0xffffffff),(32,4,0xffffffff)))


@pytest.mark.parametrize('target',['plan','ancestor','sticky_ancestor'])
def test_real_extended_acl_writer_is_refused_on_operator_authority(tmp_path,target):
    import dream.core.phase_plan as module
    folder=tmp_path/'folder';previous=os.umask(0)
    try:folder.mkdir(mode=0o1777 if target=='sticky_ancestor' else 0o700)
    finally:os.umask(previous)
    path=save(folder);selected=path if target=='plan' else folder
    # New generic fixture only; no existing source/owner permissions are changed.
    os.setxattr(selected,'system.posix_acl_access',_writer_acl(directory=target!='plan'))
    assert os.getxattr(selected,'system.posix_acl_access')
    before=len(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError,match='authority ACL'):module.load_phase_plan(path,workspace=tmp_path)
    assert len(os.listdir('/proc/self/fd'))==before


@pytest.mark.parametrize('mode',['unreadable','malformed'])
def test_unreadable_or_malformed_acl_on_actual_descriptor_fails_closed(tmp_path,monkeypatch,mode):
    import errno
    import dream.core.phase_plan as module
    path=save(tmp_path);selected=(path.stat().st_dev,path.stat().st_ino);getxattr=module.os.getxattr;observed=[]
    def unknown(fd,name):
        assert isinstance(fd,int)
        info=os.fstat(fd)
        if (info.st_dev,info.st_ino)==selected:
            observed.append(fd)
            if mode=='malformed':return b'broken'
            raise OSError(errno.EACCES,'fixture cannot inspect access ACL')
        return getxattr(fd,name)
    before=len(os.listdir('/proc/self/fd'));monkeypatch.setattr(module.os,'getxattr',unknown)
    with pytest.raises(ValueError,match='authority ACL'):module.load_phase_plan(path,workspace=tmp_path)
    assert observed and len(os.listdir('/proc/self/fd'))==before


def test_result_permissions_do_not_inherit_operator_plan_policy(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    folder=tmp_path/'outputs';previous=os.umask(0)
    try:folder.mkdir(mode=0o777)
    finally:os.umask(previous)
    create_mode(folder/'result',b'generic result',0o666)
    raw=document();raw['phases'][0]['acceptance']={'all':[{'path':'outputs/result','text':'generic result'}]}
    with load_phase_plan(save(tmp_path,raw),workspace=tmp_path) as plan:
        assert plan.phases[0].check() is True
    assert folder.stat().st_mode&0o777==0o777
    assert (folder/'result').stat().st_mode&0o777==0o666


def test_actual_user_private_group_plan_and_parent_are_supported(tmp_path):
    from dream.core.phase_plan import load_phase_plan
    from dream.core.tool_artifacts import _unsafe_writers
    folder=tmp_path/'private-group';previous=os.umask(0)
    try:folder.mkdir(mode=0o775)
    finally:os.umask(previous)
    path=folder/'plan.json';create_mode(path,json.dumps(document()).encode(),0o664)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:assert _unsafe_writers(os.fstat(fd),fd) is False
    finally:os.close(fd)
    with load_phase_plan(path,workspace=tmp_path) as plan:assert plan.goal==document()['goal']
    assert folder.stat().st_mode&0o777==0o775 and path.stat().st_mode&0o777==0o664


@pytest.mark.parametrize('kind',['unknown','foreign_member','other_primary_member'])
def test_unknown_or_shared_group_is_not_operator_plan_authority(tmp_path,monkeypatch,kind):
    from types import SimpleNamespace
    from dream.core.phase_plan import load_phase_plan
    from dream.core import tool_artifacts
    path=tmp_path/'group-plan.json';create_mode(path,json.dumps(document()).encode(),0o664)
    if kind=='unknown':
        def unknown(gid):raise KeyError('fixture unresolved group')
        monkeypatch.setattr(tool_artifacts.grp,'getgrgid',unknown)
    elif kind=='foreign_member':
        monkeypatch.setattr(tool_artifacts.grp,'getgrgid',lambda gid:SimpleNamespace(gr_mem=['foreign-member']))
    else:
        gid=path.stat().st_gid
        monkeypatch.setattr(tool_artifacts.pwd,'getpwall',lambda:[SimpleNamespace(pw_gid=gid,pw_uid=65534)])
    before=len(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError,match='authority'):load_phase_plan(path,workspace=tmp_path)
    assert len(os.listdir('/proc/self/fd'))==before


def test_acl_becoming_unreadable_during_private_group_check_is_clean_refusal(tmp_path,monkeypatch):
    import errno
    import dream.core.phase_plan as module
    path=save(tmp_path);selected=(path.stat().st_dev,path.stat().st_ino);getxattr=module.os.getxattr;calls=[]
    def unavailable(fd,name):
        info=os.fstat(fd)
        if (info.st_dev,info.st_ino)==selected:
            calls.append(fd)
            if len(calls)>1:raise OSError(errno.EACCES,'fixture ACL availability changed')
        return getxattr(fd,name)
    before=len(os.listdir('/proc/self/fd'));monkeypatch.setattr(module.os,'getxattr',unavailable)
    with pytest.raises(ValueError,match='authority ACL'):module.load_phase_plan(path,workspace=tmp_path)
    assert len(calls)==2 and len(os.listdir('/proc/self/fd'))==before
