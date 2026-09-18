"""Reported activity display only, using isolated Studio and GPU-disabled Chromium."""
from playwright.async_api import expect
from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401


async def test_detailed_default_preserves_reasoning_choice_and_inspectable_tools(chat, tmp_path):
    server, page, prompts, _ = chat
    await expect(page.get_by_label('Feed detail')).to_have_value('detailed')
    server.bus.publish(Event('thinking_delta', 'I will inspect the supplied file.'))
    thinking = page.locator('#stream > .think')
    await expect(thinking).to_have_attribute('open', '')
    await expect(thinking.locator('div')).to_be_visible()
    await thinking.locator('summary').click()
    server.bus.publish(Event('thinking_delta', ' Then check the source references.'))
    await expect(thinking).not_to_have_attribute('open', '')
    await expect(thinking).to_contain_text('source references')
    server.bus.publish(Event('tool_use', {'id':'actual-call', 'name':'mcp__files__read_file', 'input':{'path':'notes.md','query':'source references'}}))
    tool = page.locator('#stream > .tool')
    await expect(tool.locator('.tname')).to_have_text('mcp__files__read_file')
    await expect(tool.get_by_role('button', name='Inspect arguments')).to_contain_text('request characters')
    await tool.get_by_role('button', name='Inspect arguments').click()
    await expect(tool.locator('.feed-full').first).to_be_visible()
    await expect(tool.locator('.feed-full').first).to_contain_text('source references')
    server.bus.publish(Event('tool_result', {'id':'actual-call', 'content':'Actual result <script>window.injected=true</script>', 'is_error':False}))
    await expect(tool.locator('.feed-preview')).to_be_visible()
    await expect(tool.locator('.feed-preview')).to_contain_text('Actual result')
    await expect(tool.locator('.badge')).to_have_text('Completed')
    assert await page.evaluate('window.injected') is None
    assert prompts == []
    await page.set_viewport_size({'width':480,'height':850})
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.screenshot(path=str(tmp_path / 'detailed-feed-mobile.png'))


async def test_compact_setting_persists_and_replay_is_display_only(chat):
    server, page, prompts, _ = chat
    server.bus.publish(Event('thinking_delta', 'Reported reasoning'))
    server.bus.publish(Event('tool_use', {'id':'read', 'name':'read_file', 'input':{'path':'sample.md'}}))
    server.bus.publish(Event('tool_result', {'id':'read', 'content':'Retained tool result'}))
    server.bus.publish(Event('result', {}))
    await page.get_by_label('Feed detail').select_option('compact')
    await expect(page.locator('#stream > .tool')).not_to_have_attribute('open', '')
    await page.reload()
    await expect(page.get_by_label('Feed detail')).to_have_value('compact')
    await expect(page.locator('#stream > .tool')).to_have_count(1)
    await expect(page.locator('#stream > .think')).to_have_count(1)
    await expect(page.locator('.feed-preview')).not_to_be_visible()
    await page.get_by_label('Feed detail').select_option('detailed')
    await expect(page.locator('.feed-preview')).to_be_visible()
    assert prompts == []


async def test_display_bounds_large_reasoning_args_and_results(chat):
    server, page, _, _ = chat
    server.bus.publish(Event('thinking_delta', 'r' * 80000))
    await expect(page.locator('.think')).to_contain_text('Display truncated')
    assert len(await page.locator('.think div').text_content()) < 66000
    server.bus.publish(Event('tool_use', {'id':'large', 'name':'read_file', 'input':{'query':'a'*25000}}))
    server.bus.publish(Event('tool_result', {'id':'large', 'content':'result '*6000}))
    tool = page.locator('#stream > .tool')
    await expect(tool).to_contain_text('display limited to 20,000')
    await expect(tool.locator('.feed-preview')).to_be_visible()
    assert len(await tool.locator('.feed-preview').text_content()) <= 800
    for full in await tool.locator('.feed-full').all():
        assert len(await full.text_content()) < 20200


