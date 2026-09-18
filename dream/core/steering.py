"""One ordinary HTTP chat turn's saved corrections; never an execution queue."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path


async def finish_owned(awaitable):
    """Cancellation waits for an owned storage operation, without detaching it."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class SteeringInbox:
    def __init__(self, path: Path, session: str, turn: int, log_turn, emit=None):
        self.path, self.session, self.turn = path, session, turn
        self.log_turn, self.emit = log_turn, emit
        self.receipts: dict[str, dict] = {}
        self.open = True
        self._lock = asyncio.Lock()

    def _save(self, receipts):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'session_id': self.session, 'turn': self.turn,
                   'recovery': 'Saved corrections only. Inspect before explicitly resubmitting; never automatically replay.',
                   'receipts': list(receipts.values())}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent,
                                             prefix='.steering-', delete=False) as fh:
                temporary = fh.name
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _notice(self, receipt):
        public = {k: v for k, v in receipt.items() if k != 'text'}
        public.update(session_id=self.session, turn=self.turn, recovery_path=str(self.path))
        if self.emit:
            self.emit(public)
        return public

    async def submit(self, text: str, identifier: str):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('Steering receipt ID is invalid.')
        if not isinstance(text, str) or not text.strip() or len(text) > 64000:
            raise ValueError('Steering requires 1–64000 characters.')
        return await finish_owned(self._submit(text, identifier))

    async def _submit(self, text, identifier):
        async with self._lock:
            previous = self.receipts.get(identifier)
            if previous:
                if previous['text'] != text:
                    raise ValueError('Steering receipt ID already belongs to different text.')
                return self._notice(previous)
            if not self.open:
                raise ValueError('Steering is closed for this turn. Your draft was not queued; send it when ready.')
            if len(self.receipts) >= 32:
                raise ValueError('This turn already has 32 steering receipts.')
            receipt = {'id': identifier, 'text': text, 'status': 'pending', 'user_turn_id': None}
            staged = {**self.receipts, identifier: receipt}
            # The receipt itself preserves the text even if session logging fails.
            await asyncio.to_thread(self._save, staged)
            self.receipts = staged
            def capture():
                try:
                    self.log_turn('user', text, on_commit=lambda turn_id: receipt.update(user_turn_id=turn_id))
                except Exception as exc:
                    receipt['warning'] = f'Session logging failed ({type(exc).__name__}); correction is saved in its receipt.'
                    if receipt['user_turn_id'] is None:
                        receipt['status'] = 'retained'
                try:
                    self._save(self.receipts)
                except OSError:
                    receipt['warning'] = 'Receipt update failed; its initial saved text remains available. Inspect before resubmitting.'
                    receipt['status'] = 'retained'
            await asyncio.to_thread(capture)
            return self._notice(receipt)

    async def drain(self, *, final=False):
        async with self._lock:
            pending = [dict(r) for r in self.receipts.values() if r['status'] == 'pending']
            if not pending:
                if final:
                    self.open = False
                return []
            staged = {k: dict(v) for k, v in self.receipts.items()}
            for receipt in pending:
                staged[receipt['id']]['status'] = 'included'
            await finish_owned(asyncio.to_thread(self._save, staged))
            self.receipts = staged
            for receipt in pending:
                self._notice(staged[receipt['id']])
            return pending

    async def submitted(self):
        await finish_owned(self._submitted())

    async def _submitted(self):
        async with self._lock:
            staged = {k: dict(v) for k, v in self.receipts.items()}
            changed = [r for r in staged.values() if r['status'] == 'included']
            if not changed:
                return
            for receipt in changed:
                receipt['status'] = 'submitted'
            # A request was transmitted even if persisting that observation fails.
            self.receipts = staged
            await asyncio.to_thread(self._save, staged)
            for receipt in changed:
                self._notice(receipt)

    async def close(self):
        await finish_owned(self._close())

    async def _close(self):
        async with self._lock:
            self.open = False
            staged = {k: dict(v) for k, v in self.receipts.items()}
            changed = [r for r in staged.values() if r['status'] in {'pending', 'included'}]
            if not changed:
                return
            for receipt in changed:
                receipt['status'] = 'retained'
            await asyncio.to_thread(self._save, staged)
            self.receipts = staged
            for receipt in changed:
                self._notice(receipt)
