"""Trusted final-check hooks must commit synchronously or seal their lease."""
import pytest

from dream.core.help_continuation import HelpContinuation
from test_help_continuation import assisted, budget
from test_task_checkpoints import managed


def prepared(assisted, checkpoint):
    c,key,parent,_=assisted
    service=HelpContinuation(c.journal,authority=key,check_checkpoint=checkpoint)
    service.authorize(budget(parent,c),authority=key)
    worker=service.begin_stage('worker',budget(parent,c,'worker'),authority=key)
    service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    review=service.begin_stage('review',budget(parent,c,'independent_review'),authority=key)
    service.finish_stage(review,budget(parent,c),authority=key,passed=True)
    check=service.begin_stage('check',budget(parent,c),authority=key)
    return c,key,parent,service,check


@pytest.mark.parametrize('mode',['noop','awaitable','invalid_ack','exception'])
async def test_noncommitting_host_hook_seals_after_one_actual_check(assisted,mode):
    async def unexecuted():raise AssertionError('The checkpoint coroutine must not execute')
    def checkpoint(snapshot,**kwargs):
        if mode=='awaitable':return unexecuted()
        if mode=='invalid_ack':return True
        if mode=='exception':raise OSError('Synthetic pre-commit error')
        return None
    c,key,parent,service,stage=prepared(assisted,checkpoint)
    calls=[]
    def actual_check():calls.append(1);return True
    with pytest.raises((ValueError,OSError)):
        service.execute_check(stage,actual_check,budget(parent,c),authority=key,
            check_name='acceptance',check_version='b'*64)
    assert calls==[1]
    assert service.inspect()['status']=='assisted'
    with pytest.raises(PermissionError):
        service.execute_check(stage,actual_check,budget(parent,c),authority=key,
            check_name='acceptance',check_version='b'*64)
    assert calls==[1] and c.task['status']!='done'


async def test_host_hook_commits_exact_final_snapshot_once(assisted):
    c,key,parent,_=assisted
    invoked=[]
    def checkpoint(snapshot,**kwargs):
        invoked.append(kwargs)
        c.journal.record('fixture_actual_check_checkpoint',incremental=True,help_continuation=snapshot)
    c,key,parent,service,stage=prepared(assisted,checkpoint)
    before=c.journal.seq
    saved=service.execute_check(stage,lambda:True,budget(parent,c),authority=key,
        check_name='acceptance',check_version='b'*64)
    assert c.journal.seq==before+1 and c.journal.state['help_continuation']==saved
    assert invoked==[{'check_name':'acceptance','check_version':'b'*64,'passed':True}]
    assert saved['status']=='ready_for_verification' and c.task['status']!='done'


@pytest.mark.parametrize('invalid',[True,[], 'not a callable'])
async def test_checkpoint_configuration_is_host_callable_only(assisted,invalid):
    c,key,_,_=assisted
    with pytest.raises(ValueError):HelpContinuation(c.journal,authority=key,check_checkpoint=invalid)


async def test_async_checkpoint_definition_is_rejected_before_a_check(assisted):
    c,key,_,_=assisted
    async def callback(*args,**kwargs):raise AssertionError('Never called')
    with pytest.raises(ValueError):HelpContinuation(c.journal,authority=key,check_checkpoint=callback)
