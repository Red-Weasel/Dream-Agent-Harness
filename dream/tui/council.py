"""Explicit Council controls, executed on the App's provider lifecycle task."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace

from rich.text import Text

from ..core import turn_origin
from ..core.backends.base import Event


@dataclass
class CouncilRequest:
    payload: dict
    future: asyncio.Future


class CouncilControls:
    def _publish_council_activity(self, task_id, member, state, detail=''):
        model = self.engine.model if self.engine.provider.key == member else (self.engine._moe.advisor_models.get(member) if self.engine._moe else None)
        row = {'task_id': task_id, 'member': member, 'role': 'active worker',
               'state': state, 'detail': str(detail)[:1000],
               'workspace': str(self.workspace), 'model': model,
               'observed_at': datetime.now(timezone.utc).isoformat(),
               'source': 'Dream Council lifecycle', 'ownership': 'sequential workspace writer'}
        rows = getattr(self, '_council_activity', [])
        self._council_activity = [r for r in rows if r['task_id'] != task_id][-31:] + [row]
        self.bus.publish(Event('council_activity', dict(row)))

    def _council_busy(self):
        return (not getattr(self, '_accepting_input', False)
                or getattr(self, '_council_pending', None) is not None
                or not self._gui_prompts.empty()
                or getattr(self, '_deferred_gui_prompt', None) is not None
                or getattr(self, '_active_loop', None) is not None)

    def _current_council_config(self):
        from ..core.moe import MoeConfig
        cfg = self.engine._moe or MoeConfig(self.engine.provider.key, [])
        active_effort = getattr(self.engine, 'effort', cfg.orchestrator_effort)
        if active_effort:
            if self.engine.provider.key in {'openai', 'xai'}:
                from ..core import effort
                if effort.normalize(active_effort) is not None:
                    active_effort = effort.for_openai(active_effort)['reasoning_effort']
            elif self.engine.provider.key == 'anthropic':
                active_effort = {'med': 'medium', 'ultra': 'max'}.get(active_effort, active_effort)
            elif active_effort == 'med':
                active_effort = 'medium'
        return replace(cfg, orchestrator_effort=active_effort)

    def _council_status(self):
        from ..core.council_config import provider_choices
        cfg = self._current_council_config()
        backend = getattr(self.engine, 'backend', None)
        capability_status = getattr(backend, 'capability_status', None)
        capabilities = capability_status() if self.engine.provider.key == 'machx' and callable(capability_status) else None
        available = getattr(self.engine, '_backend_available', True)
        return {'config': asdict(cfg), 'model': self.engine.model,
                'choices': provider_choices(model=self.engine.model, capabilities=capabilities),
                'busy': self._council_busy(), 'main_available': available,
                'activity': list(getattr(self, '_council_activity', [])),
                'warning': getattr(self, '_council_work_warning', '') if available else 'The main agent is disconnected after an incomplete handoff. Start a new Dream application to recover.'}

    async def _queue_council_control(self, payload):
        if self._council_busy():
            raise ValueError('Wait for the current turn, queued input or Council action to finish.')
        future = asyncio.get_running_loop().create_future()
        # A disconnected HTTP client must not cancel a provider handoff midway.
        # Its outcome remains in the transcript and refreshed Council status.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        request = CouncilRequest(dict(payload), future)
        self._council_pending = request
        self._gui_prompts.put_nowait(request)
        return await asyncio.shield(future)

    async def _execute_council_control(self, request):
        self.bus.publish(Event('turn_start', {}))
        result = None
        failure = None
        try:
            payload = request.payload
            if payload['action'] == 'council_configure':
                from ..core.council_config import parse_config
                cfg = parse_config(payload.get('config'))
                await self.engine.configure_council(cfg, model=payload.get('model'))
                self._council_work_warning = ''
                self.moe = self.engine._moe
                self.provider = self.engine.provider.key
                self.model = self.engine.model
                self.provider_label = self.engine.provider_label
                self.provider_kind = self.engine.provider.key
                text = f'Council updated. Main: {self.provider_label}; members: {", ".join(cfg.advisors) or "none"}.'
                self.renderer.system(text)
                self.bus.publish(Event('system', text))
            elif payload['action'] == 'council_work':
                # Provider connect/disconnect must remain on this lifecycle task;
                # _ask owns cancellation of each member's generation task.
                result = await self._work_council(payload.get('question'), payload.get('advisor'))
            elif payload['action'] == 'council_ask':
                result = await self._run_turn(self._consult_council(
                    payload.get('question'), payload.get('advisor'), payload.get('sources')))
                if self.interrupted:
                    raise ValueError('Council consultation interrupted. No prompt was replayed.')
            else:
                raise ValueError('Unknown Council action')
        except asyncio.CancelledError:
            failure = ValueError('Dream closed before the Council action completed. Check its state before retrying.')
            raise
        except Exception as exc:
            failure = ValueError(str(exc))
            self.bus.publish(Event('error', str(exc)))
        finally:
            self._council_pending = None
            self.bus.publish(Event('hello', self._studio_session_info()))
            self.bus.publish(Event('turn_end', {}))
            if not request.future.done():
                if failure:
                    request.future.set_exception(failure)
                else:
                    if result is None:
                        result = self._council_status()
                        result['busy'] = False
                    request.future.set_result(result)

    def _sync_council_main(self):
        self.moe = self.engine._moe
        self.provider = self.engine.provider.key
        self.model = self.engine.model
        self.provider_label = self.engine.provider_label
        self.provider_kind = self.engine.provider.key

    async def _work_council(self, question, advisor=None):
        """Give members full editing turns, then hand back to the original main.

        Shared workspace writers take turns. Each uses the ordinary turn and
        permission path; private read-only consultations remain independent.
        Do not move provider handoffs into a child task (SDK cancel scopes).
        """
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError('Enter a Council task of 1–8000 characters.')
        original = self.engine._moe
        if original is None or not original.advisors:
            raise ValueError('Choose at least one Council member first.')
        if advisor is not None and (not isinstance(advisor, str) or advisor not in original.advisors):
            raise ValueError('Choose a configured Council member.')
        members = [advisor] if advisor else list(original.advisors)
        original_model = self.engine.model
        original = self._current_council_config()
        completed = []
        switched = False
        task_id = None
        member = None
        try:
            for member in members:
                task_id = uuid.uuid4().hex
                self._publish_council_activity(task_id, member, 'queued')
                cfg = replace(original, orchestrator=member,
                              orchestrator_effort=original.advisor_efforts.get(member))
                await self.engine.configure_council(cfg, model=original.advisor_models.get(member, ''))
                switched = True
                self._sync_council_main()
                label = f'Council work · {self.provider_label} · {self.model or "provider default"}'
                self.renderer.system(label)
                self.bus.publish(Event('system', label))
                session_info = getattr(self, '_studio_session_info', None)
                if callable(session_info):
                    self.bus.publish(Event('hello', session_info()))
                prompt = (f'{label}\nYou are taking an active work turn in the current workspace. '
                          'Use your tools to implement the requested improvements and verify the result. '
                          'Inspect existing files and prior Council work before editing; preserve unrelated changes. '
                          'Report actual changes, checks and any unfinished work for the main agent.\n\n'
                          f'User task:\n{question.strip()}')
                self._publish_council_activity(task_id, member, 'running')
                # Dream's wrapper around the owner's task: marked, and the Fresh start handoff lists the task
                # in the owner's own words (turn_origin.owner_words).
                with turn_origin.generated(turn_origin.COUNCIL):
                    success = await self._ask(prompt)
                if self.interrupted:
                    raise ValueError('Council work interrupted. Remaining members were not started; inspect partial work before retrying.')
                if success is not True:
                    raise ValueError('Council member did not complete successfully. Remaining members were not started; inspect partial work before retrying.')
                completed.append(member)
                self._publish_council_activity(task_id, member, 'completed')
                task_id = None
        except BaseException as exc:
            if task_id:
                state = 'interrupted' if isinstance(exc, asyncio.CancelledError) or self.interrupted else 'failed'
                self._publish_council_activity(task_id, member, state, str(exc))
            raise
        finally:
            if switched:
                try:
                    await self.engine.configure_council(original, model=original_model or '')
                except BaseException as exc:
                    self._sync_council_main()
                    self._council_work_warning = 'The original main could not be restored. Check the current selection and existing work before continuing.'
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise RuntimeError('Council work ended but could not restore the original main. '
                                       'Check Council status and existing files before continuing; no task was replayed.') from exc
            self._sync_council_main()
        self._council_work_warning = ''
        text = 'Council work turns finished. Control returned to the main agent; review the transcript and changed files.'
        self.bus.publish(Event('system', text))
        return {'completed': completed, 'main_restored': True}

    async def _consult_council(self, question, advisor=None, sources=None):
        from ..core import moe
        from ..tools.context import bind_context
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError('Enter a Council question of 1–8000 characters.')
        cfg = self.engine._moe
        if cfg is None or not cfg.advisors:
            raise ValueError('Choose at least one advisor in Council first.')
        if advisor is not None and (not isinstance(advisor, str) or (advisor and advisor not in cfg.advisors)):
            raise ValueError('Choose a configured advisor, or ask the whole Council.')
        advisors = [advisor] if advisor else cfg.advisors
        context = await asyncio.to_thread(self.engine.council_context)
        scope = getattr(self.engine, '_tool_context', None)
        if scope is not None:
            from .. import config
            from ..telemetry.runtime import RunMeter
            meter = RunMeter(self.engine.session_id, getattr(self.engine, '_turn_index', 0),
                             self.engine.profile, config.LOG_DIR / 'runtime' / f'{self.engine.session_id}.jsonl')
            meter.record('council_started', advisors=list(advisors))
            scope = replace(scope, runtime_meter=meter)
            self.engine.runtime_meter = meter
        self.bus.publish(Event('system', f'Consulting {", ".join(advisors)}: {question.strip()}'))
        options = {'cwd': str(self.workspace), 'max_concurrency': cfg.max_concurrency,
                   'timeout': cfg.timeout_seconds, 'models': cfg.advisor_models,
                   'legal': cfg.legal_review, 'sources': sources, 'mode': self.mode}
        efforts = getattr(cfg, 'advisor_efforts', None)
        if efforts:
            options['efforts'] = efforts
        with bind_context(scope) if scope is not None else nullcontext():
            results = await moe.council(advisors, question.strip(), context, **options)
        await self.engine.record_council_results(question.strip(), results)
        for row in results:
            label = str(row.get('label') or row['advisor'])
            if row.get('model'):
                label += ' · ' + str(row['model'])
            text = f'Council · {label}\n{row["answer"]}'
            self.renderer.console.print(Text(text))
            # A tool-result card preserves attribution and does not pretend the
            # main model authored or verified an advisor's answer.
            identifier = 'council-' + uuid.uuid4().hex
            self.bus.publish(Event('tool_use', {'id': identifier, 'name': 'council',
                             'input': {'advisor': row['advisor'], 'question': question.strip()}}))
            self.bus.publish(Event('tool_result', {'id': identifier, 'name': 'council', 'content': text}))
        return {'results': results}