async def test_worker_activity_stays_attributed_and_does_not_change_lead_counts(chat):
    server, page, prompts, _ = chat
    events = [
        {'kind':'tool_use','data':{'id':'lead','name':'delegate','input':{}}},
        {'kind':'agent_activity','data':{'run_id':'worker-a','agent':'Verifier','kind':'status','status':'queued'}},
        {'kind':'agent_activity','data':{'run_id':'worker-b','agent':'Researcher','kind':'status','status':'running'}},
        {'kind':'agent_activity','data':{'run_id':'worker-a','agent':'Verifier','kind':'thinking_delta','text':'Checking source A.'}},
        {'kind':'agent_activity','data':{'run_id':'worker-b','agent':'Researcher','kind':'tool_use','data':{'id':'shared-tool-id','name':'read_file','input':{'path':'B.md'}}}},
        {'kind':'agent_activity','data':{'run_id':'worker-a','agent':'Verifier','kind':'tool_use','data':{'id':'shared-tool-id','name':'read_file','input':{'path':'A.md'}}}},
        {'kind':'agent_activity','data':{'run_id':'worker-b','agent':'Researcher','kind':'tool_result','data':{'id':'shared-tool-id','content':'Source B result'}}},
        {'kind':'agent_activity','data':{'run_id':'worker-a','agent':'Verifier','kind':'tool_result','data':{'id':'shared-tool-id','content':'Source A result','is_error':True}}},
        {'kind':'agent_activity','data':{'run_id':'worker-a','agent':'Verifier','kind':'status','status':'failed'}},
        {'kind':'agent_activity','data':{'run_id':'worker-b','agent':'Researcher','kind':'status','status':'complete'}},
        {'kind':'tool_result','data':{'id':'lead','content':'Worker summaries returned'}},
        {'kind':'result','data':{}},
    ]
    for event in events:
        server.bus.publish(Event(event['kind'], event['data']))
    a, b = page.locator('[data-run-id="worker-a"]'), page.locator('[data-run-id="worker-b"]')
    await expect(a.locator('.agent-state')).to_have_text('Failed')
    await expect(b.locator('.agent-state')).to_have_text('Completed')
    await expect(a).to_contain_text('Source A result')
    await expect(a).not_to_contain_text('Source B result')
    await expect(b).to_contain_text('Source B result')
    await expect(page.locator('#counts')).to_contain_text('1 tool calls')
    # Render the same bounded history twice; worker cards must be replaced, not duplicated.
    await page.evaluate('events => handle({kind:"history",data:{events}})', events)
    await page.evaluate('events => handle({kind:"history",data:{events}})', events)
    await expect(page.locator('.agent-card')).to_have_count(2)
    await expect(page.locator('#counts')).to_contain_text('1 tool calls')
    assert prompts == []


async def test_failed_tool_opens_with_readable_error_in_compact(chat):
    server, page, _, _ = chat
    await page.get_by_label('Feed detail').select_option('compact')
    server.bus.publish(Event('tool_use', {'id':'failure', 'name':'read_file', 'input':{'path':'missing.md'}}))
    server.bus.publish(Event('tool_result', {'id':'failure', 'content':'File not found. Check the path.', 'is_error':True}))
    await expect(page.locator('#stream > .tool')).to_have_attribute('open', '')
    await expect(page.locator('.feed-preview')).to_be_visible()
    await expect(page.locator('.feed-preview')).to_contain_text('File not found')


async def test_worker_report_labels_nonstream_reasoning_and_real_reconnect(chat):
    server, page, prompts, _ = chat
    report = {'run_id':'verifier-run', 'agent':'Verifier', 'phase':'subagent', 'round':1}
    server.bus.publish(Event('agent_activity', {**report, 'kind':'status','status':'running'}))
    server.bus.publish(Event('agent_activity', {**report, 'kind':'request','request_index':1}))
    card = page.locator('[data-run-id="verifier-run"]')
    await expect(card.locator('.agent-state')).to_have_text('Request started · awaiting response')
    server.bus.publish(Event('agent_activity', {**report, 'kind':'response','request_index':1}))
    server.bus.publish(Event('agent_activity', {**report, 'kind':'thinking_report','text':'The received source supports this finding.','reported_after_response':True}))
    await expect(card.locator('.think summary')).to_have_text('Thinking · reported after response')
    await expect(card.locator('.think div')).to_be_visible()
    server.bus.publish(Event('agent_activity', {**report, 'kind':'status','status':'completed'}))
    await page.reload()
    await expect(page.locator('.agent-card')).to_have_count(1)
    await expect(card.locator('.agent-state')).to_have_text('Completed')
    await expect(card).to_contain_text('received source supports')
    assert not prompts


async def test_worker_display_bound_keeps_new_activity_visible(chat):
    _, page, _, _ = chat
    await page.evaluate('''() => {
      for(let n=0;n<34;n++) {
        handle({kind:'agent_activity',data:{run_id:'bounded-'+n,agent:'Worker '+n,kind:'status',status:'running'}});
        handle({kind:'agent_activity',data:{run_id:'bounded-'+n,agent:'Worker '+n,kind:'status',status:'completed'}});
      }
      for(let n=0;n<101;n++) handle({kind:'agent_activity',data:{run_id:'bounded-33',agent:'Worker 33',kind:'request',request_index:n}});
    }''')
    await expect(page.locator('.agent-card')).to_have_count(32)
    await expect(page.locator('[data-run-id="bounded-0"]')).to_have_count(0)
    await expect(page.locator('[data-run-id="bounded-33"]')).to_be_visible()
    await expect(page.locator('.agent-feed-limit')).to_contain_text('32 workers')
    await expect(page.locator('.agent-item-limit')).to_contain_text('100 items')


async def test_worker_requested_and_interrupted_tool_status_are_literal(chat):
    server, page, _, _ = chat
    worker = {'run_id':'interrupted-worker', 'agent':'Verifier', 'phase':'subagent'}
    server.bus.publish(Event('agent_activity', {**worker,'kind':'tool_use','status':'requested',
        'data':{'id':'child-call','name':'run_bash','input':{'command':'fixture-command'}}}))
    badge = page.locator('[data-run-id="interrupted-worker"] .tool .badge')
    await expect(badge).to_have_text('Requested')
    server.bus.publish(Event('agent_activity', {**worker,'kind':'tool_result','status':'interrupted',
        'data':{'id':'child-call','content':'Tool observation interrupted; outcome is unknown.','is_error':True}}))
    await expect(badge).to_have_text('Interrupted')
    await expect(page.locator('[data-run-id="interrupted-worker"] .feed-preview')).to_contain_text('outcome is unknown')


async def test_worker_failure_explanation_visible_in_compact_and_retained(chat):
    server, page, _, _ = chat
    await page.get_by_label('Feed detail').select_option('compact')
    worker = {'run_id':'timeout-worker','agent':'Verifier','phase':'subagent'}
    server.bus.publish(Event('agent_activity', {**worker,'kind':'status','status':'running','text':'Subagent started.'}))
    card = page.locator('[data-run-id="timeout-worker"]')
    await expect(card).not_to_have_attribute('open', '')
    server.bus.publish(Event('agent_activity', {**worker,'kind':'status','status':'failed','text':'Subagent timed out; work is incomplete.'}))
    await expect(card).to_have_attribute('open', '')
    await expect(card.locator('.agent-status-detail')).to_have_text('Subagent timed out; work is incomplete.')
    await page.reload()
    await expect(card.locator('.agent-status-detail')).to_be_visible()
    await expect(card.locator('.agent-status-detail')).to_have_count(1)
    await expect(card).not_to_contain_text('Subagent started.')
    await expect(card).to_contain_text('Subagent timed out')


async def test_compact_failed_child_tool_opens_enclosing_worker(chat):
    server, page, _, _ = chat
    await page.get_by_label('Feed detail').select_option('compact')
    worker = {'run_id':'tool-error-worker','agent':'Researcher','phase':'subagent'}
    server.bus.publish(Event('agent_activity', {**worker,'kind':'tool_use','status':'requested','data':{'id':'denied','name':'read_file','input':{'path':'sample.md'}}}))
    card = page.locator('[data-run-id="tool-error-worker"]')
    await expect(card).not_to_have_attribute('open', '')
    server.bus.publish(Event('agent_activity', {**worker,'kind':'tool_result','status':'failed','data':{'id':'denied','content':'Permission was denied.','is_error':True}}))
    await expect(card).to_have_attribute('open', '')
    await expect(card.locator('.feed-preview')).to_be_visible()
    await expect(card.locator('.feed-preview')).to_have_text('Permission was denied.')
